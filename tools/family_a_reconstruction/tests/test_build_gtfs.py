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
    interpolate_blank_stop_times,
    load_static_index,
    parse_gtfs_time,
    rebuild_stop_times,
    repackage_gtfs,
    segment_key_for,
)
from family_a.calendar_scope import time_bucket_for_seconds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CALENDAR_FIELDS = [
    "service_id", "monday", "tuesday", "wednesday", "thursday", "friday",
    "saturday", "sunday", "start_date", "end_date",
]
_CALENDAR_DATES_FIELDS = ["service_id", "date", "exception_type"]


def _make_gtfs_zip(
    tmp_path: Path,
    trip_rows: list[dict],
    stop_times_rows: list[dict],
    extra_files: dict[str, str] | None = None,
    calendar_rows: list[dict] | None = None,
    calendar_dates_rows: list[dict] | None = None,
    name: str = "static.zip",
) -> str:
    zip_path = tmp_path / name
    trip_fields = ["trip_id", "route_id", "direction_id", "service_id"]
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

        if calendar_rows is not None:
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=_CALENDAR_FIELDS)
            w.writeheader()
            w.writerows(calendar_rows)
            zf.writestr("calendar.txt", buf.getvalue())

        if calendar_dates_rows is not None:
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=_CALENDAR_DATES_FIELDS)
            w.writeheader()
            w.writerows(calendar_dates_rows)
            zf.writestr("calendar_dates.txt", buf.getvalue())

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
    service_ids: dict[str, str] | None = None,
) -> StaticIndex:
    stop_map = {}
    for trip_id, stop_list in stops.items():
        for seq, stop_id, arr, dep in stop_list:
            stop_map[(trip_id, seq)] = (stop_id, arr, dep)
    if service_ids is None:
        service_ids = {tid: "" for tid in trips}
    return StaticIndex(
        trip_route=trips,
        trip_stops={tid: sorted(sl, key=lambda x: x[0]) for tid, sl in stops.items()},
        stop_map=stop_map,
        all_trip_ids=set(trips.keys()),
        trip_service_id=service_ids,
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
        trip_rows=[{"trip_id": "t1", "route_id": "R1", "direction_id": "0", "service_id": "svc1"}],
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
    assert idx.trip_service_id == {"t1": "svc1"}
    assert idx.trip_stops["t1"] == [
        (1, "A", parse_gtfs_time("08:00:00"), parse_gtfs_time("08:00:00")),
        (2, "B", parse_gtfs_time("08:10:00"), parse_gtfs_time("08:11:00")),
    ]
    assert idx.stop_map[("t1", 2)] == ("B", parse_gtfs_time("08:10:00"), parse_gtfs_time("08:11:00"))


# ---------------------------------------------------------------------------
# load_static_index - stop_time_dist_traveled (FA-10)
# ---------------------------------------------------------------------------


def test_load_static_index_stop_time_dist_traveled_filled(tmp_path):
    path = tmp_path / "gtfs.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("trips.txt", "trip_id,route_id,direction_id,service_id\nt1,R1,0,svc1\n")
        zf.writestr(
            "stop_times.txt",
            "trip_id,arrival_time,departure_time,stop_id,stop_sequence,shape_dist_traveled\n"
            "t1,08:00:00,08:00:00,A,1,0.0\n"
            "t1,08:10:00,08:11:00,B,2,1112.0\n",
        )
    idx = load_static_index(str(path))
    assert idx.stop_time_dist_traveled == {("t1", 1): 0.0, ("t1", 2): 1112.0}


def test_load_static_index_stop_time_dist_traveled_absent_column(tmp_path):
    path = _make_gtfs_zip(
        tmp_path,
        trip_rows=[{"trip_id": "t1", "route_id": "R1", "direction_id": "0", "service_id": "svc1"}],
        stop_times_rows=[
            {"trip_id": "t1", "arrival_time": "08:00:00", "departure_time": "08:00:00",
             "stop_id": "A", "stop_sequence": "1"},
        ],
    )
    idx = load_static_index(path)
    assert idx.stop_time_dist_traveled == {("t1", 1): None}


def test_load_static_index_stop_time_dist_traveled_blank_value_is_none(tmp_path):
    path = tmp_path / "gtfs.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("trips.txt", "trip_id,route_id,direction_id,service_id\nt1,R1,0,svc1\n")
        zf.writestr(
            "stop_times.txt",
            "trip_id,arrival_time,departure_time,stop_id,stop_sequence,shape_dist_traveled\n"
            "t1,08:00:00,08:00:00,A,1,\n"
            "t1,08:10:00,08:11:00,B,2,1112.0\n",
        )
    idx = load_static_index(str(path))
    assert idx.stop_time_dist_traveled == {("t1", 1): None, ("t1", 2): 1112.0}


# ---------------------------------------------------------------------------
# interpolate_blank_stop_times (FA-19)
# ---------------------------------------------------------------------------


def test_interpolate_blank_run_is_spread_evenly():
    stops = [
        (1, "A", 28800, 28800),
        (2, "B", None, None),
        (3, "C", None, None),
        (4, "D", 29100, 29100),
    ]
    out, filled = interpolate_blank_stop_times(stops)
    assert out == [
        (1, "A", 28800, 28800),
        (2, "B", 28900, 28900),
        (3, "C", 29000, 29000),
        (4, "D", 29100, 29100),
    ]
    assert filled == {2, 3}


def test_interpolate_single_blank_lands_midway_between_departure_and_arrival():
    # Anchors are the previous stop's DEPARTURE (1060) and the next stop's ARRIVAL (1160),
    # not their arrivals - so the dwell at stop 1 must not be interpolated across.
    stops = [(1, "A", 1000, 1060), (2, "B", None, None), (3, "C", 1160, 1200)]
    out, filled = interpolate_blank_stop_times(stops)
    assert out[1] == (2, "B", 1110, 1110)
    assert filled == {2}


def test_interpolate_leading_blanks_clamp_to_first_known_arrival():
    stops = [(1, "A", None, None), (2, "B", None, None), (3, "C", 500, 600)]
    out, filled = interpolate_blank_stop_times(stops)
    assert out == [(1, "A", 500, 500), (2, "B", 500, 500), (3, "C", 500, 600)]
    assert filled == {1, 2}


def test_interpolate_trailing_blanks_clamp_to_last_known_departure():
    stops = [(1, "A", 100, 200), (2, "B", None, None)]
    out, filled = interpolate_blank_stop_times(stops)
    assert out == [(1, "A", 100, 200), (2, "B", 200, 200)]
    assert filled == {2}


def test_interpolate_trip_without_any_times_keeps_zeros():
    # Nothing to anchor to. Every sequence comes back in the filled set, which is exactly how
    # load_static_index recognises this case in order to warn about it.
    stops = [(1, "A", None, None), (2, "B", None, None)]
    out, filled = interpolate_blank_stop_times(stops)
    assert out == [(1, "A", 0, 0), (2, "B", 0, 0)]
    assert filled == {1, 2}


def test_interpolate_leaves_a_fully_timed_trip_untouched():
    stops = [(1, "A", 100, 120), (2, "B", 300, 300)]
    out, filled = interpolate_blank_stop_times(stops)
    assert out == stops
    assert filled == set()


# ---------------------------------------------------------------------------
# load_static_index - blank scheduled times (FA-19)
# ---------------------------------------------------------------------------


def _blank_times_zip(tmp_path) -> str:
    """The Bucharest MREX_LV_968_0_316 shape: two blank stops between two timepoints."""
    return _make_gtfs_zip(
        tmp_path,
        trip_rows=[{"trip_id": "t1", "route_id": "R1", "direction_id": "0", "service_id": "svc1"}],
        stop_times_rows=[
            {"trip_id": "t1", "arrival_time": "23:10:00", "departure_time": "23:10:00",
             "stop_id": "A", "stop_sequence": "2"},
            {"trip_id": "t1", "arrival_time": "", "departure_time": "",
             "stop_id": "B", "stop_sequence": "3"},
            {"trip_id": "t1", "arrival_time": "", "departure_time": "",
             "stop_id": "C", "stop_sequence": "4"},
            {"trip_id": "t1", "arrival_time": "23:16:30", "departure_time": "23:17:00",
             "stop_id": "D", "stop_sequence": "5"},
        ],
    )


def test_load_static_index_interpolates_blank_times(tmp_path):
    idx = load_static_index(_blank_times_zip(tmp_path))
    # 83400 -> 83790 over three hops: 130 s each.
    assert idx.trip_stops["t1"] == [
        (2, "A", 83400, 83400),
        (3, "B", 83530, 83530),
        (4, "C", 83660, 83660),
        (5, "D", 83790, 83820),
    ]


def test_load_static_index_records_which_stops_were_interpolated(tmp_path):
    idx = load_static_index(_blank_times_zip(tmp_path))
    assert idx.interpolated_time_stops == {("t1", 3), ("t1", 4)}


def test_load_static_index_stop_map_agrees_with_trip_stops_after_interpolation(tmp_path):
    idx = load_static_index(_blank_times_zip(tmp_path))
    for seq, stop_id, arr, dep in idx.trip_stops["t1"]:
        assert idx.stop_map[("t1", seq)] == (stop_id, arr, dep)


def test_load_static_index_genuine_midnight_is_not_treated_as_blank(tmp_path):
    # The whole defect was "" being falsy; an explicit 00:00:00 is a real time and must survive
    # untouched, or every overnight trip would be silently reinterpolated.
    path = _make_gtfs_zip(
        tmp_path,
        trip_rows=[{"trip_id": "t1", "route_id": "R1", "direction_id": "0", "service_id": "svc1"}],
        stop_times_rows=[
            {"trip_id": "t1", "arrival_time": "00:00:00", "departure_time": "00:00:00",
             "stop_id": "A", "stop_sequence": "1"},
            {"trip_id": "t1", "arrival_time": "00:05:00", "departure_time": "00:05:00",
             "stop_id": "B", "stop_sequence": "2"},
        ],
    )
    idx = load_static_index(path)
    assert idx.trip_stops["t1"] == [(1, "A", 0, 0), (2, "B", 300, 300)]
    assert idx.interpolated_time_stops == set()


def test_load_static_index_one_sided_blank_still_falls_back_to_the_other_field(tmp_path):
    # Pre-FA-19 behaviour, deliberately unchanged: a row with only one of the two fields is not
    # the blank case and must not be interpolated.
    path = _make_gtfs_zip(
        tmp_path,
        trip_rows=[{"trip_id": "t1", "route_id": "R1", "direction_id": "0", "service_id": "svc1"}],
        stop_times_rows=[
            {"trip_id": "t1", "arrival_time": "08:00:00", "departure_time": "",
             "stop_id": "A", "stop_sequence": "1"},
            {"trip_id": "t1", "arrival_time": "", "departure_time": "08:05:00",
             "stop_id": "B", "stop_sequence": "2"},
        ],
    )
    idx = load_static_index(path)
    assert idx.trip_stops["t1"] == [(1, "A", 28800, 28800), (2, "B", 29100, 29100)]
    assert idx.interpolated_time_stops == set()


def test_rebuild_stop_times_does_not_explode_on_a_blank_run(tmp_path):
    """The end-to-end symptom FA-19 exists to kill.

    With no observations at all every segment falls back to its scheduled duration, so a trip
    whose times are intact must come out exactly as scheduled. Before FA-19 the blank stop parsed
    as 0, which made the NEXT segment book 23:10:00 itself as a travel time: the trip ended at
    46:10:00 instead of 23:10:00.
    """
    path = _make_gtfs_zip(
        tmp_path,
        trip_rows=[{"trip_id": "t1", "route_id": "R1", "direction_id": "0", "service_id": "svc1"}],
        stop_times_rows=[
            {"trip_id": "t1", "arrival_time": "23:00:00", "departure_time": "23:00:00",
             "stop_id": "A", "stop_sequence": "1"},
            {"trip_id": "t1", "arrival_time": "", "departure_time": "",
             "stop_id": "B", "stop_sequence": "2"},
            {"trip_id": "t1", "arrival_time": "23:10:00", "departure_time": "23:10:00",
             "stop_id": "C", "stop_sequence": "3"},
        ],
    )
    idx = load_static_index(path)
    corrections, _corrected, _gap = rebuild_stop_times(idx, {}, {"svc1": {"WEEKDAY"}})

    assert corrections[("t1", 3)] == (parse_gtfs_time("23:10:00"), parse_gtfs_time("23:10:00"))
    assert corrections[("t1", 2)] == (parse_gtfs_time("23:05:00"), parse_gtfs_time("23:05:00"))


# ---------------------------------------------------------------------------
# rebuild_stop_times
# ---------------------------------------------------------------------------

_DEFAULT_DAY_TYPES = {"svc_weekday": {"WEEKDAY"}}


def _simple_index() -> StaticIndex:
    # trip t1: A(dep=60) -> B(arr=120, dep=180) -> C(arr=240, dep=240)
    return _make_static_index(
        trips={"t1": ("R1", "0")},
        stops={"t1": [
            (1, "A", 0, 60),      # arr=0, dep=60
            (2, "B", 120, 180),   # arr=120, dep=180
            (3, "C", 240, 240),   # arr=240, dep=240
        ]},
        service_ids={"t1": "svc_weekday"},
    )


def test_rebuild_gap_fallback_uses_scheduled():
    idx = _simple_index()
    corrections, corrected, gaps = rebuild_stop_times(idx, {}, _DEFAULT_DAY_TYPES)
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
    # Observed A->B travel = 90s (30s longer than scheduled 60s). A's scheduled
    # departure (60s) is bucket 0 at the default 120-min width.
    bucket = time_bucket_for_seconds(60, 120)
    stats = {segment_key_for("R1", "0", "A", "B", "WEEKDAY", bucket): 90.0}
    corrections, corrected, gaps = rebuild_stop_times(idx, stats, _DEFAULT_DAY_TYPES)
    assert corrected == 1
    assert gaps == 1  # B->C still a gap
    # new_arr[B] = 60 + 90 = 150; dwell = 60; new_dep[B] = 210
    assert corrections[("t1", 2)] == (150, 210)
    # B->C: travel = 240 - 180 = 60 (scheduled); but running_time is now 210
    assert corrections[("t1", 3)] == (270, 270)


def test_rebuild_monotonic_clamp():
    idx = _simple_index()
    # Inject a negative segment stat - clamp must prevent non-monotonic output
    bucket = time_bucket_for_seconds(60, 120)
    stats = {segment_key_for("R1", "0", "A", "B", "WEEKDAY", bucket): -999.0}
    corrections, corrected, gaps = rebuild_stop_times(idx, stats, _DEFAULT_DAY_TYPES)
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
        service_ids={"t_night": "svc_weekday"},
    )
    prev_dep = parse_gtfs_time("23:55:00")
    bucket = time_bucket_for_seconds(prev_dep, 120)
    stats = {segment_key_for("R2", "0", "X", "Y", "WEEKDAY", bucket): 600.0}
    corrections, corrected, gaps = rebuild_stop_times(idx, stats, _DEFAULT_DAY_TYPES)
    arr_y, dep_y = corrections[("t_night", 2)]
    assert arr_y == parse_gtfs_time("24:05:00")
    assert format_gtfs_time(arr_y) == "24:05:00"


