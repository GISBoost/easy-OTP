"""Unit tests for easy_otp.core.gtfsrt_realizer (RT-3 pure helpers).

No QGIS, no network, no protobuf required (decode_snapshot test uses importorskip).
Run: py -m pytest easy_otp/test/test_build_realized_gtfs.py -v
"""

import csv
import io
import statistics
import sys
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# QGIS stub (same pattern as test_run_travel_time_matrix.py) — lets us import
# easy_otp.algorithms.build_realized_gtfs (for resolve_matching_mode) without a
# real QGIS install.
# ---------------------------------------------------------------------------

class _FakeQgsProcessingException(RuntimeError):
    pass


if "qgis" not in sys.modules:
    _qgis_core = MagicMock()
    _qgis_core.QgsProcessingException = _FakeQgsProcessingException
    sys.modules["qgis"] = MagicMock()
    sys.modules["qgis.core"] = _qgis_core
    sys.modules["qgis.PyQt"] = MagicMock()
    sys.modules["qgis.PyQt.QtCore"] = MagicMock()
    sys.modules["qgis.PyQt.QtWidgets"] = MagicMock()

from easy_otp.algorithms.build_realized_gtfs import resolve_matching_mode
from easy_otp.core.gtfsrt_realizer import (
    StaticIndex,
    aggregate_segments,
    check_snapshot_time_span,
    check_trip_overlap,
    collect_segment_times,
    decode_snapshot,
    deduplicate_snapshots,
    format_gtfs_time,
    load_static_index,
    load_static_indices,
    parse_gtfs_time,
    rebuild_stop_times,
    repackage_gtfs,
    sample_feed_capabilities,
    segment_key_for,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_gtfs_zip(
    tmp_path: Path,
    trip_rows: list[dict],
    stop_times_rows: list[dict],
    extra_files: dict[str, str] | None = None,
    name: str = "static.zip",
) -> str:
    zip_path = tmp_path / name
    trip_fields = ["trip_id", "route_id", "direction_id"]
    st_fields = ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"]

    with zipfile.ZipFile(zip_path, "w") as zf:
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=trip_fields)
        w.writeheader()
        w.writerows(trip_rows)
        zf.writestr("trips.txt", buf.getvalue())

        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=st_fields)
        w.writeheader()
        w.writerows(stop_times_rows)
        zf.writestr("stop_times.txt", buf.getvalue())

        if extra_files:
            for name, content in extra_files.items():
                zf.writestr(name, content)

    return str(zip_path)


def _read_zip_member(path: str, member: str) -> str:
    with zipfile.ZipFile(path) as zf:
        return zf.read(member).decode("utf-8")


def _make_static_index(
    trips: dict[str, tuple[str, str]],          # trip_id -> (route_id, direction_id)
    stops: dict[str, list[tuple]],               # trip_id -> [(seq, stop_id, arr, dep), ...]
    all_route_ids: set[str] | None = None,
    all_stop_ids: set[str] | None = None,
) -> StaticIndex:
    stop_map = {}
    for trip_id, stop_list in stops.items():
        for seq, stop_id, arr, dep in stop_list:
            stop_map[(trip_id, seq)] = (stop_id, arr, dep)
    if all_route_ids is None:
        all_route_ids = {route_id for route_id, _direction_id in trips.values()}
    if all_stop_ids is None:
        all_stop_ids = {stop_id for stop_list in stops.values() for _, stop_id, _, _ in stop_list}
    return StaticIndex(
        trip_route=trips,
        trip_stops={tid: sorted(sl, key=lambda x: x[0]) for tid, sl in stops.items()},
        stop_map=stop_map,
        all_trip_ids=set(trips.keys()),
        all_route_ids=all_route_ids,
        all_stop_ids=all_stop_ids,
    )


def _make_fake_feed(trip_id_to_stus: dict[str, list[dict]]) -> MagicMock:
    """Build a fake FeedMessage-like object without protobuf."""
    entities = []
    for trip_id, stu_list in trip_id_to_stus.items():
        tu = MagicMock()
        tu.trip.trip_id = trip_id
        tu.trip.schedule_relationship = 0  # SCHEDULED

        stus = []
        for stu_data in stu_list:
            stu = MagicMock()
            stu.stop_sequence = stu_data["seq"]
            stu.schedule_relationship = 0

            # departure event
            if "dep_delay" in stu_data or "dep_time" in stu_data:
                dep_ev = MagicMock()
                dep_ev.delay = stu_data.get("dep_delay", 0)
                dep_ev.time = stu_data.get("dep_time", 0)
                stu.HasField.side_effect = lambda f, _dep=dep_ev: f == "departure"
                stu.departure = dep_ev
                stu.arrival = MagicMock(time=0, delay=0)
            else:
                stu.HasField.side_effect = lambda f: False
                stu.departure = MagicMock(time=0, delay=0)
                stu.arrival = MagicMock(time=0, delay=0)

            stus.append(stu)

        tu.stop_time_update = stus
        entity = MagicMock()
        entity.HasField.side_effect = lambda f: f == "trip_update"
        entity.trip_update = tu
        entities.append(entity)

    feed = MagicMock()
    feed.entity = entities
    return feed


# ---------------------------------------------------------------------------
# parse_gtfs_time / format_gtfs_time
# ---------------------------------------------------------------------------

def test_parse_gtfs_time_normal():
    assert parse_gtfs_time("08:30:00") == 8 * 3600 + 30 * 60

def test_parse_gtfs_time_over_24():
    assert parse_gtfs_time("25:30:00") == 25 * 3600 + 30 * 60

def test_format_gtfs_time_normal():
    assert format_gtfs_time(8 * 3600 + 30 * 60) == "08:30:00"

def test_format_gtfs_time_over_24():
    assert format_gtfs_time(25 * 3600 + 30 * 60) == "25:30:00"

def test_parse_format_roundtrip():
    for s in ("00:00:00", "23:59:59", "24:00:00", "25:30:45", "28:15:00"):
        assert format_gtfs_time(parse_gtfs_time(s)) == s


# ---------------------------------------------------------------------------
# load_static_index
# ---------------------------------------------------------------------------

def test_load_static_index(tmp_path):
    zip_path = _make_gtfs_zip(
        tmp_path,
        trip_rows=[
            {"trip_id": "t1", "route_id": "R1", "direction_id": "0"},
            {"trip_id": "t2", "route_id": "R1", "direction_id": "1"},
        ],
        stop_times_rows=[
            {"trip_id": "t1", "arrival_time": "08:00:00", "departure_time": "08:00:00",
             "stop_id": "A", "stop_sequence": "1"},
            {"trip_id": "t1", "arrival_time": "08:10:00", "departure_time": "08:11:00",
             "stop_id": "B", "stop_sequence": "2"},
        ],
    )
    idx = load_static_index(zip_path)
    assert "t1" in idx.all_trip_ids
    assert "t2" in idx.all_trip_ids
    assert idx.trip_route["t1"] == ("R1", "0")
    assert len(idx.trip_stops["t1"]) == 2
    seq, stop_id, arr, dep = idx.trip_stops["t1"][1]
    assert seq == 2
    assert stop_id == "B"
    assert arr == parse_gtfs_time("08:10:00")
    assert dep == parse_gtfs_time("08:11:00")


