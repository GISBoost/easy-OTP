"""Unit tests for family_a.segment_stats (FA-3).

No QGIS, no network - pure stdlib + pandas + pytest.
Run: pytest tests/test_segment_stats.py -v
"""

import statistics
import zoneinfo
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from family_a.build_gtfs import StaticIndex, rebuild_stop_times, segment_key_for
from family_a.calendar_scope import day_type_for_date, time_bucket_for_seconds
from family_a.interpolate import stop_distance_along_shape
from family_a.matcher import cumulative_distances
from family_a.segment_stats import (
    SEG_FIRST_PAIR,
    SEG_GAP,
    SEG_NO_PREVIOUS,
    SEG_OK,
    SEG_STATIONARY,
    aggregate_segments,
    collect_segment_observations,
    collect_stop_crossings,
    filter_min_observations,
)

# Same straight north-south line used in test_matcher.py / test_interpolate.py.
_STRAIGHT_LINE = [(0.0, 0.0), (0.01, 0.0), (0.02, 0.0)]

# Out-and-back loop mirroring shape 154679's exact structure (FA-11 handoff): the
# coordinate (0.01, 0.0) occurs at a low index (1) and a high index (3).
_LOOP_LINE = [(0.0, 0.0), (0.01, 0.0), (0.02, 0.0), (0.01, 0.0), (0.0, 0.0)]

_T0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def _t(seconds: int) -> datetime:
    return _T0 + timedelta(seconds=seconds)


def _make_static_index(trips, stops, service_ids=None) -> StaticIndex:
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


def _matched_df(rows: list[tuple]) -> pd.DataFrame:
    """Build a matched-style DataFrame. Rows are 3-tuples
    (trip_id, timestamp, distance_along_shape_m) for the pre-FA-6 shape (no
    recording_date column), or 4-tuples with a trailing recording_date
    (date) to exercise FA-6's (trip_id, recording_date) grouping. All rows
    in one call must be the same length.
    """
    has_recording_date = bool(rows) and len(rows[0]) == 4
    columns = ["trip_id", "timestamp", "distance_along_shape_m"]
    if has_recording_date:
        columns.append("recording_date")
    df = pd.DataFrame(rows, columns=columns)
    df["perpendicular_dist_m"] = 0.0
    return df


# ---------------------------------------------------------------------------
# aggregate_segments (mirrors RT-3's invariant tests)
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
    values = list(range(1, 21))
    key = ("R1", "0", "A", "B")
    p50, p85 = aggregate_segments({key: values})
    expected_p85 = statistics.quantiles(values, n=100, method="inclusive")[84]
    assert p85[key] == pytest.approx(expected_p85)


def test_aggregate_p85_never_exceeds_the_observed_maximum():
    """The D2 regression test.

    CPython's default 'exclusive' method estimates a POPULATION quantile and extrapolates past
    the largest observation whenever the sample is smaller than ~12 - which describes 56-78% of
    the keys in a real recording. 'inclusive' agrees with numpy.percentile, pandas.quantile and
    R's type 7, and stays inside [min, max] by construction. The existing p85 >= p50 clamp never
    caught this: it only guards the bottom.
    """
    cases = {
        ("R1", "0", "A", "B"): [100.0, 200.0],
        ("R1", "0", "B", "C"): [60.0, 90.0, 300.0],
        ("R1", "0", "C", "D"): [100.0, 200.0, 300.0, 400.0, 500.0],
    }
    _p50, p85 = aggregate_segments(cases)

    for key, values in cases.items():
        assert p85[key] <= max(values)
    # The three numbers numpy/pandas/R produce for the same inputs.
    assert p85[("R1", "0", "A", "B")] == pytest.approx(185.0)
    assert p85[("R1", "0", "B", "C")] == pytest.approx(237.0)
    assert p85[("R1", "0", "C", "D")] == pytest.approx(440.0)


def test_aggregate_exclusive_mode_reproduces_the_pre_d2_output():
    """The legacy arm, pinned - including the overshoot, which is the whole point of keeping it."""
    key = ("R1", "0", "A", "B")
    values = [100.0, 200.0]
    _p50, p85 = aggregate_segments({key: values}, percentile_method="exclusive")
    assert p85[key] == pytest.approx(255.0)
    assert p85[key] > max(values)


def test_aggregate_single_observation():
    key = ("R1", "0", "A", "B")
    p50, p85 = aggregate_segments({key: [42.0]})
    assert p50[key] == pytest.approx(42.0)
    assert p85[key] == pytest.approx(42.0)


# ---------------------------------------------------------------------------
# collect_segment_observations
#
# Every call in this block passes skip_first_segment=False, for the same reason the FA-18
# block passes max_bracket_gap_s=None: to isolate the mechanism under test. These fixtures
# are 2- and 3-stop trips whose subject IS the first stop pair (interpolation, bracket gaps,
# trusted shape_dist anchoring, recording_date grouping), and FA-20 drops that pair before
# any of it runs. FA-20's own behaviour, and the production defaults, are covered in the
# FA-17/FA-20 and FA-18 blocks below and end to end in test_cli.py.
# ---------------------------------------------------------------------------


def _two_stop_static_index() -> StaticIndex:
    return _make_static_index(
        trips={"t1": ("R1", "0")},
        stops={"t1": [(1, "A", 0, 0), (2, "B", 100, 100)]},
    )


def test_collect_successful_interpolation_appends_segment_time():
    idx = _two_stop_static_index()
    trip_shapes = {"t1": "shape1"}
    shapes = {"shape1": _STRAIGHT_LINE}
    stop_locations = {"A": (0.0, 0.0), "B": (0.01, 0.0)}
    d_b = stop_distance_along_shape(0.01, 0.0, _STRAIGHT_LINE)

    matched = _matched_df([
        ("t1", _t(0), 0.0),
        ("t1", _t(100), d_b),
    ])

    segment_times, counts = collect_segment_observations(
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC", skip_first_segment=False
    )

    # _t(0) = 2026-01-01T00:00:00 UTC, a Thursday -> WEEKDAY, bucket 0
    key = ("R1", "0", "A", "B", "WEEKDAY", time_bucket_for_seconds(0, 120))
    assert segment_times[key] == pytest.approx([100.0])
    assert counts["trips_processed"] == 1
    assert counts["segments_observed"] == 1
    assert counts["interpolation_gaps"] == 0
    assert counts["rejected_seg_time"] == 0


def test_collect_segment_observations_uses_local_time_not_utc_for_day_type():
    # Etc/GMT-9 is UTC+9 (Etc zone sign convention is inverted), no DST. A UTC
    # Saturday 23:00 observation lands on a local Sunday 08:00 - if the
    # tz_convert step were accidentally skipped, day_type would be derived from
    # the naive UTC Saturday 23:00 instead. (Since D3 the time_bucket comes from
    # the schedule, so only day_type still exercises the conversion; the bucket's
    # own two sources have their own tests below.)
    idx = _two_stop_static_index()
    trip_shapes = {"t1": "shape1"}
    shapes = {"shape1": _STRAIGHT_LINE}
    stop_locations = {"A": (0.0, 0.0), "B": (0.01, 0.0)}
    d_b = stop_distance_along_shape(0.01, 0.0, _STRAIGHT_LINE)

    t_from = datetime(2026, 1, 3, 23, 0, 0, tzinfo=timezone.utc)  # Saturday, UTC
    t_to = t_from + timedelta(seconds=100)

    matched = _matched_df([
        ("t1", t_from, 0.0),
        ("t1", t_to, d_b),
    ])

    segment_times, _counts = collect_segment_observations(
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="Etc/GMT-9", skip_first_segment=False
    )

    # Local: 2026-01-04 08:00:00, a Sunday. The bucket is A's scheduled departure (0).
    key = ("R1", "0", "A", "B", "SUNDAY", time_bucket_for_seconds(0, 120))
    assert segment_times[key] == pytest.approx([100.0])
    assert all(day_type == "SUNDAY" for *_rest, day_type, _bucket in segment_times)