def test_rebuild_dwell_preserved():
    idx = _simple_index()
    bucket = time_bucket_for_seconds(60, 120)
    stats = {segment_key_for("R1", "0", "A", "B", "WEEKDAY", bucket): 90.0}
    corrections, _corrected, _gaps = rebuild_stop_times(idx, stats, _DEFAULT_DAY_TYPES)
    arr_b, dep_b = corrections[("t1", 2)]
    assert dep_b - arr_b == 60  # scheduled dwell at B preserved


def test_segment_key_for_builds_expected_tuple():
    assert segment_key_for("R1", "0", "A", "B", "WEEKDAY", 3) == ("R1", "0", "A", "B", "WEEKDAY", 3)


def test_rebuild_day_type_scoping_denies_wrong_day_type():
    # Two trips share route/stop pair A->B, one WEEKDAY service, one SUNDAY -
    # observed data for WEEKDAY only must not leak into the SUNDAY trip. This
    # is the direct regression test for the Lodz finding (11443_11337, a 3:48
    # AM trip corrected by an afternoon-only recording).
    idx = _make_static_index(
        trips={"t_wd": ("R1", "0"), "t_sun": ("R1", "0")},
        stops={
            "t_wd": [(1, "A", 0, 60), (2, "B", 120, 180)],
            "t_sun": [(1, "A", 0, 60), (2, "B", 120, 180)],
        },
        service_ids={"t_wd": "svc_wd", "t_sun": "svc_sun"},
    )
    service_day_types = {"svc_wd": {"WEEKDAY"}, "svc_sun": {"SUNDAY"}}
    bucket = time_bucket_for_seconds(60, 120)
    stats = {segment_key_for("R1", "0", "A", "B", "WEEKDAY", bucket): 90.0}

    corrections, corrected, gaps = rebuild_stop_times(idx, stats, service_day_types)

    assert corrected == 1
    assert gaps == 1
    assert corrections[("t_wd", 2)] == (150, 210)  # corrected: 60+90=150, dwell 60
    assert corrections[("t_sun", 2)] == (120, 180)  # stays scheduled


