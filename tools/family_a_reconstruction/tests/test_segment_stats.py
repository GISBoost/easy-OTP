"""Unit tests for family_a.segment_stats (FA-3).

No QGIS, no network - pure stdlib + pandas + pytest.
Run: pytest tests/test_segment_stats.py -v
"""

import statistics
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from family_a.build_gtfs import StaticIndex
from family_a.calendar_scope import time_bucket_for_seconds
from family_a.interpolate import stop_distance_along_shape
from family_a.matcher import cumulative_distances
from family_a.segment_stats import (
    aggregate_segments,
    collect_segment_observations,
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
    expected_p85 = statistics.quantiles(values, n=100)[84]
    assert p85[key] == pytest.approx(expected_p85)


def test_aggregate_single_observation():
    key = ("R1", "0", "A", "B")
    p50, p85 = aggregate_segments({key: [42.0]})
    assert p50[key] == pytest.approx(42.0)
    assert p85[key] == pytest.approx(42.0)


# ---------------------------------------------------------------------------
# collect_segment_observations
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

    segment_times, counts = collect_segment_observations(matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC")

    # _t(0) = 2026-01-01T00:00:00 UTC, a Thursday -> WEEKDAY, bucket 0
    key = ("R1", "0", "A", "B", "WEEKDAY", time_bucket_for_seconds(0, 120))
    assert segment_times[key] == pytest.approx([100.0])
    assert counts["trips_processed"] == 1
    assert counts["segments_observed"] == 1
    assert counts["interpolation_gaps"] == 0
    assert counts["rejected_seg_time"] == 0


def test_collect_segment_observations_uses_local_time_not_utc_for_day_type_and_bucket():
    # Etc/GMT-9 is UTC+9 (Etc zone sign convention is inverted), no DST. A UTC
    # Saturday 23:00 observation lands on a local Sunday 08:00 - if the
    # tz_convert step were accidentally skipped, day_type/time_bucket would
    # be derived from the naive UTC Saturday 23:00 instead.
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
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="Etc/GMT-9"
    )

    # Local: 2026-01-04 08:00:00, a Sunday.
    expected_bucket = time_bucket_for_seconds(8 * 3600, 120)
    key = ("R1", "0", "A", "B", "SUNDAY", expected_bucket)
    assert segment_times[key] == pytest.approx([100.0])

    naive_utc_bucket = time_bucket_for_seconds(23 * 3600, 120)
    assert expected_bucket != naive_utc_bucket
    wrong_key = ("R1", "0", "A", "B", "SATURDAY", naive_utc_bucket)
    assert wrong_key not in segment_times


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

    segment_times, counts = collect_segment_observations(matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC")

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
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC"
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
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC"
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
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC",
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
    segment_times, counts = collect_segment_observations(matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC")

    assert segment_times == {}
    assert counts["trips_processed"] == 0
    assert counts["trips_skipped_unresolvable"] == 1


def test_collect_trip_with_fewer_than_two_stops_is_skipped():
    idx = _make_static_index(trips={"t1": ("R1", "0")}, stops={"t1": [(1, "A", 0, 0)]})
    trip_shapes = {"t1": "shape1"}
    shapes = {"shape1": _STRAIGHT_LINE}
    stop_locations = {"A": (0.0, 0.0)}

    matched = _matched_df([("t1", _t(0), 0.0)])
    segment_times, counts = collect_segment_observations(matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC")

    assert segment_times == {}
    assert counts["trips_processed"] == 0
    assert counts["trips_skipped_unresolvable"] == 1


def test_collect_missing_stop_location_is_distinct_from_interpolation_gap():
    idx = _two_stop_static_index()
    trip_shapes = {"t1": "shape1"}
    shapes = {"shape1": _STRAIGHT_LINE}
    stop_locations = {"A": (0.0, 0.0)}  # B missing

    matched = _matched_df([("t1", _t(0), 0.0), ("t1", _t(100), 100.0)])
    segment_times, counts = collect_segment_observations(matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC")

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
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC", max_bracket_gap_s=None
    )

    assert segment_times == {}
    assert counts["rejected_seg_time"] == 1
    assert counts["segments_observed"] == 0


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
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC"
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
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC"
    )

    key = ("R1", "0", "A", "B", "WEEKDAY", time_bucket_for_seconds(0, 120))
    assert segment_times[key] == pytest.approx([123.0])
    assert counts["rejected_seg_time"] == 0
    assert counts["segments_observed"] == 1


def test_collect_very_slow_segment_not_rejected_no_lower_bound():
    """FA-13 has no lower speed bound - heavy traffic / a long dwell producing
    <1 m/s must NOT be rejected. Uses max_bracket_gap_s=None since a 20-minute
    gap between the two real GPS observations would otherwise be caught by
    FA-14's bracket-gap check first (unrelated to the speed check under test
    here) - same isolation technique as test_collect_rejects_implausible_segment_time.
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
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC", max_bracket_gap_s=None
    )

    key = ("R1", "0", "A", "B", "WEEKDAY", time_bucket_for_seconds(0, 120))
    assert segment_times[key] == pytest.approx([1200.0])
    assert counts["rejected_seg_time"] == 0
    assert counts["segments_observed"] == 1


def test_collect_empty_matched_dataframe():
    idx = _two_stop_static_index()
    matched = _matched_df([])
    segment_times, counts = collect_segment_observations(matched, idx, {}, {}, {}, agency_tz="UTC")
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
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC",
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
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC"
    )
    with_none_params, counts_with_none = collect_segment_observations(
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC",
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
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC"
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
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC"
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
        matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC",
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

    segment_times, counts = collect_segment_observations(matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC")

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

    segment_times, counts = collect_segment_observations(matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC")

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

    segment_times, counts = collect_segment_observations(matched, idx, trip_shapes, shapes, stop_locations, agency_tz="UTC")

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