def _late_vehicle_across_a_bucket_boundary():
    """A trip scheduled to leave A at 07:50 whose vehicle only crosses A at 08:10.

    With the default 120-minute buckets those two times sit on opposite sides of the 08:00
    boundary: scheduled -> bucket 3, observed -> bucket 4. This is the exact situation D3 is
    about, and 20 minutes of lateness is an ordinary rush-hour value, not a contrived one.
    """
    idx = _make_static_index(
        trips={"t1": ("R1", "0")},
        stops={"t1": [(1, "A", 28200, 28200), (2, "B", 28800, 28800)]},  # 07:50, 08:00
    )
    d_b = stop_distance_along_shape(0.01, 0.0, _STRAIGHT_LINE)
    matched = _matched_df([
        ("t1", _t(29400), 0.0),    # 08:10:00 UTC, Thursday -> WEEKDAY
        ("t1", _t(29500), d_b),    # 100 s later
    ])
    common = dict(
        trip_shapes={"t1": "shape1"},
        shapes={"shape1": _STRAIGHT_LINE},
        stop_locations={"A": (0.0, 0.0), "B": (0.01, 0.0)},
        agency_tz="UTC",
        skip_first_segment=False,
    )
    return idx, matched, common


def test_bucket_source_scheduled_files_a_late_observation_where_the_rebuild_looks():
    """The D3 regression test, end to end: archive side and apply side must agree.

    rebuild_stop_times searches by the SCHEDULED departure from the previous stop. Filing the
    observation by its scheduled departure too means a late vehicle is still found; filing it by
    the observed crossing (the pre-2026-08-09 behaviour, asserted in the next test) means it is
    filed in a bucket nobody ever searches, and the trip silently falls back to its schedule.
    """
    idx, matched, common = _late_vehicle_across_a_bucket_boundary()

    segment_times, _counts = collect_segment_observations(matched, idx, **common)

    scheduled_bucket = time_bucket_for_seconds(28200, 120)
    observed_bucket = time_bucket_for_seconds(29400, 120)
    assert scheduled_bucket != observed_bucket  # the fixture really does straddle a boundary
    assert list(segment_times) == [
        segment_key_for("R1", "0", "A", "B", "WEEKDAY", scheduled_bucket)
    ]

    # And the applying side actually consumes it.
    p50 = {key: statistics.median(v) for key, v in segment_times.items()}
    _corrections, corrected, _gaps = rebuild_stop_times(idx, p50, {"": {"WEEKDAY"}})
    assert corrected == 1


def test_bucket_source_observed_reproduces_the_pre_d3_loss():
    """The legacy arm, pinned - and the demonstration that it loses the observation.

    Same data, same rebuild: bucketing by the observed crossing puts the key in bucket 4 while
    rebuild_stop_times looks in bucket 3, so a 20-minutes-late vehicle contributes nothing.
    """
    idx, matched, common = _late_vehicle_across_a_bucket_boundary()

    segment_times, _counts = collect_segment_observations(
        matched, idx, time_bucket_source="observed", **common
    )

    assert list(segment_times) == [
        segment_key_for("R1", "0", "A", "B", "WEEKDAY", time_bucket_for_seconds(29400, 120))
    ]

    p50 = {key: statistics.median(v) for key, v in segment_times.items()}
    _corrections, corrected, gaps = rebuild_stop_times(idx, p50, {"": {"WEEKDAY"}})
    assert corrected == 0
    assert gaps == 1


def test_collect_one_sided_interpolation_failure_is_a_gap():
    idx = _two_stop_static_index()
    trip_shapes = {"t1": "shape1"}
    shapes = {"shape1": _STRAIGHT_LINE}
    stop_locations = {"A": (0.0, 0.0), "B": (0.01, 0.0)}

    # Vehicle only ever observed near A - B's distance is never bracketed.
    matched = _matched_df([
        ("t1", _t(0), 0.0),
        ("t1", _t(10), 1.0),
    ])

    segment_times, counts = collect_segment_observations(
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC", skip_first_segment=False
    )

    assert segment_times == {}
    assert counts["interpolation_gaps"] == 1
    assert counts["segments_observed"] == 0


def test_collect_wide_bracket_gap_excludes_only_that_stop_pair():
    """FA-14, PRD §7 #10: a widely-time-spaced bracket rejects only the affected
    stop pair (B->C) - the sibling pair in the same trip (A->B) is unaffected.
    """
    idx = _make_static_index(
        trips={"t1": ("R1", "0")},
        stops={"t1": [(1, "A", 0, 0), (2, "B", 100, 100), (3, "C", 300, 300)]},
    )
    trip_shapes = {"t1": "shape1"}
    shapes = {"shape1": _STRAIGHT_LINE}
    stop_locations = {"A": (0.0, 0.0), "B": (0.01, 0.0), "C": (0.02, 0.0)}
    d_b = stop_distance_along_shape(0.01, 0.0, _STRAIGHT_LINE)
    d_c = stop_distance_along_shape(0.02, 0.0, _STRAIGHT_LINE)

    # A->B bracketed by (t0, t50): 50s gap, accepted. B->C bracketed by (t50, t450):
    # 400s gap, rejected (> DEFAULT_MAX_BRACKET_GAP_S = 300s).
    matched = _matched_df([
        ("t1", _t(0), 0.0),
        ("t1", _t(50), d_b),
        ("t1", _t(450), d_c),
    ])

    segment_times, counts = collect_segment_observations(
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC", skip_first_segment=False
    )

    key_ab = ("R1", "0", "A", "B", "WEEKDAY", time_bucket_for_seconds(0, 120))
    key_bc = ("R1", "0", "B", "C", "WEEKDAY", time_bucket_for_seconds(0, 120))
    assert segment_times[key_ab] == pytest.approx([50.0])
    assert key_bc not in segment_times
    assert counts["segments_observed"] == 1
    assert counts["interpolation_gaps"] == 1
    assert counts["bracket_gap_rejected"] == 1


def test_collect_bracket_gap_rejected_not_incremented_under_threshold():
    idx = _two_stop_static_index()
    trip_shapes = {"t1": "shape1"}
    shapes = {"shape1": _STRAIGHT_LINE}
    stop_locations = {"A": (0.0, 0.0), "B": (0.01, 0.0)}
    d_b = stop_distance_along_shape(0.01, 0.0, _STRAIGHT_LINE)

    matched = _matched_df([
        ("t1", _t(0), 0.0),
        ("t1", _t(100), d_b),
    ])

    _segment_times, counts = collect_segment_observations(
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC", skip_first_segment=False
    )
    assert counts["bracket_gap_rejected"] == 0