# ---------------------------------------------------------------------------
# load_static_indices (RT3-2 — multi-file static merge)
# ---------------------------------------------------------------------------

def test_load_static_indices_single_path_matches_load_static_index(tmp_path):
    zip_path = _make_gtfs_zip(
        tmp_path,
        trip_rows=[{"trip_id": "t1", "route_id": "R1", "direction_id": "0"}],
        stop_times_rows=[
            {"trip_id": "t1", "arrival_time": "08:00:00", "departure_time": "08:00:00",
             "stop_id": "A", "stop_sequence": "1"},
        ],
    )
    direct = load_static_index(zip_path)
    merged, collision_count = load_static_indices([zip_path])
    assert collision_count == 0
    assert merged == direct


def test_load_static_indices_disjoint_trip_ids(tmp_path):
    zip_a = _make_gtfs_zip(
        tmp_path,
        trip_rows=[{"trip_id": "tram1", "route_id": "T1", "direction_id": "0"}],
        stop_times_rows=[
            {"trip_id": "tram1", "arrival_time": "08:00:00", "departure_time": "08:00:00",
             "stop_id": "A", "stop_sequence": "1"},
        ],
        name="tram.zip",
    )
    zip_b = _make_gtfs_zip(
        tmp_path,
        trip_rows=[{"trip_id": "bus1", "route_id": "B1", "direction_id": "0"}],
        stop_times_rows=[
            {"trip_id": "bus1", "arrival_time": "09:00:00", "departure_time": "09:00:00",
             "stop_id": "C", "stop_sequence": "1"},
        ],
        name="bus.zip",
    )
    merged, collision_count = load_static_indices([zip_a, zip_b])
    assert collision_count == 0
    assert merged.all_trip_ids == {"tram1", "bus1"}
    assert "tram1" in merged.trip_route and "bus1" in merged.trip_route
    assert ("tram1", 1) in merged.stop_map and ("bus1", 1) in merged.stop_map


def test_load_static_indices_collision_first_file_wins(tmp_path):
    zip_a = _make_gtfs_zip(
        tmp_path,
        trip_rows=[{"trip_id": "t1", "route_id": "R1", "direction_id": "0"}],
        stop_times_rows=[
            {"trip_id": "t1", "arrival_time": "08:00:00", "departure_time": "08:00:00",
             "stop_id": "A", "stop_sequence": "1"},
        ],
        name="first.zip",
    )
    zip_b = _make_gtfs_zip(
        tmp_path,
        trip_rows=[{"trip_id": "t1", "route_id": "R2", "direction_id": "1"}],
        stop_times_rows=[
            {"trip_id": "t1", "arrival_time": "10:00:00", "departure_time": "10:00:00",
             "stop_id": "Z", "stop_sequence": "1"},
        ],
        name="second.zip",
    )
    merged, collision_count = load_static_indices([zip_a, zip_b])
    assert collision_count == 1
    # first file's data wins — route R1, stop A, not R2/Z from the second file
    assert merged.trip_route["t1"] == ("R1", "0")
    assert merged.stop_map[("t1", 1)][0] == "A"


def test_load_static_indices_multiple_collisions_counted_once_each(tmp_path):
    zip_a = _make_gtfs_zip(
        tmp_path,
        trip_rows=[
            {"trip_id": "t1", "route_id": "R1", "direction_id": "0"},
            {"trip_id": "t2", "route_id": "R1", "direction_id": "0"},
        ],
        stop_times_rows=[
            {"trip_id": "t1", "arrival_time": "08:00:00", "departure_time": "08:00:00",
             "stop_id": "A", "stop_sequence": "1"},
            {"trip_id": "t2", "arrival_time": "08:05:00", "departure_time": "08:05:00",
             "stop_id": "A", "stop_sequence": "1"},
        ],
        name="first.zip",
    )
    zip_b = _make_gtfs_zip(
        tmp_path,
        trip_rows=[
            {"trip_id": "t1", "route_id": "R9", "direction_id": "1"},
            {"trip_id": "t2", "route_id": "R9", "direction_id": "1"},
            {"trip_id": "t3", "route_id": "R9", "direction_id": "1"},
        ],
        stop_times_rows=[
            {"trip_id": "t3", "arrival_time": "09:00:00", "departure_time": "09:00:00",
             "stop_id": "Z", "stop_sequence": "1"},
        ],
        name="second.zip",
    )
    merged, collision_count = load_static_indices([zip_a, zip_b])
    assert collision_count == 2  # t1, t2 collide; t3 is new
    assert merged.all_trip_ids == {"t1", "t2", "t3"}


def test_load_static_indices_same_trip_id_across_three_files_counts_once(tmp_path):
    # trip_id "t1" reappears in all three files — must be counted as ONE
    # collision, not once per extra reappearance.
    zip_a = _make_gtfs_zip(
        tmp_path,
        trip_rows=[{"trip_id": "t1", "route_id": "R1", "direction_id": "0"}],
        stop_times_rows=[
            {"trip_id": "t1", "arrival_time": "08:00:00", "departure_time": "08:00:00",
             "stop_id": "A", "stop_sequence": "1"},
        ],
        name="first.zip",
    )
    zip_b = _make_gtfs_zip(
        tmp_path,
        trip_rows=[{"trip_id": "t1", "route_id": "R2", "direction_id": "1"}],
        stop_times_rows=[
            {"trip_id": "t1", "arrival_time": "09:00:00", "departure_time": "09:00:00",
             "stop_id": "B", "stop_sequence": "1"},
        ],
        name="second.zip",
    )
    zip_c = _make_gtfs_zip(
        tmp_path,
        trip_rows=[{"trip_id": "t1", "route_id": "R3", "direction_id": "1"}],
        stop_times_rows=[
            {"trip_id": "t1", "arrival_time": "10:00:00", "departure_time": "10:00:00",
             "stop_id": "C", "stop_sequence": "1"},
        ],
        name="third.zip",
    )
    merged, collision_count = load_static_indices([zip_a, zip_b, zip_c])
    assert collision_count == 1
    assert merged.trip_route["t1"] == ("R1", "0")


def test_load_static_indices_bad_file_error_names_path(tmp_path):
    good_zip = _make_gtfs_zip(
        tmp_path,
        trip_rows=[{"trip_id": "t1", "route_id": "R1", "direction_id": "0"}],
        stop_times_rows=[
            {"trip_id": "t1", "arrival_time": "08:00:00", "departure_time": "08:00:00",
             "stop_id": "A", "stop_sequence": "1"},
        ],
        name="good.zip",
    )
    bad_zip = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad_zip, "w"):
        pass  # empty zip — missing trips.txt

    with pytest.raises(Exception) as excinfo:
        load_static_indices([good_zip, str(bad_zip)])
    assert str(bad_zip) in str(excinfo.value)


# ---------------------------------------------------------------------------
# aggregate_segments
# ---------------------------------------------------------------------------

def test_aggregate_p50_equals_median():
    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    key = ("R1", "0", "A", "B")
    p50, p85 = aggregate_segments({key: values})
    assert p50[key] == pytest.approx(statistics.median(values))

def test_aggregate_p85_geq_p50():
    values = [10.0, 10.0, 10.0, 10.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0]
    key = ("R1", "0", "A", "B")
    p50, p85 = aggregate_segments({key: values})
    assert p85[key] >= p50[key]

