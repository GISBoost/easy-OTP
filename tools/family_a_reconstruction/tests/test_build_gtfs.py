"""Unit tests for family_a.build_gtfs (FA-3).

No QGIS, no network - pure stdlib + pytest.
Mirrors easy_otp/test/test_build_realized_gtfs.py's helper style and test
scenarios, minus the drop_trip_ids cases (Family A has no cancellation
signal).
Run: pytest tests/test_build_gtfs.py -v
"""

import csv
import io
import zipfile
from pathlib import Path

from family_a.build_gtfs import (
    StaticIndex,
    format_gtfs_time,
    load_static_index,
    parse_gtfs_time,
    rebuild_stop_times,
    repackage_gtfs,
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
            for extra_name, content in extra_files.items():
                zf.writestr(extra_name, content)

    return str(zip_path)


def _read_zip_member(path: str, member: str) -> str:
    with zipfile.ZipFile(path) as zf:
        return zf.read(member).decode("utf-8")


def _make_static_index(
    trips: dict[str, tuple[str, str]],
    stops: dict[str, list[tuple]],
) -> StaticIndex:
    stop_map = {}
    for trip_id, stop_list in stops.items():
        for seq, stop_id, arr, dep in stop_list:
            stop_map[(trip_id, seq)] = (stop_id, arr, dep)
    return StaticIndex(
        trip_route=trips,
        trip_stops={tid: sorted(sl, key=lambda x: x[0]) for tid, sl in stops.items()},
        stop_map=stop_map,
        all_trip_ids=set(trips.keys()),
    )


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


def test_load_static_index_basic(tmp_path):
    path = _make_gtfs_zip(
        tmp_path,
        trip_rows=[{"trip_id": "t1", "route_id": "R1", "direction_id": "0"}],
        stop_times_rows=[
            {"trip_id": "t1", "arrival_time": "08:00:00", "departure_time": "08:00:00",
             "stop_id": "A", "stop_sequence": "1"},
            {"trip_id": "t1", "arrival_time": "08:10:00", "departure_time": "08:11:00",
             "stop_id": "B", "stop_sequence": "2"},
        ],
    )
    idx = load_static_index(path)
    assert idx.trip_route == {"t1": ("R1", "0")}
    assert idx.all_trip_ids == {"t1"}
    assert idx.trip_stops["t1"] == [
        (1, "A", parse_gtfs_time("08:00:00"), parse_gtfs_time("08:00:00")),
        (2, "B", parse_gtfs_time("08:10:00"), parse_gtfs_time("08:11:00")),
    ]
    assert idx.stop_map[("t1", 2)] == ("B", parse_gtfs_time("08:10:00"), parse_gtfs_time("08:11:00"))


# ---------------------------------------------------------------------------
# rebuild_stop_times
# ---------------------------------------------------------------------------


def _simple_index() -> StaticIndex:
    # trip t1: A(dep=60) -> B(arr=120, dep=180) -> C(arr=240, dep=240)
    return _make_static_index(
        trips={"t1": ("R1", "0")},
        stops={"t1": [
            (1, "A", 0, 60),      # arr=0, dep=60
            (2, "B", 120, 180),   # arr=120, dep=180
            (3, "C", 240, 240),   # arr=240, dep=240
        ]},
    )


def test_rebuild_gap_fallback_uses_scheduled():
    idx = _simple_index()
    corrections, corrected, gaps = rebuild_stop_times(idx, {})
    assert corrected == 0
    assert gaps == 2  # two segments (A->B, B->C)
    # First stop unchanged
    assert corrections[("t1", 1)] == (0, 60)
    # A->B scheduled travel = 120 - 60 = 60; dwell at B = 180 - 120 = 60
    assert corrections[("t1", 2)] == (120, 180)
    # B->C scheduled travel = 240 - 180 = 60; dwell at C = 0
    assert corrections[("t1", 3)] == (240, 240)


def test_rebuild_corrected_segment():
    idx = _simple_index()
    # Observed A->B travel = 90s (30s longer than scheduled 60s)
    stats = {("R1", "0", "A", "B"): 90.0}
    corrections, corrected, gaps = rebuild_stop_times(idx, stats)
    assert corrected == 1
    assert gaps == 1  # B->C still a gap
    # new_arr[B] = 60 + 90 = 150; dwell = 60; new_dep[B] = 210
    assert corrections[("t1", 2)] == (150, 210)
    # B->C: travel = 240 - 180 = 60 (scheduled); but running_time is now 210
    assert corrections[("t1", 3)] == (270, 270)


def test_rebuild_monotonic_clamp():
    idx = _simple_index()
    # Inject a negative segment stat - clamp must prevent non-monotonic output
    stats = {("R1", "0", "A", "B"): -999.0}
    corrections, corrected, gaps = rebuild_stop_times(idx, stats)
    arr_b, dep_b = corrections[("t1", 2)]
    arr_a, dep_a = corrections[("t1", 1)]
    assert arr_b >= dep_a  # arrival at B >= departure from A
    assert dep_b >= arr_b  # departure from B >= arrival at B


def test_rebuild_gtfs_time_over_24():
    # Trip spanning midnight: X departs at 23:55:00, Y arrives at 24:05:00
    idx = _make_static_index(
        trips={"t_night": ("R2", "0")},
        stops={"t_night": [
            (1, "X", parse_gtfs_time("23:50:00"), parse_gtfs_time("23:55:00")),
            (2, "Y", parse_gtfs_time("24:05:00"), parse_gtfs_time("24:05:00")),
        ]},
    )
    stats = {("R2", "0", "X", "Y"): 600.0}
    corrections, corrected, gaps = rebuild_stop_times(idx, stats)
    arr_y, dep_y = corrections[("t_night", 2)]
    assert arr_y == parse_gtfs_time("24:05:00")
    assert format_gtfs_time(arr_y) == "24:05:00"


def test_rebuild_dwell_preserved():
    idx = _simple_index()
    stats = {("R1", "0", "A", "B"): 90.0}
    corrections, _corrected, _gaps = rebuild_stop_times(idx, stats)
    arr_b, dep_b = corrections[("t1", 2)]
    assert dep_b - arr_b == 60  # scheduled dwell at B preserved


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
    corrections = {("t1", 2): (parse_gtfs_time("08:15:00"), parse_gtfs_time("08:16:00"))}
    out_path = str(tmp_path / "out.zip")
    repackage_gtfs(src_path, out_path, corrections)

    content = _read_zip_member(out_path, "stop_times.txt")
    assert "08:15:00" in content
    assert "08:16:00" in content
    assert "08:10:00" not in content  # original replaced


def test_repackage_untouched_row_kept_unchanged(tmp_path):
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
    # Only correct stop 2; stop 1 has no entry in corrections.
    corrections = {("t1", 2): (parse_gtfs_time("08:15:00"), parse_gtfs_time("08:16:00"))}
    out_path = str(tmp_path / "out.zip")
    repackage_gtfs(src_path, out_path, corrections)

    content = _read_zip_member(out_path, "stop_times.txt")
    assert "08:00:00" in content  # stop 1 untouched


def test_repackage_preserves_extra_stop_times_columns(tmp_path):
    # Non-standard columns (pickup_type, drop_off_type) must be preserved unchanged
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
        w.writeheader()
        w.writerows(trip_rows)
        zf.writestr("trips.txt", buf.getvalue())

        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=fields)
        w.writeheader()
        w.writerows(st_rows)
        zf.writestr("stop_times.txt", buf.getvalue())

    out_path = str(tmp_path / "out.zip")
    repackage_gtfs(str(zip_path), out_path, {})

    content = _read_zip_member(out_path, "stop_times.txt")
    assert "pickup_type" in content
    assert "drop_off_type" in content
    assert ",1\r\n" in content or ",1\n" in content  # drop_off_type=1 preserved