def test_collect_custom_max_bracket_gap_s_threading():
    """A gap that passes the default (300s) but is rejected under a stricter
    caller-supplied threshold - proves max_bracket_gap_s reaches interpolate_stop_time.
    With only 2 observations, the same single bracketing pair is used to resolve both
    d_from (A, at the series' first point) and d_to (B, at the series' second point),
    so the rejection is counted twice - once per interpolate_stop_time call.
    """
    idx = _two_stop_static_index()
    trip_shapes = {"t1": "shape1"}
    shapes = {"shape1": _STRAIGHT_LINE}
    stop_locations = {"A": (0.0, 0.0), "B": (0.01, 0.0)}
    d_b = stop_distance_along_shape(0.01, 0.0, _STRAIGHT_LINE)

    matched = _matched_df([
        ("t1", _t(0), 0.0),
        ("t1", _t(200), d_b),
    ])

    segment_times, counts = collect_segment_observations(
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC", skip_first_segment=False,
        max_bracket_gap_s=60.0,
    )
    assert segment_times == {}
    assert counts["bracket_gap_rejected"] == 2
    assert counts["interpolation_gaps"] == 1


def test_collect_trip_with_no_resolvable_shape_is_skipped():
    idx = _two_stop_static_index()
    trip_shapes: dict[str, str] = {}  # no shape for t1
    shapes: dict[str, list] = {}
    stop_locations = {"A": (0.0, 0.0), "B": (0.01, 0.0)}

    matched = _matched_df([("t1", _t(0), 0.0), ("t1", _t(100), 100.0)])
    segment_times, counts = collect_segment_observations(
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC", skip_first_segment=False
    )

    assert segment_times == {}
    assert counts["trips_processed"] == 0
    assert counts["trips_skipped_unresolvable"] == 1


def test_collect_trip_with_fewer_than_two_stops_is_skipped():
    idx = _make_static_index(trips={"t1": ("R1", "0")}, stops={"t1": [(1, "A", 0, 0)]})
    trip_shapes = {"t1": "shape1"}
    shapes = {"shape1": _STRAIGHT_LINE}
    stop_locations = {"A": (0.0, 0.0)}

    matched = _matched_df([("t1", _t(0), 0.0)])
    segment_times, counts = collect_segment_observations(
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC", skip_first_segment=False
    )

    assert segment_times == {}
    assert counts["trips_processed"] == 0
    assert counts["trips_skipped_unresolvable"] == 1


def test_collect_missing_stop_location_is_distinct_from_interpolation_gap():
    idx = _two_stop_static_index()
    trip_shapes = {"t1": "shape1"}
    shapes = {"shape1": _STRAIGHT_LINE}
    stop_locations = {"A": (0.0, 0.0)}  # B missing

    matched = _matched_df([("t1", _t(0), 0.0), ("t1", _t(100), 100.0)])
    segment_times, counts = collect_segment_observations(
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC", skip_first_segment=False
    )

    assert segment_times == {}
    assert counts["trips_processed"] == 1  # the trip itself was resolvable and attempted
    assert counts["missing_stop_location"] == 1
    assert counts["interpolation_gaps"] == 0


def test_collect_rejects_implausible_segment_time():
    idx = _two_stop_static_index()
    trip_shapes = {"t1": "shape1"}
    shapes = {"shape1": _STRAIGHT_LINE}
    stop_locations = {"A": (0.0, 0.0), "B": (0.01, 0.0)}
    d_b = stop_distance_along_shape(0.01, 0.0, _STRAIGHT_LINE)

    # 3 hours between the two observations -> derived segment time > 7200s. Disables
    # FA-14's bracket-gap check (max_bracket_gap_s=None) to isolate this test to the
    # seg_time sanity filter specifically - a 3h bracket gap would otherwise be caught
    # by that earlier check first (see test_collect_wide_bracket_gap_excludes_only_that_stop_pair
    # for a bracket-gap-specific test).
    matched = _matched_df([
        ("t1", _t(0), 0.0),
        ("t1", _t(3 * 3600), d_b),
    ])

    segment_times, counts = collect_segment_observations(
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC", skip_first_segment=False, max_bracket_gap_s=None
    )

    assert segment_times == {}
    assert counts["rejected_seg_time"] == 1
    assert counts["segments_observed"] == 0
    # Precedence lock (FA-18): this observation implies 0.37 km/h, so it is stationary too,
    # but rejected_seg_time wins and rejected_stationary must NOT also count it. Documented
    # consequence: the very slowest cases bypass the counter named after them.
    assert counts["rejected_stationary"] == 0


def test_collect_rejects_implausible_speed():
    """FA-13: a segment covering ~1111m (A->B, see d_b) in 10s implies ~111 m/s -
    far above _MAX_PLAUSIBLE_SPEED_MPS (100 km/h ~= 27.78 m/s) - even though its duration alone is
    well under _MAX_PLAUSIBLE_SEG_TIME_S and its bracket gap is well under
    DEFAULT_MAX_BRACKET_GAP_S, so this isolates the new speed check specifically.
    """
    idx = _two_stop_static_index()
    trip_shapes = {"t1": "shape1"}
    shapes = {"shape1": _STRAIGHT_LINE}
    stop_locations = {"A": (0.0, 0.0), "B": (0.01, 0.0)}
    d_b = stop_distance_along_shape(0.01, 0.0, _STRAIGHT_LINE)

    matched = _matched_df([
        ("t1", _t(0), 0.0),
        ("t1", _t(10), d_b),
    ])

    segment_times, counts = collect_segment_observations(
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC", skip_first_segment=False
    )

    assert segment_times == {}
    assert counts["rejected_seg_time"] == 1
    assert counts["segments_observed"] == 0


def test_collect_normal_urban_speed_passes_unchanged():
    """~9 m/s (typical urban bus/tram) must pass through unaffected by FA-13."""
    idx = _two_stop_static_index()
    trip_shapes = {"t1": "shape1"}
    shapes = {"shape1": _STRAIGHT_LINE}
    stop_locations = {"A": (0.0, 0.0), "B": (0.01, 0.0)}
    d_b = stop_distance_along_shape(0.01, 0.0, _STRAIGHT_LINE)

    matched = _matched_df([
        ("t1", _t(0), 0.0),
        ("t1", _t(123), d_b),
    ])

    segment_times, counts = collect_segment_observations(
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC", skip_first_segment=False
    )

    key = ("R1", "0", "A", "B", "WEEKDAY", time_bucket_for_seconds(0, 120))
    assert segment_times[key] == pytest.approx([123.0])
    assert counts["rejected_seg_time"] == 0
    assert counts["segments_observed"] == 1