def test_aggregate_p85_value():
    # 20 values; P85 should be statistics.quantiles(values, n=100)[84]
    values = list(range(1, 21))  # [1, 2, …, 20]
    key = ("R1", "0", "A", "B")
    p50, p85 = aggregate_segments({key: values})
    expected_p85 = statistics.quantiles(values, n=100)[84]
    assert p85[key] == pytest.approx(expected_p85)

def test_aggregate_single_observation():
    key = ("R1", "0", "A", "B")
    p50, p85 = aggregate_segments({key: [42.0]})
    assert p50[key] == pytest.approx(42.0)
    assert p85[key] == pytest.approx(42.0)

def test_aggregate_invariant_p85_geq_p50_enforced():
    # Construct a case where raw quantile might equal median; verify invariant
    key = ("R1", "0", "A", "B")
    p50, p85 = aggregate_segments({key: [5.0, 5.0, 5.0]})
    assert p85[key] >= p50[key]


# ---------------------------------------------------------------------------
# rebuild_stop_times
# ---------------------------------------------------------------------------

def _simple_index() -> StaticIndex:
    # trip t1: A(dep=60) → B(arr=120, dep=180) → C(arr=240, dep=240)
    return _make_static_index(
        trips={"t1": ("R1", "0")},
        stops={"t1": [
            (1, "A", 0,   60),    # arr=0, dep=60
            (2, "B", 120, 180),   # arr=120, dep=180
            (3, "C", 240, 240),   # arr=240, dep=240
        ]},
    )


def test_rebuild_gap_fallback_uses_scheduled():
    idx = _simple_index()
    corrections, corrected, gaps = rebuild_stop_times(idx, {})
    assert corrected == 0
    assert gaps == 2  # two segments (A→B, B→C)
    # First stop unchanged
    assert corrections[("t1", 1)] == (0, 60)
    # A→B scheduled travel = 120 - 60 = 60; dwell at B = 180 - 120 = 60
    # running_time starts at 60 (dep[A])
    # new_arr[B] = 60 + 60 = 120; new_dep[B] = 120 + 60 = 180
    assert corrections[("t1", 2)] == (120, 180)
    # B→C scheduled travel = 240 - 180 = 60; dwell at C = 0
    # new_arr[C] = 180 + 60 = 240; new_dep[C] = 240
    assert corrections[("t1", 3)] == (240, 240)


def test_rebuild_corrected_segment():
    idx = _simple_index()
    # Observed A→B travel = 90s (30s longer than scheduled 60s)
    stats = {("R1", "0", "A", "B"): 90.0}
    corrections, corrected, gaps = rebuild_stop_times(idx, stats)
    assert corrected == 1
    assert gaps == 1  # B→C still a gap
    # new_arr[B] = 60 + 90 = 150; dwell = 60; new_dep[B] = 210
    assert corrections[("t1", 2)] == (150, 210)
    # B→C: travel = 240 - 180 = 60 (scheduled); but running_time is now 210
    # new_arr[C] = 210 + 60 = 270; dwell = 0; new_dep[C] = 270
    assert corrections[("t1", 3)] == (270, 270)


def test_rebuild_monotonic_clamp():
    idx = _simple_index()
    # Inject a negative segment stat — clamp must prevent non-monotonic output
    stats = {("R1", "0", "A", "B"): -999.0}
    corrections, corrected, gaps = rebuild_stop_times(idx, stats)
    # new_arr[B] = max(60 + (-999), 60) = 60
    arr_b, dep_b = corrections[("t1", 2)]
    arr_a, dep_a = corrections[("t1", 1)]
    assert arr_b >= dep_a  # arrival at B >= departure from A
    assert dep_b >= arr_b  # departure from B >= arrival at B


def test_rebuild_gtfs_time_over_24():
    # Trip spanning midnight: A departs at 23:55:00, B arrives at 24:05:00
    idx = _make_static_index(
        trips={"t_night": ("R2", "0")},
        stops={"t_night": [
            (1, "X", parse_gtfs_time("23:50:00"), parse_gtfs_time("23:55:00")),
            (2, "Y", parse_gtfs_time("24:05:00"), parse_gtfs_time("24:05:00")),
        ]},
    )
    # scheduled travel = 24:05 - 23:55 = 600s; use same in stats
    stats = {("R2", "0", "X", "Y"): 600.0}
    corrections, corrected, gaps = rebuild_stop_times(idx, stats)
    arr_y, dep_y = corrections[("t_night", 2)]
    # running_time = dep_x = parse("23:55:00") = 86100
    # new_arr_y = 86100 + 600 = 86700 = 24:05:00
    assert arr_y == parse_gtfs_time("24:05:00")
    assert format_gtfs_time(arr_y) == "24:05:00"


def test_rebuild_drop_trip():
    idx = _simple_index()
    corrections, corrected, gaps = rebuild_stop_times(idx, {}, drop_trip_ids=frozenset({"t1"}))
    assert corrections == {}


# ---------------------------------------------------------------------------
# repackage_gtfs
# ---------------------------------------------------------------------------

def test_repackage_preserves_extra_members(tmp_path):
    src_path = _make_gtfs_zip(
        tmp_path,
        trip_rows=[{"trip_id": "t1", "route_id": "R1", "direction_id": "0"}],
        stop_times_rows=[
            {"trip_id": "t1", "arrival_time": "08:00:00", "departure_time": "08:00:00",
             "stop_id": "A", "stop_sequence": "1"},
        ],
        extra_files={"agency.txt": "agency_id,agency_name\n1,TestAgency\n",
                     "feed_info.txt": "feed_publisher_name\nTestFeed\n"},
    )
    out_path = str(tmp_path / "out.zip")
    repackage_gtfs(src_path, out_path, {})

    with zipfile.ZipFile(out_path) as zf:
        names = zf.namelist()
    assert "agency.txt" in names
    assert "feed_info.txt" in names


def test_repackage_new_stop_times(tmp_path):
    src_path = _make_gtfs_zip(
        tmp_path,
        trip_rows=[{"trip_id": "t1", "route_id": "R1", "direction_id": "0"}],
        stop_times_rows=[
            {"trip_id": "t1", "arrival_time": "08:00:00", "departure_time": "08:00:00",
             "stop_id": "A", "stop_sequence": "1"},
            {"trip_id": "t1", "arrival_time": "08:10:00", "departure_time": "08:11:00",
             "stop_id": "B", "stop_sequence": "2"},
        ],
    )
    # Correct stop 2: shift arrival by +5 minutes
    corrections = {("t1", 2): (parse_gtfs_time("08:15:00"), parse_gtfs_time("08:16:00"))}
    out_path = str(tmp_path / "out.zip")
    repackage_gtfs(src_path, out_path, corrections)

    content = _read_zip_member(out_path, "stop_times.txt")
    assert "08:15:00" in content
    assert "08:16:00" in content
    assert "08:10:00" not in content  # original replaced