def test_rebuild_time_bucket_scoping_denies_wrong_bucket():
    # Same day_type, two scheduled instances of the same stop pair at
    # different times of day - only the afternoon bucket has observed data.
    idx = _make_static_index(
        trips={"t_morning": ("R1", "0"), "t_afternoon": ("R1", "0")},
        stops={
            "t_morning": [(1, "A", 0, 0), (2, "B", 100, 100)],
            "t_afternoon": [(1, "A", 43200, 43200), (2, "B", 43300, 43300)],
        },
        service_ids={"t_morning": "svc_weekday", "t_afternoon": "svc_weekday"},
    )
    afternoon_bucket = time_bucket_for_seconds(43200, 120)
    stats = {segment_key_for("R1", "0", "A", "B", "WEEKDAY", afternoon_bucket): 90.0}

    corrections, corrected, gaps = rebuild_stop_times(idx, stats, _DEFAULT_DAY_TYPES)

    assert corrected == 1
    assert gaps == 1
    assert corrections[("t_morning", 2)] == (100, 100)  # stays scheduled
    arr_afternoon, _dep_afternoon = corrections[("t_afternoon", 2)]
    assert arr_afternoon == 43200 + 90  # corrected


def test_rebuild_unknown_service_id_is_always_gap():
    # A service_id absent from service_day_types (no known active dates) must
    # never match segment_stats, no matter how densely the route/stops are
    # covered - "empty day_types" must mean "always gap", not "matches
    # everything".
    idx = _simple_index()
    idx.trip_service_id["t1"] = "svc_unknown"
    bucket = time_bucket_for_seconds(60, 120)
    dense_stats = {
        segment_key_for("R1", "0", "A", "B", day_type, b): 90.0
        for day_type in ("WEEKDAY", "SATURDAY", "SUNDAY")
        for b in range(12)
    }
    assert segment_key_for("R1", "0", "A", "B", "WEEKDAY", bucket) in dense_stats

    corrections, corrected, gaps = rebuild_stop_times(idx, dense_stats, {})

    assert corrected == 0
    assert gaps == 2
    assert corrections[("t1", 2)] == (120, 180)  # stays scheduled