def test_collect_slow_but_moving_segment_is_kept():
    """Heavy traffic must not be mistaken for a stopped vehicle.

    Originally written for FA-13, whose speed check is upper-bound only. FA-18 added a
    LOWER bound, so the old claim ("<1 m/s must NOT be rejected") is no longer true and
    this test now guards the narrower invariant it always really covered: a segment that
    is slow but genuinely moving survives both bounds.

    Margin over the bounds is deliberate and worth keeping explicit: 1112 m / 1200 s =
    0.927 m/s, i.e. 3.34 km/h, which is 1.67x _MIN_PLAUSIBLE_SPEED_MPS (2 km/h). If a
    future recalibration raises that threshold past 3.34 km/h this test goes red - and
    the failure would point at the threshold, not at a regression here, so re-time the
    fixture rather than "fixing" the filter.

    Uses max_bracket_gap_s=None since a 20-minute gap between the two real GPS
    observations would otherwise be caught by FA-14's bracket-gap check first (unrelated
    to the speed checks under test here) - same isolation technique as
    test_collect_rejects_implausible_segment_time.
    """
    idx = _two_stop_static_index()
    trip_shapes = {"t1": "shape1"}
    shapes = {"shape1": _STRAIGHT_LINE}
    stop_locations = {"A": (0.0, 0.0), "B": (0.01, 0.0)}
    d_b = stop_distance_along_shape(0.01, 0.0, _STRAIGHT_LINE)

    matched = _matched_df([
        ("t1", _t(0), 0.0),
        ("t1", _t(1200), d_b),
    ])

    segment_times, counts = collect_segment_observations(
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC", skip_first_segment=False, max_bracket_gap_s=None
    )

    key = ("R1", "0", "A", "B", "WEEKDAY", time_bucket_for_seconds(0, 120))
    assert segment_times[key] == pytest.approx([1200.0])
    assert counts["rejected_seg_time"] == 0
    assert counts["rejected_stationary"] == 0
    assert counts["segments_observed"] == 1


def test_collect_empty_matched_dataframe():
    idx = _two_stop_static_index()
    matched = _matched_df([])
    segment_times, counts = collect_segment_observations(
        matched, idx, {}, {}, {}, agency_tz="UTC", skip_first_segment=False
    )
    assert segment_times == {}
    assert counts["trips_processed"] == 0


# ---------------------------------------------------------------------------
# collect_segment_observations - shape_dist_traveled trust (FA-10)
# ---------------------------------------------------------------------------


def test_collect_trusted_stop_dist_bypasses_geometric_anchoring():
    """A trusted stop_time distance must be used directly for interpolation,
    not the geometric projection stop_distance_along_shape would derive from
    stop_locations. Chosen deliberately different from the geometric distance
    so the two are distinguishable in the resulting segment time.
    """
    idx = _two_stop_static_index()
    trip_shapes = {"t1": "shape1"}
    shapes = {"shape1": _STRAIGHT_LINE}
    stop_locations = {"A": (0.0, 0.0), "B": (0.01, 0.0)}
    geometric_d_b = stop_distance_along_shape(0.01, 0.0, _STRAIGHT_LINE)
    trusted_d_b = geometric_d_b + 200.0  # deliberately different from the geometric value

    matched = _matched_df([
        ("t1", _t(0), 0.0),
        ("t1", _t(100), trusted_d_b),
    ])

    trusted_stop_dist = {("t1", 1): 0.0, ("t1", 2): trusted_d_b}
    segment_times, counts = collect_segment_observations(
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC", skip_first_segment=False,
        trusted_stop_dist=trusted_stop_dist,
    )

    key = ("R1", "0", "A", "B", "WEEKDAY", time_bucket_for_seconds(0, 120))
    assert segment_times[key] == pytest.approx([100.0])
    assert counts["segments_observed"] == 1


def test_collect_shape_cumulative_dist_and_trusted_stop_dist_omitted_matches_default():
    """Fallback-parity regression: omitting both new FA-10 params must reproduce
    exactly today's fully-geometric output - the hard "byte-identical fallback"
    constraint, checked at collect_segment_observations's own level.
    """
    idx = _two_stop_static_index()
    trip_shapes = {"t1": "shape1"}
    shapes = {"shape1": _STRAIGHT_LINE}
    stop_locations = {"A": (0.0, 0.0), "B": (0.01, 0.0)}
    d_b = stop_distance_along_shape(0.01, 0.0, _STRAIGHT_LINE)

    matched = _matched_df([
        ("t1", _t(0), 0.0),
        ("t1", _t(100), d_b),
    ])

    without_params, counts_without = collect_segment_observations(
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC", skip_first_segment=False
    )
    with_none_params, counts_with_none = collect_segment_observations(
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC", skip_first_segment=False,
        shape_cumulative_dist=None, trusted_stop_dist=None,
    )

    assert without_params == with_none_params
    assert counts_without == counts_with_none


# ---------------------------------------------------------------------------
# collect_segment_observations - sequential monotonic stop-pattern resolution (FA-11)
# ---------------------------------------------------------------------------


def test_collect_late_stop_resolves_to_late_pass_reproducing_poznan_route151_pattern():
    """End-to-end reproduction of the Poznań route 151 / shape 154679 case (FA-11
    handoff): a 3-stop trip on an out-and-back loop, whose last stop sits on a
    coordinate the shape's polyline also visits much earlier. Independent per-stop
    resolution (today's pre-FA-11 bug) would anchor that last stop to the EARLY
    pass, making the vehicle appear to reach it before the middle stop - a negative,
    rejected segment time. FA-11's sequential resolver must anchor it to the LATE
    pass instead, giving a small, plausible segment time.
    """
    cumulative = cumulative_distances(_LOOP_LINE)
    idx = _make_static_index(
        trips={"t1": ("R151", "0")},
        stops={"t1": [(1, "A", 0, 0), (2, "B", 0, 0), (3, "C", 0, 0)]},
    )
    trip_shapes = {"t1": "loop_shape"}
    shapes = {"loop_shape": _LOOP_LINE}
    stop_locations = {"A": (0.0, 0.0), "B": (0.02, 0.0), "C": (0.01, 0.0)}

    # Real vehicle trajectory: passes A at t=0, B (the turnaround) at t=200, then C
    # (the duplicated point) at t=260 - a genuine, correctly-ordered, monotonic
    # position series matching the shape's own late pass through that coordinate.
    matched = _matched_df([
        ("t1", _t(0), cumulative[0]),
        ("t1", _t(200), cumulative[2]),
        ("t1", _t(260), cumulative[3]),
        ("t1", _t(320), cumulative[4]),
    ])

    segment_times, counts = collect_segment_observations(
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC", skip_first_segment=False
    )

    key_ab = ("R151", "0", "A", "B", "WEEKDAY", time_bucket_for_seconds(0, 120))
    key_bc = ("R151", "0", "B", "C", "WEEKDAY", time_bucket_for_seconds(200, 120))
    assert segment_times[key_ab] == pytest.approx([200.0])
    assert segment_times[key_bc] == pytest.approx([60.0])
    assert counts["segments_observed"] == 2
    assert counts["rejected_seg_time"] == 0