def test_repackage_drop_trip_from_trips_txt(tmp_path):
    src_path = _make_gtfs_zip(
        tmp_path,
        trip_rows=[
            {"trip_id": "t1", "route_id": "R1", "direction_id": "0"},
            {"trip_id": "t2", "route_id": "R1", "direction_id": "1"},
        ],
        stop_times_rows=[
            {"trip_id": "t1", "arrival_time": "08:00:00", "departure_time": "08:00:00",
             "stop_id": "A", "stop_sequence": "1"},
            {"trip_id": "t2", "arrival_time": "09:00:00", "departure_time": "09:00:00",
             "stop_id": "A", "stop_sequence": "1"},
        ],
    )
    out_path = str(tmp_path / "out.zip")
    repackage_gtfs(src_path, out_path, {}, drop_trip_ids={"t2"})

    trips_content = _read_zip_member(out_path, "trips.txt")
    stop_times_content = _read_zip_member(out_path, "stop_times.txt")
    assert "t1" in trips_content
    assert "t2" not in trips_content
    assert "t2" not in stop_times_content


def test_repackage_preserves_extra_stop_times_columns(tmp_path):
    # Non-standard columns (pickup_type, timepoint) must be preserved unchanged
    trip_rows = [{"trip_id": "t1", "route_id": "R1", "direction_id": "0"}]
    fields = ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence",
              "pickup_type", "drop_off_type"]
    st_rows = [
        {"trip_id": "t1", "arrival_time": "08:00:00", "departure_time": "08:00:00",
         "stop_id": "A", "stop_sequence": "1", "pickup_type": "0", "drop_off_type": "1"},
    ]

    zip_path = tmp_path / "static.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=["trip_id", "route_id", "direction_id"])
        w.writeheader(); w.writerows(trip_rows)
        zf.writestr("trips.txt", buf.getvalue())

        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=fields)
        w.writeheader(); w.writerows(st_rows)
        zf.writestr("stop_times.txt", buf.getvalue())

    out_path = str(tmp_path / "out.zip")
    repackage_gtfs(str(zip_path), out_path, {})

    content = _read_zip_member(out_path, "stop_times.txt")
    assert "pickup_type" in content
    assert "drop_off_type" in content
    assert ",1\r\n" in content or ",1\n" in content  # drop_off_type=1 preserved


# ---------------------------------------------------------------------------
# check_trip_overlap (mocked — no protobuf required)
# ---------------------------------------------------------------------------

def _fake_snapshot(tmp_path: Path, name: str = "snapshot_20260621-080000.pb") -> Path:
    p = tmp_path / name
    p.write_bytes(b"fake")
    return p


def test_check_overlap_high(tmp_path):
    snap = _fake_snapshot(tmp_path)
    idx = _make_static_index(
        trips={"trip1": ("R1", "0"), "trip2": ("R1", "1")},
        stops={},
    )
    mock_entity = MagicMock()
    mock_entity.HasField.side_effect = lambda f: f == "trip_update"
    mock_entity.trip_update.trip.trip_id = "trip1"
    mock_feed = MagicMock()
    mock_feed.entity = [mock_entity]

    with patch("easy_otp.core.gtfsrt_realizer.decode_snapshot", return_value=mock_feed):
        overlap = check_trip_overlap([snap], idx)

    assert overlap == pytest.approx(1.0)


def test_check_overlap_low(tmp_path):
    snap = _fake_snapshot(tmp_path)
    idx = _make_static_index(
        trips={"other_trip": ("R1", "0")},
        stops={},
    )
    mock_entity = MagicMock()
    mock_entity.HasField.side_effect = lambda f: f == "trip_update"
    mock_entity.trip_update.trip.trip_id = "trip_not_in_static"
    mock_feed = MagicMock()
    mock_feed.entity = [mock_entity]

    with patch("easy_otp.core.gtfsrt_realizer.decode_snapshot", return_value=mock_feed):
        overlap = check_trip_overlap([snap], idx)

    assert overlap == pytest.approx(0.0)


def test_check_overlap_unreadable_snapshot(tmp_path):
    snap = _fake_snapshot(tmp_path)
    idx = _make_static_index(trips={}, stops={})

    with patch(
        "easy_otp.core.gtfsrt_realizer.decode_snapshot",
        side_effect=Exception("corrupt"),
    ):
        overlap = check_trip_overlap([snap], idx)

    assert overlap == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# check_snapshot_time_span
# ---------------------------------------------------------------------------

def test_snapshot_time_span_known_duration():
    paths = [
        Path("snapshot_20260621-060000.pb"),
        Path("snapshot_20260621-080000.pb"),
        Path("snapshot_20260621-220000.pb"),
    ]
    assert check_snapshot_time_span(paths) == pytest.approx(16 * 3600)


def test_snapshot_time_span_single_path():
    paths = [Path("snapshot_20260621-060000.pb")]
    assert check_snapshot_time_span(paths) == pytest.approx(0.0)


def test_snapshot_time_span_empty_list():
    assert check_snapshot_time_span([]) == pytest.approx(0.0)


def test_snapshot_time_span_skips_malformed_filename():
    paths = [
        Path("snapshot_20260621-060000.pb"),
        Path("not_a_snapshot.pb"),
        Path("snapshot_20260622-070000.pb"),
    ]
    expected = 24 * 3600 + 3600  # 25h between the two well-formed timestamps
    assert check_snapshot_time_span(paths) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# collect_segment_times (mocked — no protobuf required)
# ---------------------------------------------------------------------------

def test_collect_uses_delay_offsets(tmp_path):
    snap = _fake_snapshot(tmp_path)
    idx = _make_static_index(
        trips={"t1": ("R1", "0")},
        stops={"t1": [
            (1, "A", 0, 3600),    # dep from A at 3600s (01:00:00)
            (2, "B", 4200, 4200), # arr at B at 4200s (01:10:00); scheduled travel=600s
        ]},
    )
    # Build a mock feed: one TripUpdate with two StopTimeUpdates
    # dep at A: delay=60 → observed dep = 3600+60 = 3660
    # arr at B: delay=120 → observed arr = 4200+120 = 4320
    # observed seg time = 4320 - 3660 = 660s

    stu_from = MagicMock()
    stu_from.stop_sequence = 1
    stu_from.schedule_relationship = 0
    dep_ev = MagicMock(time=0, delay=60)
    stu_from.HasField.side_effect = lambda f: f == "departure"
    stu_from.departure = dep_ev
    stu_from.arrival = MagicMock(time=0, delay=0)

    stu_to = MagicMock()
    stu_to.stop_sequence = 2
    stu_to.schedule_relationship = 0
    arr_ev = MagicMock(time=0, delay=120)
    stu_to.HasField.side_effect = lambda f: f == "arrival"
    stu_to.arrival = arr_ev
    stu_to.departure = MagicMock(time=0, delay=0)

    tu = MagicMock()
    tu.trip.trip_id = "t1"
    tu.trip.schedule_relationship = 0
    tu.stop_time_update = [stu_from, stu_to]

    entity = MagicMock()
    entity.HasField.side_effect = lambda f: f == "trip_update"
    entity.trip_update = tu

    feed = MagicMock()
    feed.entity = [entity]

    with patch("easy_otp.core.gtfsrt_realizer.decode_snapshot", return_value=feed):
        seg_times, canceled, skipped = collect_segment_times([snap], idx)

    assert canceled == set()
    assert skipped == 0  # TRIP_ID mode never increments the fallback counter
    key = ("R1", "0", "A", "B")
    assert key in seg_times
    assert seg_times[key] == pytest.approx([660.0])


