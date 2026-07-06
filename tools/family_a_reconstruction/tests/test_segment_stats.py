"""Unit tests for family_a.segment_stats (FA-3).

No QGIS, no network - pure stdlib + pandas + pytest.
Run: pytest tests/test_segment_stats.py -v
"""

import statistics
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from family_a.build_gtfs import StaticIndex
from family_a.interpolate import stop_distance_along_shape
from family_a.segment_stats import (
    aggregate_segments,
    collect_segment_observations,
    filter_min_observations,
)

# Same straight north-south line used in test_matcher.py / test_interpolate.py.
_STRAIGHT_LINE = [(0.0, 0.0), (0.01, 0.0), (0.02, 0.0)]

_T0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def _t(seconds: int) -> datetime:
    return _T0 + timedelta(seconds=seconds)


def _make_static_index(trips, stops) -> StaticIndex:
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


def _matched_df(rows: list[tuple[str, datetime, float]]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["trip_id", "timestamp", "distance_along_shape_m"])
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

    segment_times, counts = collect_segment_observations(matched, idx, trip_shapes, shapes, stop_locations)

    key = ("R1", "0", "A", "B")
    assert segment_times[key] == pytest.approx([100.0])
    assert counts["trips_processed"] == 1
    assert counts["segments_observed"] == 1
    assert counts["interpolation_gaps"] == 0
    assert counts["rejected_seg_time"] == 0


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

    segment_times, counts = collect_segment_observations(matched, idx, trip_shapes, shapes, stop_locations)

    assert segment_times == {}
    assert counts["interpolation_gaps"] == 1
    assert counts["segments_observed"] == 0


def test_collect_trip_with_no_resolvable_shape_is_skipped():
    idx = _two_stop_static_index()
    trip_shapes: dict[str, str] = {}  # no shape for t1
    shapes: dict[str, list] = {}
    stop_locations = {"A": (0.0, 0.0), "B": (0.01, 0.0)}

    matched = _matched_df([("t1", _t(0), 0.0), ("t1", _t(100), 100.0)])
    segment_times, counts = collect_segment_observations(matched, idx, trip_shapes, shapes, stop_locations)

    assert segment_times == {}
    assert counts["trips_processed"] == 0
    assert counts["trips_skipped_unresolvable"] == 1


def test_collect_trip_with_fewer_than_two_stops_is_skipped():
    idx = _make_static_index(trips={"t1": ("R1", "0")}, stops={"t1": [(1, "A", 0, 0)]})
    trip_shapes = {"t1": "shape1"}
    shapes = {"shape1": _STRAIGHT_LINE}
    stop_locations = {"A": (0.0, 0.0)}

    matched = _matched_df([("t1", _t(0), 0.0)])
    segment_times, counts = collect_segment_observations(matched, idx, trip_shapes, shapes, stop_locations)

    assert segment_times == {}
    assert counts["trips_processed"] == 0
    assert counts["trips_skipped_unresolvable"] == 1


def test_collect_missing_stop_location_is_distinct_from_interpolation_gap():
    idx = _two_stop_static_index()
    trip_shapes = {"t1": "shape1"}
    shapes = {"shape1": _STRAIGHT_LINE}
    stop_locations = {"A": (0.0, 0.0)}  # B missing

    matched = _matched_df([("t1", _t(0), 0.0), ("t1", _t(100), 100.0)])
    segment_times, counts = collect_segment_observations(matched, idx, trip_shapes, shapes, stop_locations)

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

    # 3 hours between the two observations -> derived segment time > 7200s.
    matched = _matched_df([
        ("t1", _t(0), 0.0),
        ("t1", _t(3 * 3600), d_b),
    ])

    segment_times, counts = collect_segment_observations(matched, idx, trip_shapes, shapes, stop_locations)

    assert segment_times == {}
    assert counts["rejected_seg_time"] == 1
    assert counts["segments_observed"] == 0


def test_collect_empty_matched_dataframe():
    idx = _two_stop_static_index()
    matched = _matched_df([])
    segment_times, counts = collect_segment_observations(matched, idx, {}, {}, {})
    assert segment_times == {}
    assert counts["trips_processed"] == 0


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