def test_collect_pattern_cache_keyed_by_shape_and_stop_pattern_not_bare_shape_id():
    """Two trips share one shape_id but differ in stop pattern - an express variant
    skipping a middle stop the local variant serves. Deliberately overlapping
    stop_sequence numbers (both trips have a seq=2, but it means a different
    physical stop in each) so that a cache keyed by bare shape_id alone would leak
    the local trip's seq=2 (the middle stop M) into the express trip's own seq=2
    (stop B) lookup. The pattern-keyed cache (FA-11) must keep them independent.
    """
    cumulative = cumulative_distances(_STRAIGHT_LINE)
    idx = _make_static_index(
        trips={"t_local": ("RL", "0"), "t_express": ("RX", "0")},
        stops={
            "t_local": [(1, "A", 0, 0), (2, "M", 0, 0), (3, "B", 0, 0)],
            "t_express": [(1, "A", 0, 0), (2, "B", 0, 0)],  # seq=2 is B here, not M
        },
    )
    trip_shapes = {"t_local": "shared_shape", "t_express": "shared_shape"}
    shapes = {"shared_shape": _STRAIGHT_LINE}
    stop_locations = {"A": (0.0, 0.0), "M": (0.01, 0.0), "B": (0.02, 0.0)}

    # t_local processed first, populating the cache under a leak-prone key if the
    # cache key were bare "shared_shape" alone.
    matched = _matched_df([
        ("t_local", _t(0), cumulative[0]),
        ("t_local", _t(100), cumulative[1]),
        ("t_local", _t(200), cumulative[2]),
        ("t_express", _t(300), cumulative[0]),
        ("t_express", _t(400), cumulative[2]),
    ])

    segment_times, _counts = collect_segment_observations(
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC", skip_first_segment=False
    )

    key_express_ab = ("RX", "0", "A", "B", "WEEKDAY", time_bucket_for_seconds(300, 120))
    # If the cache leaked t_local's seq=2 (M's distance) into t_express's own seq=2
    # (B), this segment would resolve to a fractional ~50s instead of the correct
    # 100s (B's own true crossing, matching the vehicle's real 300->400 timing).
    assert segment_times[key_express_ab] == pytest.approx([100.0])


def test_collect_fully_trusted_trip_never_calls_resolve_stop_distances_for_pattern(monkeypatch):
    """Documents/guards the all-or-nothing-per-trip design decision FA-11's branch
    logic relies on (shape_dist.evaluate_trip_trust never trusts a subset of a
    trip's stops): a fully trusted trip must skip the new sequential resolver
    entirely, not just project_point_to_polyline (already proven not to be called,
    by test_shape_dist.py's own _boom guards on that function).
    """
    def _boom(*args, **kwargs):
        raise AssertionError("resolve_stop_distances_for_pattern must not be called for a fully trusted trip")

    monkeypatch.setattr("family_a.segment_stats.resolve_stop_distances_for_pattern", _boom)

    idx = _two_stop_static_index()
    trip_shapes = {"t1": "shape1"}
    shapes = {"shape1": _STRAIGHT_LINE}
    stop_locations = {"A": (0.0, 0.0), "B": (0.01, 0.0)}
    trusted_d_b = 5000.0  # deliberately far from the geometric value

    # 200s, not 100s: 5000m in 100s implies 50 m/s, over FA-13's
    # _MAX_PLAUSIBLE_SPEED_MPS (100 km/h ~= 27.78 m/s) - 200s keeps the implied speed
    # (25 m/s) under it, unrelated to what this test actually guards (the resolver call).
    matched = _matched_df([
        ("t1", _t(0), 0.0),
        ("t1", _t(200), trusted_d_b),
    ])

    trusted_stop_dist = {("t1", 1): 0.0, ("t1", 2): trusted_d_b}
    segment_times, counts = collect_segment_observations(
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC", skip_first_segment=False,
        trusted_stop_dist=trusted_stop_dist,
    )

    key = ("R1", "0", "A", "B", "WEEKDAY", time_bucket_for_seconds(0, 120))
    assert segment_times[key] == pytest.approx([200.0])
    assert counts["segments_observed"] == 1


# ---------------------------------------------------------------------------
# collect_segment_observations - recording_date grouping (FA-6)
# ---------------------------------------------------------------------------


def test_collect_same_recording_date_still_merges_correctly():
    """Control case: two observations with an identical recording_date
    interpolate as one series, exactly like the pre-FA-6 behaviour - the
    grouping key change must not affect a single-day recording.
    """
    idx = _two_stop_static_index()
    trip_shapes = {"t1": "shape1"}
    shapes = {"shape1": _STRAIGHT_LINE}
    stop_locations = {"A": (0.0, 0.0), "B": (0.01, 0.0)}
    d_b = stop_distance_along_shape(0.01, 0.0, _STRAIGHT_LINE)

    matched = _matched_df([
        ("t1", _t(0), 0.0, date(2026, 1, 1)),
        ("t1", _t(100), d_b, date(2026, 1, 1)),
    ])

    segment_times, counts = collect_segment_observations(
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC", skip_first_segment=False
    )

    key = ("R1", "0", "A", "B", "WEEKDAY", time_bucket_for_seconds(0, 120))
    assert segment_times[key] == pytest.approx([100.0])
    assert counts["segments_observed"] == 1


def test_collect_prevents_cross_day_bracketing_with_close_timestamps():
    """Core FA-6 regression test: same trip_id observed on two different
    recording_dates only 50 seconds apart by raw timestamp (deliberately NOT
    >2h apart, so _MAX_PLAUSIBLE_SEG_TIME_S cannot catch this by accident -
    the regression must be prevented by the grouping key itself). Under the
    pre-FA-6 bare-trip_id grouping this pair would bracket stop B's distance
    and silently produce a bogus ~50s segment mixing two unrelated days.

    Grouping by (trip_id, recording_date) means each day is processed as its
    own independent attempt at the trip's one stop pair (A->B) - each day's
    single-point series can't bracket either stop, so each day contributes
    its own interpolation gap (2 total), not one combined gap.
    """
    idx = _two_stop_static_index()
    trip_shapes = {"t1": "shape1"}
    shapes = {"shape1": _STRAIGHT_LINE}
    stop_locations = {"A": (0.0, 0.0), "B": (0.01, 0.0)}
    d_b = stop_distance_along_shape(0.01, 0.0, _STRAIGHT_LINE)

    matched = _matched_df([
        ("t1", _t(0), 0.0, date(2026, 1, 1)),
        ("t1", _t(50), d_b, date(2026, 1, 2)),
    ])

    segment_times, counts = collect_segment_observations(
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC", skip_first_segment=False
    )

    assert segment_times == {}
    assert counts["segments_observed"] == 0
    assert counts["rejected_seg_time"] == 0
    assert counts["interpolation_gaps"] == 2


def test_collect_without_recording_date_column_groups_by_trip_id_only():
    """Backward-compat case: a matched table with no recording_date column at
    all (as produced by a pre-FA-6 single-directory match run) must still
    group by bare trip_id and interpolate successfully, reproducing this
    function's pre-FA-6 behaviour exactly.
    """
    idx = _two_stop_static_index()
    trip_shapes = {"t1": "shape1"}
    shapes = {"shape1": _STRAIGHT_LINE}
    stop_locations = {"A": (0.0, 0.0), "B": (0.01, 0.0)}
    d_b = stop_distance_along_shape(0.01, 0.0, _STRAIGHT_LINE)

    matched = _matched_df([
        ("t1", _t(0), 0.0),
        ("t1", _t(50), d_b),
    ])
    assert "recording_date" not in matched.columns

    segment_times, counts = collect_segment_observations(
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC", skip_first_segment=False
    )

    assert counts["segments_observed"] == 1
    assert counts["interpolation_gaps"] == 0


# ---------------------------------------------------------------------------
# filter_min_observations
# ---------------------------------------------------------------------------