def test_collect_canceled_trip(tmp_path):
    snap = _fake_snapshot(tmp_path)
    idx = _make_static_index(trips={"t1": ("R1", "0")}, stops={})

    tu = MagicMock()
    tu.trip.trip_id = "t1"
    tu.trip.schedule_relationship = 3  # CANCELED
    tu.stop_time_update = []

    entity = MagicMock()
    entity.HasField.side_effect = lambda f: f == "trip_update"
    entity.trip_update = tu

    feed = MagicMock()
    feed.entity = [entity]

    with patch("easy_otp.core.gtfsrt_realizer.decode_snapshot", return_value=feed):
        seg_times, canceled, _ = collect_segment_times([snap], idx, canceled_policy="skip")

    assert "t1" in canceled
    assert seg_times == {}


def test_collect_skips_trip_not_in_static(tmp_path):
    snap = _fake_snapshot(tmp_path)
    idx = _make_static_index(trips={}, stops={})  # empty static

    tu = MagicMock()
    tu.trip.trip_id = "unknown_trip"
    tu.trip.schedule_relationship = 0
    tu.stop_time_update = []

    entity = MagicMock()
    entity.HasField.side_effect = lambda f: f == "trip_update"
    entity.trip_update = tu

    feed = MagicMock()
    feed.entity = [entity]

    with patch("easy_otp.core.gtfsrt_realizer.decode_snapshot", return_value=feed):
        seg_times, canceled, _ = collect_segment_times([snap], idx)

    assert seg_times == {}


def test_collect_rejects_nonpositive_segment_time(tmp_path):
    snap = _fake_snapshot(tmp_path)
    idx = _make_static_index(
        trips={"t1": ("R1", "0")},
        stops={"t1": [
            (1, "A", 0, 3600),
            (2, "B", 3000, 3000),  # arr < dep of previous → scheduled travel = -600s
        ]},
    )
    # Both STUs have delay=0 → seg_time = 3000 - 3600 = -600s → must be rejected
    stu_from = MagicMock()
    stu_from.stop_sequence = 1
    stu_from.schedule_relationship = 0
    stu_from.HasField.side_effect = lambda f: f == "departure"
    stu_from.departure = MagicMock(time=0, delay=0)
    stu_from.arrival = MagicMock(time=0, delay=0)

    stu_to = MagicMock()
    stu_to.stop_sequence = 2
    stu_to.schedule_relationship = 0
    stu_to.HasField.side_effect = lambda f: f == "arrival"
    stu_to.arrival = MagicMock(time=0, delay=0)
    stu_to.departure = MagicMock(time=0, delay=0)

    tu = MagicMock()
    tu.trip.trip_id = "t1"
    tu.trip.schedule_relationship = 0
    tu.stop_time_update = [stu_from, stu_to]

    entity = MagicMock()
    entity.HasField.side_effect = lambda f: f == "trip_update"
    entity.trip_update = tu

    feed = MagicMock()
    feed.entity = [entity]

    with patch("easy_otp.core.gtfsrt_realizer.decode_snapshot", return_value=feed):
        seg_times, _, _ = collect_segment_times([snap], idx)

    assert seg_times == {}


# ---------------------------------------------------------------------------
# collect_segment_times — reconcile_last_snapshot (RT3-4)
# ---------------------------------------------------------------------------

def _make_trip_entity(trip_id, dep_delay, arr_delay, from_seq=1, to_seq=2):
    """Build a mock entity for one trip with a single from/to StopTimeUpdate pair."""
    stu_from = MagicMock()
    stu_from.stop_sequence = from_seq
    stu_from.schedule_relationship = 0
    stu_from.HasField.side_effect = lambda f: f == "departure"
    stu_from.departure = MagicMock(time=0, delay=dep_delay)
    stu_from.arrival = MagicMock(time=0, delay=0)

    stu_to = MagicMock()
    stu_to.stop_sequence = to_seq
    stu_to.schedule_relationship = 0
    stu_to.HasField.side_effect = lambda f: f == "arrival"
    stu_to.arrival = MagicMock(time=0, delay=arr_delay)
    stu_to.departure = MagicMock(time=0, delay=0)

    tu = MagicMock()
    tu.trip.trip_id = trip_id
    tu.trip.schedule_relationship = 0
    tu.stop_time_update = [stu_from, stu_to]

    entity = MagicMock()
    entity.HasField.side_effect = lambda f: f == "trip_update"
    entity.trip_update = tu
    return entity


def _make_feed(entities):
    feed = MagicMock()
    feed.entity = entities
    return feed


def test_collect_reconcile_keeps_last_snapshot_value(tmp_path):
    # Scheduled travel A->B is 600s (dep at 3600, arr at 4200); three snapshots
    # of the same trip/segment carry different arrival delays -> distinct seg_times.
    idx = _make_static_index(
        trips={"t1": ("R1", "0")},
        stops={"t1": [(1, "A", 0, 3600), (2, "B", 4200, 4200)]},
    )
    snaps = [
        _fake_snapshot(tmp_path, "snapshot_20260621-080000.pb"),
        _fake_snapshot(tmp_path, "snapshot_20260621-080500.pb"),
        _fake_snapshot(tmp_path, "snapshot_20260621-081000.pb"),
    ]
    feeds = [
        _make_feed([_make_trip_entity("t1", dep_delay=0, arr_delay=60)]),   # 660s
        _make_feed([_make_trip_entity("t1", dep_delay=0, arr_delay=120)]),  # 720s
        _make_feed([_make_trip_entity("t1", dep_delay=0, arr_delay=300)]),  # 900s
    ]

    with patch("easy_otp.core.gtfsrt_realizer.decode_snapshot", side_effect=feeds):
        seg_times, _, _ = collect_segment_times(snaps, idx, reconcile_last_snapshot=True)

    key = ("R1", "0", "A", "B")
    assert seg_times[key] == pytest.approx([900.0])


def test_collect_reconcile_false_keeps_all_snapshots(tmp_path):
    idx = _make_static_index(
        trips={"t1": ("R1", "0")},
        stops={"t1": [(1, "A", 0, 3600), (2, "B", 4200, 4200)]},
    )
    snaps = [
        _fake_snapshot(tmp_path, "snapshot_20260621-080000.pb"),
        _fake_snapshot(tmp_path, "snapshot_20260621-080500.pb"),
        _fake_snapshot(tmp_path, "snapshot_20260621-081000.pb"),
    ]
    feeds = [
        _make_feed([_make_trip_entity("t1", dep_delay=0, arr_delay=60)]),
        _make_feed([_make_trip_entity("t1", dep_delay=0, arr_delay=120)]),
        _make_feed([_make_trip_entity("t1", dep_delay=0, arr_delay=300)]),
    ]

    with patch("easy_otp.core.gtfsrt_realizer.decode_snapshot", side_effect=feeds):
        seg_times, _, _ = collect_segment_times(snaps, idx, reconcile_last_snapshot=False)

    key = ("R1", "0", "A", "B")
    # Pins today's (pre-0.7) behavior explicitly so it can't silently drift.
    assert seg_times[key] == pytest.approx([660.0, 720.0, 900.0])