def test_rebuild_multi_day_type_service_picks_deterministic_alphabetical_match():
    # A service that "runs every day" (trip_day_types has all 3 members) with
    # segment_stats populated for more than one of them must pick the same
    # candidate every run, not whatever order set iteration happens to
    # produce - sorted() gives SATURDAY < SUNDAY < WEEKDAY alphabetically.
    idx = _simple_index()
    idx.trip_service_id["t1"] = "svc_every_day"
    bucket = time_bucket_for_seconds(60, 120)
    stats = {
        segment_key_for("R1", "0", "A", "B", "SATURDAY", bucket): 11.0,
        segment_key_for("R1", "0", "A", "B", "SUNDAY", bucket): 22.0,
        segment_key_for("R1", "0", "A", "B", "WEEKDAY", bucket): 33.0,
    }
    service_day_types = {"svc_every_day": {"WEEKDAY", "SATURDAY", "SUNDAY"}}

    corrections, corrected, _gaps = rebuild_stop_times(idx, stats, service_day_types)

    assert corrected == 1
    # SATURDAY sorts first -> its value (11.0) must be the one applied.
    assert corrections[("t1", 2)] == (60 + 11, 60 + 11 + 60)


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


def test_repackage_streaming_large_row_count_flush_before_close(tmp_path):
    # Several hundred rows to exceed TextIOWrapper's default internal buffer -
    # catches a missed text_out.flush() before the `with` block closes fh_out,
    # which would silently truncate the tail of the file.
    n_rows = 500
    stop_times_rows = [
        {
            "trip_id": "t1",
            "arrival_time": format_gtfs_time(60 * i),
            "departure_time": format_gtfs_time(60 * i),
            "stop_id": f"S{i}",
            "stop_sequence": str(i + 1),
        }
        for i in range(n_rows)
    ]
    src_path = _make_gtfs_zip(
        tmp_path,
        trip_rows=[{"trip_id": "t1", "route_id": "R1", "direction_id": "0"}],
        stop_times_rows=stop_times_rows,
    )
    out_path = str(tmp_path / "out.zip")
    repackage_gtfs(src_path, out_path, {})

    content = _read_zip_member(out_path, "stop_times.txt")
    lines = content.strip("\r\n").split("\r\n")
    assert len(lines) == n_rows + 1  # header + n_rows
    last_row = lines[-1]
    assert last_row.split(",")[3] == f"S{n_rows - 1}"  # last row's stop_id present and correct