def test_filter_min_observations_drops_under_threshold():
    segment_times = {
        ("R1", "0", "A", "B"): [10.0],
        ("R1", "0", "B", "C"): [10.0, 20.0],
        ("R1", "0", "C", "D"): [10.0, 20.0, 30.0],
    }
    filtered, dropped = filter_min_observations(segment_times, min_observations=2)
    assert set(filtered.keys()) == {("R1", "0", "B", "C"), ("R1", "0", "C", "D")}
    assert dropped == 1


def test_filter_min_observations_keeps_all_when_threshold_is_one():
    segment_times = {("R1", "0", "A", "B"): [10.0]}
    filtered, dropped = filter_min_observations(segment_times, min_observations=1)
    assert filtered == segment_times
    assert dropped == 0


# ---------------------------------------------------------------------------
# FA-17/FA-20: drop the first stop pair of every trip, whatever its position signal
# ---------------------------------------------------------------------------


def _three_stop_static_index() -> StaticIndex:
    return _make_static_index(
        trips={"t1": ("R1", "0")},
        stops={"t1": [(1, "A", 0, 0), (2, "B", 100, 100), (3, "C", 200, 200)]},
    )


def _three_stop_inputs():
    """Straight-line trip A->B->C with one observation at each stop, 100s apart."""
    stop_locations = {"A": (0.0, 0.0), "B": (0.01, 0.0), "C": (0.02, 0.0)}
    d_b = stop_distance_along_shape(0.01, 0.0, _STRAIGHT_LINE)
    d_c = stop_distance_along_shape(0.02, 0.0, _STRAIGHT_LINE)
    rows = [("t1", _t(0), 0.0), ("t1", _t(100), d_b), ("t1", _t(200), d_c)]
    return _three_stop_static_index(), {"t1": "shape1"}, {"shape1": _STRAIGHT_LINE}, stop_locations, rows


_BUCKET = time_bucket_for_seconds(0, 120)
_KEY_AB = ("R1", "0", "A", "B", "WEEKDAY", _BUCKET)
_KEY_BC = ("R1", "0", "B", "C", "WEEKDAY", _BUCKET)


def _collect_with_signal(signal, **kwargs):
    idx, trip_shapes, shapes, stop_locations, rows = _three_stop_inputs()
    matched = _matched_df(rows)
    if signal is not None:
        matched["position_signal"] = signal
    return collect_segment_observations(
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC", **kwargs
    )


def test_first_segment_skipped_when_position_signal_is_none():
    segment_times, counts = _collect_with_signal("none")

    assert _KEY_AB not in segment_times  # the layover-contaminated pair
    assert segment_times[_KEY_BC] == pytest.approx([100.0])  # the rest survives
    assert counts["first_segment_skipped"] == 1
    assert counts["segments_observed"] == 1


@pytest.mark.parametrize("signal", ["sequence", "stop_id"])
def test_first_segment_skipped_when_position_signal_is_usable(signal):
    """FA-20 reversal: FA-17 KEPT this pair, on the theory that an FA-12 window prevents the
    artifact. Rome and Szczecin at 100% sequence coverage have the worst first pairs measured
    (2.5 and 5.0 km/h against ~19 and ~21 km/h mid-trip), so the window does not prevent it.
    """
    segment_times, counts = _collect_with_signal(signal)

    assert _KEY_AB not in segment_times
    assert segment_times[_KEY_BC] == pytest.approx([100.0])
    assert counts["first_segment_skipped"] == 1
    assert counts["segments_observed"] == 1


def test_first_segment_skipped_when_signal_column_absent():
    """FA-20 reversal, and the one that matters most: FA-17 deliberately left a pre-FA-17
    matched table (no position_signal column) untouched. Keeping that rule would leave the
    artifact in every archived table of that era, so FA-20 skips those too. Pinned explicitly
    rather than left to follow from the code, so the reversal stays visible in the diff.
    """
    segment_times, counts = _collect_with_signal(None)

    assert _KEY_AB not in segment_times
    assert segment_times[_KEY_BC] == pytest.approx([100.0])
    assert counts["first_segment_skipped"] == 1


def test_first_segment_kept_when_skip_disabled():
    """--keep-first-segment brings the first pair back intact, whatever the signal - so the
    change stays measurable and reversible from the command line.

    Precisely: it restores pre-FA-20 behaviour for a recording WITH a position signal, and
    pre-FA-17 behaviour for one without - FA-17 dropped the pair when the signal was "none",
    and this flag disables the skip outright rather than reinstating that condition. There is
    deliberately no way to get the old signal-conditional rule back.
    """
    for signal in ("none", "sequence", "stop_id", None):
        segment_times, counts = _collect_with_signal(signal, skip_first_segment=False)

        assert segment_times[_KEY_AB] == pytest.approx([100.0]), signal
        assert segment_times[_KEY_BC] == pytest.approx([100.0]), signal
        assert counts["first_segment_skipped"] == 0, signal


def test_first_segment_skipped_when_rows_of_the_trip_disagree_on_signal():
    """Two --positions-dir values can resolve to different signals. FA-17 needed an "any row
    says none" rule for that; under FA-20 the trip is skipped whatever the rows say."""
    idx, trip_shapes, shapes, stop_locations, rows = _three_stop_inputs()
    matched = _matched_df(rows)
    matched["position_signal"] = ["sequence", "none", "sequence"]

    segment_times, counts = collect_segment_observations(
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC"
    )

    assert _KEY_AB not in segment_times
    assert counts["first_segment_skipped"] == 1


def test_two_stop_trip_contributes_nothing_when_first_segment_skipped():
    """Its only pair IS the first pair - skipping must not raise or emit a segment. No
    position_signal column here on purpose: the guard is the empty range(1, 1), not a lookup."""
    idx = _two_stop_static_index()
    stop_locations = {"A": (0.0, 0.0), "B": (0.01, 0.0)}
    d_b = stop_distance_along_shape(0.01, 0.0, _STRAIGHT_LINE)
    matched = _matched_df([("t1", _t(0), 0.0), ("t1", _t(100), d_b)])

    segment_times, counts = collect_segment_observations(
        matched, idx, {"t1": "shape1"}, {"shape1": _STRAIGHT_LINE}, stop_locations, agency_tz="UTC"
    )

    assert segment_times == {}
    assert counts["first_segment_skipped"] == 1
    assert counts["segments_observed"] == 0
    assert counts["trips_processed"] == 1


# ---------------------------------------------------------------------------
# FA-18: reject observations implying a stationary vehicle
# ---------------------------------------------------------------------------


def _slow_first_segment_inputs(seconds_a_to_b: int):
    """A->B covered in seconds_a_to_b, then B->C briskly in 100s.

    A->B spans ~1112 m (0.01 degrees of latitude), so 2500 s implies ~1.6 km/h - the
    layover-on-a-terminus signature FA-18 exists to catch - while B->C stays at ~40 km/h
    to prove only the offending pair is dropped.
    """
    idx, trip_shapes, shapes, stop_locations, _rows = _three_stop_inputs()
    d_b = stop_distance_along_shape(0.01, 0.0, _STRAIGHT_LINE)
    d_c = stop_distance_along_shape(0.02, 0.0, _STRAIGHT_LINE)
    rows = [
        ("t1", _t(0), 0.0),
        ("t1", _t(seconds_a_to_b), d_b),
        ("t1", _t(seconds_a_to_b + 100), d_c),
    ]
    return idx, trip_shapes, shapes, stop_locations, _matched_df(rows), d_b