def test_collect_reconcile_scoped_per_trip_id(tmp_path):
    # Two distinct trips sharing the same route/direction/stops (-> same SegmentKey).
    snap = _fake_snapshot(tmp_path)
    idx = _make_static_index(
        trips={"t1": ("R1", "0"), "t2": ("R1", "0")},
        stops={
            "t1": [(1, "A", 0, 3600), (2, "B", 4200, 4200)],
            "t2": [(1, "A", 0, 3600), (2, "B", 4200, 4200)],
        },
    )
    feed = _make_feed([
        _make_trip_entity("t1", dep_delay=0, arr_delay=60),
        _make_trip_entity("t2", dep_delay=0, arr_delay=120),
    ])

    with patch("easy_otp.core.gtfsrt_realizer.decode_snapshot", return_value=feed):
        seg_times, _, _ = collect_segment_times([snap], idx, reconcile_last_snapshot=True)

    key = ("R1", "0", "A", "B")
    # One reconciled observation per trip_id, not collapsed across trips.
    assert seg_times[key] == pytest.approx([660.0, 720.0])


def test_collect_reconcile_invalid_latest_does_not_evict_prior(tmp_path):
    idx = _make_static_index(
        trips={"t1": ("R1", "0")},
        stops={"t1": [(1, "A", 0, 3600), (2, "B", 4200, 4200)]},
    )
    snaps = [
        _fake_snapshot(tmp_path, "snapshot_20260621-080000.pb"),
        _fake_snapshot(tmp_path, "snapshot_20260621-080500.pb"),
    ]
    feeds = [
        # Snapshot 1 (earlier): valid observation, seg_time = 660s.
        _make_feed([_make_trip_entity("t1", dep_delay=0, arr_delay=60)]),
        # Snapshot 2 (later, chronologically last): implausible negative seg_time
        # (4200+0) - (3600+1000) = -400s -> rejected by the existing filter, so it
        # must never reach latest_per_trip_segment and cannot evict snapshot 1's value.
        _make_feed([_make_trip_entity("t1", dep_delay=1000, arr_delay=0)]),
    ]

    with patch("easy_otp.core.gtfsrt_realizer.decode_snapshot", side_effect=feeds):
        seg_times, _, _ = collect_segment_times(snaps, idx, reconcile_last_snapshot=True)

    key = ("R1", "0", "A", "B")
    assert seg_times[key] == pytest.approx([660.0])


def test_collect_reconcile_aggregate_integration(tmp_path):
    # Reconciled output (one observation per trip_id) still satisfies p85 >= p50
    # once piped into the untouched aggregate_segments.
    snap = _fake_snapshot(tmp_path)
    idx = _make_static_index(
        trips={"t1": ("R1", "0"), "t2": ("R1", "0"), "t3": ("R1", "0")},
        stops={
            "t1": [(1, "A", 0, 3600), (2, "B", 4200, 4200)],
            "t2": [(1, "A", 0, 3600), (2, "B", 4200, 4200)],
            "t3": [(1, "A", 0, 3600), (2, "B", 4200, 4200)],
        },
    )
    feed = _make_feed([
        _make_trip_entity("t1", dep_delay=0, arr_delay=60),
        _make_trip_entity("t2", dep_delay=0, arr_delay=120),
        _make_trip_entity("t3", dep_delay=0, arr_delay=300),
    ])

    with patch("easy_otp.core.gtfsrt_realizer.decode_snapshot", return_value=feed):
        seg_times, _, _ = collect_segment_times([snap], idx, reconcile_last_snapshot=True)

    p50, p85 = aggregate_segments(seg_times)
    key = ("R1", "0", "A", "B")
    assert p85[key] >= p50[key]


# ---------------------------------------------------------------------------
# decode_snapshot (requires google.transit — skipped if absent)
# ---------------------------------------------------------------------------

def test_decode_snapshot_roundtrip():
    pytest.importorskip("google.transit")
    from google.transit import gtfs_realtime_pb2

    fm = gtfs_realtime_pb2.FeedMessage()
    fm.header.gtfs_realtime_version = "2.0"
    fm.header.timestamp = 1_750_000_000
    data = fm.SerializeToString()

    decoded = decode_snapshot(data)
    assert decoded.header.gtfs_realtime_version == "2.0"
    assert decoded.header.timestamp == 1_750_000_000


# ---------------------------------------------------------------------------
# deduplicate_snapshots
# ---------------------------------------------------------------------------

def _write_snapshots(tmp_path: Path, contents: list[bytes]) -> list[Path]:
    paths = []
    for i, content in enumerate(contents):
        p = tmp_path / f"snapshot_20260621-{i:06d}.pb"
        p.write_bytes(content)
        paths.append(p)
    return paths


def test_deduplicate_empty_list():
    kept, dropped = deduplicate_snapshots([])
    assert kept == []
    assert dropped == 0


def test_deduplicate_single_snapshot(tmp_path):
    paths = _write_snapshots(tmp_path, [b"A"])
    kept, dropped = deduplicate_snapshots(paths)
    assert kept == paths
    assert dropped == 0


def test_deduplicate_keeps_all_unique(tmp_path):
    paths = _write_snapshots(tmp_path, [b"AAAA", b"BBBB", b"CCCC"])
    kept, dropped = deduplicate_snapshots(paths)
    assert kept == paths
    assert dropped == 0


def test_deduplicate_collapses_run(tmp_path):
    paths = _write_snapshots(tmp_path, [b"A", b"A", b"A", b"B", b"B"])
    kept, dropped = deduplicate_snapshots(paths)
    assert kept == [paths[0], paths[3]]
    assert dropped == 3


def test_deduplicate_only_compares_to_last_kept(tmp_path):
    """Non-adjacent repeats are never collapsed — only consecutive runs are."""
    paths = _write_snapshots(tmp_path, [b"A", b"B", b"A"])
    kept, dropped = deduplicate_snapshots(paths)
    assert kept == paths
    assert dropped == 0


def test_deduplicate_unreadable_snapshot_kept(tmp_path):
    paths = _write_snapshots(tmp_path, [b"A", b"B"])
    missing = tmp_path / "snapshot_20260621-999999.pb"  # never written — unreadable
    all_paths = [paths[0], missing, paths[1]]

    kept, dropped = deduplicate_snapshots(all_paths)

    assert missing in kept
    assert dropped == 0


def test_deduplicate_across_unreadable_gap(tmp_path):
    """An unreadable snapshot must not reset the comparison against the last kept hash."""
    paths = _write_snapshots(tmp_path, [b"A", b"A"])
    missing = tmp_path / "snapshot_20260621-999999.pb"  # never written — unreadable
    all_paths = [paths[0], missing, paths[1]]

    kept, dropped = deduplicate_snapshots(all_paths)

    assert kept == [paths[0], missing]
    assert dropped == 1


# ---------------------------------------------------------------------------
# segment_key_for (RT3-5)
# ---------------------------------------------------------------------------

def test_segment_key_for_trip_id_mode_unchanged():
    assert segment_key_for("R1", "0", "A", "B", "TRIP_ID") == ("R1", "0", "A", "B")


def test_segment_key_for_fallback_zeroes_direction():
    assert segment_key_for("R1", "1", "A", "B", "ROUTE_STOP_FALLBACK") == ("R1", "", "A", "B")


def test_segment_key_for_unresolved_mode_raises():
    with pytest.raises(ValueError):
        segment_key_for("R1", "0", "A", "B", "AUTO")


# ---------------------------------------------------------------------------
# load_static_index / load_static_indices — all_route_ids / all_stop_ids (RT3-5)
# ---------------------------------------------------------------------------