def test_repackage_streaming_matches_buffered_output_byte_for_byte(tmp_path):
    trip_rows = [{"trip_id": "t1", "route_id": "R1", "direction_id": "0"}]
    fields = ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence",
              "pickup_type", "drop_off_type"]
    st_rows = [
        {"trip_id": "t1", "arrival_time": "08:00:00", "departure_time": "08:00:00",
         "stop_id": "A", "stop_sequence": "1", "pickup_type": "0", "drop_off_type": "1"},
        {"trip_id": "t1", "arrival_time": "08:10:00", "departure_time": "08:11:00",
         "stop_id": "B", "stop_sequence": "2", "pickup_type": "1", "drop_off_type": "0"},
        {"trip_id": "t1", "arrival_time": "08:20:00", "departure_time": "08:20:00",
         "stop_id": "C", "stop_sequence": "3", "pickup_type": "0", "drop_off_type": "0"},
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

    corrections = {("t1", 2): (parse_gtfs_time("08:15:00"), parse_gtfs_time("08:16:00"))}
    out_path = str(tmp_path / "out.zip")
    repackage_gtfs(str(zip_path), out_path, corrections)

    expected = (
        "trip_id,arrival_time,departure_time,stop_id,stop_sequence,pickup_type,drop_off_type\r\n"
        "t1,08:00:00,08:00:00,A,1,0,1\r\n"
        "t1,08:15:00,08:16:00,B,2,1,0\r\n"
        "t1,08:20:00,08:20:00,C,3,0,0\r\n"
    )
    content = _read_zip_member(out_path, "stop_times.txt")
    assert content == expected


# ---------------------------------------------------------------------------
# FA-15 — per-route correction counts
# ---------------------------------------------------------------------------


def test_rebuild_route_counts_out_param_splits_corrected_and_gap():
    idx = _simple_index()
    bucket = time_bucket_for_seconds(60, 120)
    stats = {segment_key_for("R1", "0", "A", "B", "WEEKDAY", bucket): 90.0}

    route_counts: dict[str, dict[str, int]] = {}
    _corrections, corrected, gaps = rebuild_stop_times(
        idx, stats, _DEFAULT_DAY_TYPES, route_counts=route_counts
    )

    # The per-route split must reconcile exactly with the whole-feed totals it accompanies.
    assert route_counts == {"R1": {"corrected": 1, "gap": 1}}
    assert sum(c["corrected"] for c in route_counts.values()) == corrected
    assert sum(c["gap"] for c in route_counts.values()) == gaps


def test_rebuild_route_counts_reports_a_route_with_no_corrections_at_all():
    """The FA-15 signal: a route whose every segment fell back to the schedule."""
    idx = _simple_index()

    route_counts: dict[str, dict[str, int]] = {}
    _corrections, corrected, gaps = rebuild_stop_times(
        idx, {}, _DEFAULT_DAY_TYPES, route_counts=route_counts
    )

    assert corrected == 0
    assert route_counts["R1"]["corrected"] == 0
    assert route_counts["R1"]["gap"] == gaps


def test_rebuild_without_route_counts_is_unchanged():
    """Omitting the out-param must reproduce the pre-FA-15 behaviour exactly."""
    idx = _simple_index()
    bucket = time_bucket_for_seconds(60, 120)
    stats = {segment_key_for("R1", "0", "A", "B", "WEEKDAY", bucket): 90.0}

    baseline = rebuild_stop_times(idx, stats, _DEFAULT_DAY_TYPES)
    with_counts = rebuild_stop_times(idx, stats, _DEFAULT_DAY_TYPES, route_counts={})

    assert baseline == with_counts