def _collect_slow(seconds_a_to_b=2500, **kwargs):
    idx, trip_shapes, shapes, stop_locations, matched, d_b = _slow_first_segment_inputs(seconds_a_to_b)
    # Two deliberate isolations, both so this block tests FA-18 and nothing else:
    # - max_bracket_gap_s=None isolates it from FA-14, whose bracket-gap rule would reject this
    #   fixture's deliberately wide observation spacing before the speed check ever runs;
    # - skip_first_segment=False isolates it from FA-20, which would otherwise drop A->B - the
    #   pair this fixture makes slow - without interpolating it at all. FA-18's production job
    #   since FA-20 is mid-trip, covered on its own by the mid-trip test below.
    segment_times, counts = collect_segment_observations(
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC",
        max_bracket_gap_s=None, skip_first_segment=False, **kwargs
    )
    return segment_times, counts, d_b


def test_stationary_observation_is_rejected():
    segment_times, counts, _d_b = _collect_slow()

    assert _KEY_AB not in segment_times          # ~1.6 km/h - standing still
    assert segment_times[_KEY_BC] == pytest.approx([100.0])  # ~40 km/h - kept
    assert counts["rejected_stationary"] == 1
    assert counts["segments_observed"] == 1


def test_normal_speed_observation_is_kept():
    segment_times, counts, _d_b = _collect_slow(seconds_a_to_b=100)

    assert segment_times[_KEY_AB] == pytest.approx([100.0])
    assert counts["rejected_stationary"] == 0


def test_none_disables_the_lower_speed_bound():
    segment_times, counts, _d_b = _collect_slow(min_plausible_speed_mps=None)

    assert segment_times[_KEY_AB] == pytest.approx([2500.0])
    assert counts["rejected_stationary"] == 0


def test_observation_exactly_at_the_threshold_is_kept():
    """The comparison is a strict `<` - exactly at the bound is not 'stationary'."""
    _st, _c, d_b = _collect_slow()
    exact = d_b / 2500.0  # the fixture's own implied speed, to the last bit

    segment_times, counts, _ = _collect_slow(min_plausible_speed_mps=exact)

    assert segment_times[_KEY_AB] == pytest.approx([2500.0])
    assert counts["rejected_stationary"] == 0


def test_stationary_rejection_is_disjoint_from_rejected_seg_time():
    """FA-15/FA-16 lesson: a counter mixing causes cannot be acted on."""
    _segment_times, counts, _d_b = _collect_slow()

    assert counts["rejected_stationary"] == 1
    assert counts["rejected_seg_time"] == 0


def test_fa20_skip_wins_over_fa18_on_a_stationary_first_pair():
    """Both mechanisms target the terminus-layover artifact; this locks the division of work.

    A trip whose first pair is stationary must be counted as skipped (FA-20, which never
    attempts the pair) and NOT as a stationary rejection (FA-18, which would have to
    interpolate it first). Without this, a future reordering could double-count the same
    artifact in two counters that are supposed to be disjoint. Run at production defaults -
    no position_signal set, since FA-20 no longer consults one.
    """
    idx, trip_shapes, shapes, stop_locations, matched, _d_b = _slow_first_segment_inputs(2500)

    segment_times, counts = collect_segment_observations(
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC", max_bracket_gap_s=None
    )

    assert counts["first_segment_skipped"] == 1
    assert counts["rejected_stationary"] == 0
    assert _KEY_AB not in segment_times
    assert segment_times[_KEY_BC] == pytest.approx([100.0])


def test_stationary_mid_trip_pair_is_still_rejected_at_production_defaults():
    """FA-18's remaining job since FA-20: the first pair is gone before it is interpolated, so
    what this bound catches is a vehicle standing still LATER in the trip - a mid-route
    terminus, a driver break, or a layover spilling past stop 2. Nothing else in this block
    runs at defaults, so without this the production configuration would be untested.
    """
    idx, trip_shapes, shapes, stop_locations, _rows = _three_stop_inputs()
    d_b = stop_distance_along_shape(0.01, 0.0, _STRAIGHT_LINE)
    d_c = stop_distance_along_shape(0.02, 0.0, _STRAIGHT_LINE)
    # A->B brisk (and skipped anyway), B->C ~1112 m in 2500 s = ~1.6 km/h.
    matched = _matched_df([("t1", _t(0), 0.0), ("t1", _t(100), d_b), ("t1", _t(2600), d_c)])

    segment_times, counts = collect_segment_observations(
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC", max_bracket_gap_s=None
    )

    assert segment_times == {}
    assert counts["first_segment_skipped"] == 1
    assert counts["rejected_stationary"] == 1
    assert counts["segments_observed"] == 0


# ---------------------------------------------------------------------------
# collect_stop_crossings - the per-stop view the chart layer consumes
# ---------------------------------------------------------------------------

# Four collinear points, so a 4-stop trip has three segments to classify independently.
_LONG_LINE = [(0.0, 0.0), (0.01, 0.0), (0.02, 0.0), (0.03, 0.0)]
_LONG_LATS = (0.0, 0.01, 0.02, 0.03)


def _four_stop_inputs(observation_seconds):
    """A->B->C->D on a straight line, one observation at each stop's own distance.

    *observation_seconds* gives the timestamp at which the vehicle is seen at each stop, so a
    caller can make any individual segment fast, slow or stationary.
    """
    idx = _make_static_index(
        trips={"t1": ("R1", "0")},
        stops={"t1": [(1, "A", 0, 0), (2, "B", 100, 100), (3, "C", 200, 200), (4, "D", 300, 300)]},
    )
    stop_locations = {name: (lat, 0.0) for name, lat in zip("ABCD", _LONG_LATS)}
    distances = [stop_distance_along_shape(lat, 0.0, _LONG_LINE) for lat in _LONG_LATS]
    matched = _matched_df([("t1", _t(s), d) for s, d in zip(observation_seconds, distances)])
    return idx, {"t1": "shape1"}, {"shape1": _LONG_LINE}, stop_locations, matched


def _segments_from_crossings(crossings, agency_tz="UTC", bucket_minutes=120):
    """Rebuild collect_segment_observations' output from a crossings table.

    Deliberately re-derives the segment key here rather than importing a shared helper: if
    both sides used the same private code path, the equivalence test below would prove nothing
    about the thing it exists to protect.
    """
    zone = zoneinfo.ZoneInfo(agency_tz)
    out = defaultdict(list)
    # dropna=False: recording_date is all-None for a pre-FA-6 matched table, and pandas'
    # default would silently discard every group.
    for _key, group in crossings.groupby(["trip_id", "recording_date"], dropna=False, sort=False):
        rows = list(group.sort_values("stop_sequence").itertuples())
        for prev, cur in zip(rows, rows[1:]):
            if cur.seg_status != SEG_OK:
                continue
            local_from = prev.obs_time.tz_convert(zone)
            # day_type still comes from the observation; the bucket comes from the previous
            # stop's SCHEDULED departure (D3), which the crossings table carries as sched_dep_s.
            key = segment_key_for(
                cur.route_id, cur.direction_id, prev.stop_id, cur.stop_id,
                day_type_for_date(local_from.date()),
                time_bucket_for_seconds(prev.sched_dep_s, bucket_minutes),
            )
            out[key].append(cur.seg_time_s)
    return dict(out)