def test_load_static_index_populates_route_and_stop_ids(tmp_path):
    zip_path = _make_gtfs_zip(
        tmp_path,
        trip_rows=[{"trip_id": "t1", "route_id": "R1", "direction_id": "0"}],
        stop_times_rows=[
            {"trip_id": "t1", "arrival_time": "08:00:00", "departure_time": "08:00:00",
             "stop_id": "A", "stop_sequence": "1"},
        ],
        extra_files={
            "routes.txt": "route_id,route_short_name\nR1,1\nR2,2\n",
            "stops.txt": "stop_id,stop_name\nA,Stop A\nB,Stop B\n",
        },
    )
    idx = load_static_index(zip_path)
    assert idx.all_route_ids == {"R1", "R2"}
    assert idx.all_stop_ids == {"A", "B"}


def test_load_static_index_missing_routes_stops_files_yields_empty_sets(tmp_path):
    zip_path = _make_gtfs_zip(
        tmp_path,
        trip_rows=[{"trip_id": "t1", "route_id": "R1", "direction_id": "0"}],
        stop_times_rows=[
            {"trip_id": "t1", "arrival_time": "08:00:00", "departure_time": "08:00:00",
             "stop_id": "A", "stop_sequence": "1"},
        ],
    )
    idx = load_static_index(zip_path)
    assert idx.all_route_ids == set()
    assert idx.all_stop_ids == set()


def test_load_static_indices_unions_route_and_stop_ids_even_across_collision(tmp_path):
    # t1 collides (first-file-wins for trip data), but all_route_ids/all_stop_ids
    # must still union across BOTH files regardless of the collision — a deliberate
    # deviation from the trip-level merge policy (see load_static_indices docstring).
    zip_a = _make_gtfs_zip(
        tmp_path,
        trip_rows=[{"trip_id": "t1", "route_id": "R1", "direction_id": "0"}],
        stop_times_rows=[
            {"trip_id": "t1", "arrival_time": "08:00:00", "departure_time": "08:00:00",
             "stop_id": "A", "stop_sequence": "1"},
        ],
        extra_files={"routes.txt": "route_id\nR1\n", "stops.txt": "stop_id\nA\n"},
        name="first.zip",
    )
    zip_b = _make_gtfs_zip(
        tmp_path,
        trip_rows=[{"trip_id": "t1", "route_id": "R2", "direction_id": "1"}],
        stop_times_rows=[
            {"trip_id": "t1", "arrival_time": "10:00:00", "departure_time": "10:00:00",
             "stop_id": "Z", "stop_sequence": "1"},
        ],
        extra_files={"routes.txt": "route_id\nR9\n", "stops.txt": "stop_id\nZ\n"},
        name="second.zip",
    )
    merged, collision_count = load_static_indices([zip_a, zip_b])
    assert collision_count == 1
    # First file's trip data wins (route R1), but both files' route/stop ids are unioned.
    assert merged.trip_route["t1"] == ("R1", "0")
    assert merged.all_route_ids == {"R1", "R9"}
    assert merged.all_stop_ids == {"A", "Z"}


# ---------------------------------------------------------------------------
# collect_segment_times / rebuild_stop_times — ROUTE_STOP_FALLBACK mode (RT3-5)
# ---------------------------------------------------------------------------

def _make_fallback_trip_entity(
    trip_id, route_id, from_stop_id, to_stop_id, dep_abs, arr_abs, from_seq=1, to_seq=2,
):
    """Build a mock entity for ROUTE_STOP_FALLBACK tests.

    Unlike _make_trip_entity, this sets route_id and stop_id as real strings directly
    on the mocks, and absolute event times rather than only delay — MagicMock
    auto-vivifies any unset attribute as a truthy mock object, not "", so a fallback
    test that skips setting these would silently produce zero segments for the wrong
    reason (a mismatch, not a genuine skip) while still "passing" if it only checked
    for the absence of an exception.
    """
    stu_from = MagicMock()
    stu_from.stop_sequence = from_seq
    stu_from.schedule_relationship = 0
    stu_from.stop_id = from_stop_id
    stu_from.HasField.side_effect = lambda f: f == "departure"
    stu_from.departure = MagicMock(time=dep_abs, delay=0)
    stu_from.arrival = MagicMock(time=0, delay=0)

    stu_to = MagicMock()
    stu_to.stop_sequence = to_seq
    stu_to.schedule_relationship = 0
    stu_to.stop_id = to_stop_id
    stu_to.HasField.side_effect = lambda f: f == "arrival"
    stu_to.arrival = MagicMock(time=arr_abs, delay=0)
    stu_to.departure = MagicMock(time=0, delay=0)

    tu = MagicMock()
    tu.trip.trip_id = trip_id
    tu.trip.route_id = route_id
    tu.trip.schedule_relationship = 0
    tu.stop_time_update = [stu_from, stu_to]

    entity = MagicMock()
    entity.HasField.side_effect = lambda f: f == "trip_update"
    entity.trip_update = tu
    return entity


def test_collect_fallback_produces_segments_where_trip_id_mode_yields_none(tmp_path):
    # Static feed knows about route R1 and stops A/B, but the RT trip_id is entirely
    # absent from the static feed — the permanent disjoint-namespace scenario
    # (Poznan/Krakow). Unlike real Poznan's single-StopTimeUpdate feed shape (#18,
    # RT3-6), this synthetic entity carries two StopTimeUpdates, so a segment CAN be
    # computed once route/stop matching succeeds — this is the core regression this
    # milestone fixes.
    snap = _fake_snapshot(tmp_path)
    idx = _make_static_index(
        trips={}, stops={}, all_route_ids={"R1"}, all_stop_ids={"A", "B"},
    )
    entity = _make_fallback_trip_entity(
        "rt_only_trip_9", route_id="R1", from_stop_id="A", to_stop_id="B",
        dep_abs=1000, arr_abs=1090,
    )
    feed = _make_feed([entity])

    with patch("easy_otp.core.gtfsrt_realizer.decode_snapshot", return_value=feed):
        trip_id_segments, _, _ = collect_segment_times([snap], idx, matching_mode="TRIP_ID")
    with patch("easy_otp.core.gtfsrt_realizer.decode_snapshot", return_value=feed):
        fallback_segments, _, skipped = collect_segment_times(
            [snap], idx, matching_mode="ROUTE_STOP_FALLBACK"
        )

    assert trip_id_segments == {}
    assert skipped == 0
    key = ("R1", "", "A", "B")
    assert fallback_segments[key] == pytest.approx([90.0])


def test_collect_fallback_skips_and_counts_missing_absolute_time(tmp_path):
    snap = _fake_snapshot(tmp_path)
    idx = _make_static_index(
        trips={}, stops={}, all_route_ids={"R1"}, all_stop_ids={"A", "B"},
    )
    entity = _make_fallback_trip_entity(
        "rt_only_trip_9", route_id="R1", from_stop_id="A", to_stop_id="B",
        dep_abs=0, arr_abs=0,  # no absolute time available — must be skipped, not crash
    )
    feed = _make_feed([entity])

    with patch("easy_otp.core.gtfsrt_realizer.decode_snapshot", return_value=feed):
        seg_times, _, skipped = collect_segment_times(
            [snap], idx, matching_mode="ROUTE_STOP_FALLBACK"
        )

    assert seg_times == {}
    assert skipped == 1


def test_rebuild_fallback_mode_round_trip(tmp_path):
    # Static feed's own trip (walked by rebuild_stop_times) shares the same
    # route/stops as the RT-side fallback entity below, even though the RT trip_id
    # itself ("rt_only_trip_9") matches nothing in trip_route/stop_map.
    snap = _fake_snapshot(tmp_path)
    idx = _make_static_index(
        trips={"static_trip_1": ("R1", "0")},
        stops={"static_trip_1": [(1, "A", 0, 60), (2, "B", 150, 150)]},
        all_route_ids={"R1"},
        all_stop_ids={"A", "B"},
    )
    entity = _make_fallback_trip_entity(
        "rt_only_trip_9", route_id="R1", from_stop_id="A", to_stop_id="B",
        dep_abs=1000, arr_abs=1100,
    )
    feed = _make_feed([entity])

    with patch("easy_otp.core.gtfsrt_realizer.decode_snapshot", return_value=feed):
        seg_times, _, skipped = collect_segment_times(
            [snap], idx, matching_mode="ROUTE_STOP_FALLBACK"
        )
    assert skipped == 0
    p50_stats, _ = aggregate_segments(seg_times)

    corrections, corrected, gaps = rebuild_stop_times(
        idx, p50_stats, matching_mode="ROUTE_STOP_FALLBACK"
    )
    assert corrected == 1
    assert gaps == 0
    # running_time starts at dep[A]=60; observed travel = 1100-1000=100
    arr_b, dep_b = corrections[("static_trip_1", 2)]
    assert arr_b == 160


def test_rebuild_fallback_mode_does_not_drop_canceled_trips():
    # Known, documented limitation (see shortHelpString): canceled_trip_ids are always
    # collected using the RT-side trip_id. In TRIP_ID mode that coincides with the
    # static trip_id, so drop_trip_ids correctly removes the trip. In
    # ROUTE_STOP_FALLBACK mode the RT trip_id namespace is, by construction, disjoint
    # from the static one — so a "canceled" RT trip_id never matches any static
    # trip_id, and rebuild_stop_times cannot drop it from the output. Pinned here so
    # this doesn't silently regress or surprise a future maintainer.
    idx = _make_static_index(
        trips={"static_trip_1": ("R1", "0")},
        stops={"static_trip_1": [(1, "A", 0, 60), (2, "B", 150, 150)]},
        all_route_ids={"R1"},
        all_stop_ids={"A", "B"},
    )
    drop_trip_ids = frozenset({"rt_only_trip_9"})  # an RT-side id, not a static one
    corrections, _, _ = rebuild_stop_times(
        idx, {}, drop_trip_ids=drop_trip_ids, matching_mode="ROUTE_STOP_FALLBACK"
    )
    assert ("static_trip_1", 1) in corrections


# ---------------------------------------------------------------------------
# sample_feed_capabilities (RT3-5)
# ---------------------------------------------------------------------------

def test_sample_feed_capabilities_mixed_feed(tmp_path):
    snap = _fake_snapshot(tmp_path)
    idx = _make_static_index(trips={}, stops={}, all_route_ids={"R1"}, all_stop_ids={"A"})

    # Entity 1: route_id known; one STU with stop_id known + absolute time present.
    stu1 = MagicMock()
    stu1.stop_id = "A"
    stu1.HasField.side_effect = lambda f: f == "departure"
    stu1.departure = MagicMock(time=1000, delay=0)
    stu1.arrival = MagicMock(time=0, delay=0)
    tu1 = MagicMock()
    tu1.trip.route_id = "R1"
    tu1.stop_time_update = [stu1]
    entity1 = MagicMock()
    entity1.HasField.side_effect = lambda f: f == "trip_update"
    entity1.trip_update = tu1

    # Entity 2: route_id NOT known; one STU with no stop_id, no absolute time.
    stu2 = MagicMock()
    stu2.stop_id = ""
    stu2.HasField.side_effect = lambda f: f == "departure"
    stu2.departure = MagicMock(time=0, delay=30)
    stu2.arrival = MagicMock(time=0, delay=0)
    tu2 = MagicMock()
    tu2.trip.route_id = "R9"
    tu2.stop_time_update = [stu2]
    entity2 = MagicMock()
    entity2.HasField.side_effect = lambda f: f == "trip_update"
    entity2.trip_update = tu2

    feed = _make_feed([entity1, entity2])

    with patch("easy_otp.core.gtfsrt_realizer.decode_snapshot", return_value=feed):
        capability = sample_feed_capabilities([snap], idx)

    assert capability["route_id_overlap"] == pytest.approx(0.5)
    assert capability["stop_id_presence_ratio"] == pytest.approx(0.5)
    assert capability["stop_id_overlap"] == pytest.approx(1.0)
    assert capability["absolute_time_ratio"] == pytest.approx(0.5)


def test_sample_feed_capabilities_empty_returns_zeros():
    idx = _make_static_index(trips={}, stops={})
    capability = sample_feed_capabilities([], idx)
    assert capability == {
        "route_id_overlap": 0.0,
        "stop_id_presence_ratio": 0.0,
        "stop_id_overlap": 0.0,
        "absolute_time_ratio": 0.0,
    }


# ---------------------------------------------------------------------------
# resolve_matching_mode (RT3-5, build_realized_gtfs.py)
# ---------------------------------------------------------------------------

def test_resolve_matching_mode_explicit_passthrough():
    assert resolve_matching_mode(0.0, {}, "TRIP_ID") == "TRIP_ID"
    assert resolve_matching_mode(0.0, {}, "ROUTE_STOP_FALLBACK") == "ROUTE_STOP_FALLBACK"


def test_resolve_matching_mode_auto_picks_trip_id_on_good_overlap():
    assert resolve_matching_mode(0.10, {}, "AUTO") == "TRIP_ID"


def test_resolve_matching_mode_auto_picks_fallback_when_capable():
    capability = {
        "route_id_overlap": 0.9,
        "stop_id_presence_ratio": 0.9,
        "stop_id_overlap": 0.9,
        "absolute_time_ratio": 0.9,
    }
    assert resolve_matching_mode(0.0, capability, "AUTO") == "ROUTE_STOP_FALLBACK"


def test_resolve_matching_mode_auto_raises_when_neither_usable():
    capability = {
        "route_id_overlap": 0.1,
        "stop_id_presence_ratio": 0.1,
        "stop_id_overlap": 0.1,
        "absolute_time_ratio": 0.1,
    }
    with pytest.raises(ValueError):
        resolve_matching_mode(0.0, capability, "AUTO")


def test_resolve_matching_mode_auto_rejects_fallback_when_route_id_overlap_low():
    # Regression pin: collect_segment_times' ROUTE_STOP_FALLBACK branch silently
    # skips any TripUpdate whose route_id isn't in the static feed, so a low
    # route_id_overlap must block AUTO from selecting ROUTE_STOP_FALLBACK even when
    # the other three ratios look fine — otherwise most entities get silently
    # dropped with no fail-fast signal.
    capability = {
        "route_id_overlap": 0.05,
        "stop_id_presence_ratio": 0.9,
        "stop_id_overlap": 0.9,
        "absolute_time_ratio": 0.9,
    }
    with pytest.raises(ValueError):
        resolve_matching_mode(0.0, capability, "AUTO")