@pytest.mark.parametrize("skip_first", [True, False])
@pytest.mark.parametrize("max_gap", [None, 300.0])
@pytest.mark.parametrize(
    "observation_seconds",
    [
        (0, 100, 200, 300),      # everything brisk
        (0, 100, 2600, 2700),    # B->C stationary (~1.6 km/h over ~1112 m)
        (0, 100, 200, 100_000),  # C->D longer than FA-13's 2h ceiling
        (0, 100, 110, 120),      # C->D ~1112 m in 10 s = 400 km/h, over FA-13's speed ceiling
        (0, 400, 800, 1200),     # brackets 400 s apart - rejected by FA-14 at max_gap=300
    ],
)
def test_crossings_reproduce_collect_segment_observations(
    observation_seconds, skip_first, max_gap
):
    """The anti-drift guard for the chart layer.

    collect_stop_crossings exists so charts do not re-implement FA-13/FA-18/FA-20 and quietly
    diverge from the aggregation path. That promise is only worth something if it is checked:
    differencing the accepted crossings must reproduce collect_segment_observations exactly,
    for every combination of filter outcomes this fixture can produce.
    """
    idx, trip_shapes, shapes, stop_locations, matched = _four_stop_inputs(observation_seconds)
    # max_gap is parametrised rather than pinned at None: FA-14 rejects a bracketing pair on a
    # per-CALL basis, and this function makes half as many calls as the original, so it is
    # exactly the filter most likely to drift between the two. Leaving it disabled would have
    # left that untested.
    common = dict(max_bracket_gap_s=max_gap, skip_first_segment=skip_first)

    expected, _counts = collect_segment_observations(
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC", **common
    )
    crossings, _ccounts = collect_stop_crossings(
        matched, idx, trip_shapes, shapes, stop_locations, **common
    )

    derived = _segments_from_crossings(crossings)
    assert set(derived) == set(expected)
    for key, values in expected.items():
        assert derived[key] == pytest.approx(values)


def test_crossings_reproduce_collect_segment_observations_with_a_missing_stop_location():
    """A stop absent from stops.txt is a static-feed defect both functions must treat alike."""
    idx, trip_shapes, shapes, stop_locations, matched = _four_stop_inputs((0, 100, 200, 300))
    stop_locations = {k: v for k, v in stop_locations.items() if k != "C"}
    common = dict(max_bracket_gap_s=None, skip_first_segment=False)

    expected, _counts = collect_segment_observations(
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC", **common
    )
    crossings, counts = collect_stop_crossings(
        matched, idx, trip_shapes, shapes, stop_locations, **common
    )

    assert _segments_from_crossings(crossings) == pytest.approx(expected)
    assert counts["missing_stop_location"] == 2      # B->C and C->D both lose an endpoint


def test_crossings_keep_one_row_per_scheduled_stop_including_misses():
    """Coverage is the point: a stop that could not be interpolated must still have a row,
    otherwise the recording-window edge bias is invisible to the consumer."""
    idx, trip_shapes, shapes, stop_locations, _m = _four_stop_inputs((0, 100, 200, 300))
    # Vehicle only ever observed around A and B - C and D are never bracketed.
    d_b = stop_distance_along_shape(0.01, 0.0, _LONG_LINE)
    matched = _matched_df([("t1", _t(0), 0.0), ("t1", _t(100), d_b)])

    crossings, counts = collect_stop_crossings(
        matched, idx, trip_shapes, shapes, stop_locations, max_bracket_gap_s=None
    )

    assert len(crossings) == 4                      # one row per scheduled stop
    assert counts["stops_total"] == 4
    assert counts["stops_crossed"] == 2             # only A and B
    assert crossings.obs_time.isna().sum() == 2     # C and D are NaT, not absent
    assert list(crossings.seg_status) == [SEG_NO_PREVIOUS, SEG_FIRST_PAIR, SEG_GAP, SEG_GAP]


def test_crossings_label_rejections_instead_of_dropping_them():
    """The semantic difference from collect_segment_observations, pinned.

    A stationary segment is LABELLED here and dropped there. A consumer doing headway or
    punctuality analysis legitimately wants the row; only the aggregation path must lose it.
    """
    idx, trip_shapes, shapes, stop_locations, matched = _four_stop_inputs((0, 100, 2600, 2700))

    crossings, counts = collect_stop_crossings(
        matched, idx, trip_shapes, shapes, stop_locations, max_bracket_gap_s=None
    )

    by_stop = dict(zip(crossings.stop_id, crossings.seg_status))
    assert by_stop["A"] == SEG_NO_PREVIOUS
    assert by_stop["B"] == SEG_FIRST_PAIR          # FA-20
    assert by_stop["C"] == SEG_STATIONARY          # FA-18, kept as a row
    assert by_stop["D"] == SEG_OK                  # accepted
    assert counts["segments_rejected_stationary"] == 1
    assert counts["segments_accepted"] == 1
    # The stationary row still carries its measurement - that is what makes it usable.
    stationary = crossings[crossings.stop_id == "C"].iloc[0]
    assert stationary.seg_time_s == pytest.approx(2500.0)
    assert pd.notna(stationary.obs_time)


def test_crossings_first_pair_label_follows_skip_first_segment():
    idx, trip_shapes, shapes, stop_locations, matched = _four_stop_inputs((0, 100, 200, 300))

    kept, counts = collect_stop_crossings(
        matched, idx, trip_shapes, shapes, stop_locations,
        max_bracket_gap_s=None, skip_first_segment=False,
    )

    assert SEG_FIRST_PAIR not in set(kept.seg_status)
    assert counts["segments_first_pair"] == 0
    assert counts["segments_accepted"] == 3


def test_crossings_empty_matched_returns_typed_empty_frame():
    """An empty input must still produce the full schema - a consumer that selects columns
    should not have to special-case the no-data day."""
    idx, trip_shapes, shapes, stop_locations, _m = _four_stop_inputs((0, 100, 200, 300))

    crossings, counts = collect_stop_crossings(
        pd.DataFrame(columns=["trip_id", "timestamp", "distance_along_shape_m"]),
        idx, trip_shapes, shapes, stop_locations,
    )

    assert crossings.empty
    assert "seg_status" in crossings.columns
    assert "obs_time" in crossings.columns
    assert counts["trips_processed"] == 0


def test_crossings_two_stop_trip_yields_two_rows_and_no_segment():
    idx = _two_stop_static_index()
    stop_locations = {"A": (0.0, 0.0), "B": (0.01, 0.0)}
    d_b = stop_distance_along_shape(0.01, 0.0, _STRAIGHT_LINE)
    matched = _matched_df([("t1", _t(0), 0.0), ("t1", _t(100), d_b)])

    crossings, counts = collect_stop_crossings(
        matched, idx, {"t1": "shape1"}, {"shape1": _STRAIGHT_LINE}, stop_locations
    )

    assert list(crossings.seg_status) == [SEG_NO_PREVIOUS, SEG_FIRST_PAIR]
    assert counts["segments_accepted"] == 0
    assert counts["trips_processed"] == 1
