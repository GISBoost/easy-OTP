"""Unit tests for family_a.interpolate (FA-3).

No QGIS, no network - pure stdlib + pytest.
Run: pytest tests/test_interpolate.py -v
"""

from datetime import datetime, timedelta, timezone

from family_a.build_gtfs import StaticIndex
from family_a.interpolate import (
    interpolate_stop_time,
    resolve_all_trip_stop_anchors,
    resolve_stop_distances_for_pattern,
    stop_distance_along_shape,
)
from family_a.matcher import cumulative_distances, project_point_to_polyline

# Same straight north-south line used in test_matcher.py.
_STRAIGHT_LINE = [(0.0, 0.0), (0.01, 0.0), (0.02, 0.0)]

# Out-and-back loop mirroring shape 154679's exact structure (FA-11 handoff): the
# coordinate (0.01, 0.0) occurs at a low index (1) and a high index (3), an exact
# match both times.
_LOOP_LINE = [(0.0, 0.0), (0.01, 0.0), (0.02, 0.0), (0.01, 0.0), (0.0, 0.0)]

_T0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def _t(seconds: int) -> datetime:
    return _T0 + timedelta(seconds=seconds)


# ---------------------------------------------------------------------------
# stop_distance_along_shape
# ---------------------------------------------------------------------------


def test_stop_distance_along_shape_matches_projection():
    expected = project_point_to_polyline(0.005, 0.0, _STRAIGHT_LINE)[0]
    assert stop_distance_along_shape(0.005, 0.0, _STRAIGHT_LINE) == expected


def test_stop_distance_along_shape_at_first_vertex():
    assert stop_distance_along_shape(0.0, 0.0, _STRAIGHT_LINE) == 0.0


# ---------------------------------------------------------------------------
# stop_distance_along_shape - FA-10 trusted_dist_m / cumulative
# ---------------------------------------------------------------------------


def test_stop_distance_along_shape_trusted_dist_bypasses_projection(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("project_point_to_polyline must not be called when trusted_dist_m is given")

    monkeypatch.setattr("family_a.interpolate.project_point_to_polyline", _boom)
    result = stop_distance_along_shape(0.005, 0.0, _STRAIGHT_LINE, trusted_dist_m=1234.5)
    assert result == 1234.5


def test_stop_distance_along_shape_trusted_dist_none_falls_back_to_projection():
    expected = project_point_to_polyline(0.005, 0.0, _STRAIGHT_LINE)[0]
    assert stop_distance_along_shape(0.005, 0.0, _STRAIGHT_LINE, trusted_dist_m=None) == expected


def test_stop_distance_along_shape_cumulative_overrides_haversine_axis():
    # A point strictly within the second segment (not exactly on a vertex, so
    # there's no perpendicular-distance tie between segments to worry about).
    point_lat, point_lon = 0.015, 0.0
    custom_cumulative = [0.0, 500.0, 1000.0]

    with_custom = stop_distance_along_shape(
        point_lat, point_lon, _STRAIGHT_LINE, cumulative=custom_cumulative
    )
    expected_with_custom = project_point_to_polyline(
        point_lat, point_lon, _STRAIGHT_LINE, cumulative=custom_cumulative
    )[0]
    assert with_custom == expected_with_custom

    default_result = stop_distance_along_shape(point_lat, point_lon, _STRAIGHT_LINE)
    assert default_result != with_custom


# ---------------------------------------------------------------------------
# resolve_stop_distances_for_pattern (FA-11)
# ---------------------------------------------------------------------------


def test_resolve_stop_distances_for_pattern_late_stop_resolves_to_late_pass_on_duplicated_point():
    # Priority acceptance criterion (PRD FA-11): a trip whose "late in sequence" stop
    # sits on a duplicated shape coordinate must anchor to the LATE pass (index 3),
    # not the early one (index 1) that project_point_to_polyline's own independent,
    # context-free tie-break would pick in isolation (see
    # test_project_point_to_polyline_duplicate_point_on_loop_shape_ties_to_lowest_index
    # in test_matcher.py, which documents that unchanged, still-correct behaviour for
    # a single point resolved with no knowledge of its place in a trip).
    cumulative = cumulative_distances(_LOOP_LINE)
    ordered_stops = [
        ("s_start", 0.0, 0.0),  # index 0 - resolves via the unrestricted global search
        ("s_turn", 0.02, 0.0),  # index 2 - the loop's turnaround point
        ("s_late", 0.01, 0.0),  # duplicated point - must resolve to the LATE pass (index 3)
    ]

    resolved = resolve_stop_distances_for_pattern(_LOOP_LINE, ordered_stops)

    assert resolved[0] == cumulative[0]
    assert resolved[1] == cumulative[2]
    assert resolved[2] == cumulative[3]

    # Confirm this is NOT what resolving the same point independently (today's
    # per-stop behaviour, the bug FA-11 fixes) would have picked.
    independent_result = project_point_to_polyline(0.01, 0.0, _LOOP_LINE)[0]
    assert independent_result == cumulative[1]
    assert resolved[2] != independent_result


def test_resolve_stop_distances_for_pattern_first_stop_ignores_backward_tolerance():
    # The first stop has no "previous" to anchor against - must always match the
    # plain unrestricted search, regardless of backward_tolerance_m, with no
    # special-cased distance-0 assumption.
    ordered_stops = [("s0", 0.005, 0.0)]
    expected = project_point_to_polyline(0.005, 0.0, _STRAIGHT_LINE)[0]

    assert resolve_stop_distances_for_pattern(_STRAIGHT_LINE, ordered_stops, backward_tolerance_m=0.0)[0] == expected
    assert (
        resolve_stop_distances_for_pattern(_STRAIGHT_LINE, ordered_stops, backward_tolerance_m=10000.0)[0]
        == expected
    )


def test_resolve_stop_distances_for_pattern_falls_back_to_global_search_and_logs_warning(caplog):
    # A pattern where the previous stop resolves to the far end of the line, and the
    # next stop's true location is far enough "behind" that no candidate satisfies
    # even a small backward tolerance - must fall back to the unrestricted global
    # search (not crash, not raise) and log a warning identifying shape_id/trip_id/
    # stop_id, per PRD FA-11's "never a hard error" constraint.
    caplog.set_level("WARNING")
    ordered_stops = [
        ("s_far", 0.02, 0.0),  # resolves to the far end of the line
        ("s_near_start", 0.0, 0.0),  # no candidate within a 10m tolerance of the far end
    ]
    expected_fallback = project_point_to_polyline(0.0, 0.0, _STRAIGHT_LINE)[0]

    resolved = resolve_stop_distances_for_pattern(
        _STRAIGHT_LINE,
        ordered_stops,
        backward_tolerance_m=10.0,
        shape_id="shape1",
        trip_id="trip1",
    )

    assert resolved[1] == expected_fallback
    assert "shape1" in caplog.text
    assert "trip1" in caplog.text
    assert "s_near_start" in caplog.text


def test_resolve_stop_distances_for_pattern_minor_backward_step_within_tolerance_does_not_fallback(caplog):
    # Real feeds have minor, legitimate non-monotonicity at stop clusters (PRD FA-11,
    # "Ograniczenia") - a stop physically ~11m "behind" the previous one along the
    # same shape must still resolve via the restricted search, not trigger the
    # unrestricted-search fallback (well within the ~50m default tolerance).
    caplog.set_level("WARNING")
    ordered_stops = [
        ("s_a", 0.01, 0.0),  # exact match at vertex 1
        ("s_b", 0.0099, 0.0),  # ~11m "behind" s_a along the line
    ]
    expected_b = project_point_to_polyline(0.0099, 0.0, _STRAIGHT_LINE)[0]

    resolved = resolve_stop_distances_for_pattern(_STRAIGHT_LINE, ordered_stops)

    assert resolved[1] == expected_b
    assert not caplog.records


# ---------------------------------------------------------------------------
# resolve_all_trip_stop_anchors (FA-12)
# ---------------------------------------------------------------------------


def _make_static_index(trips, stops, service_ids=None) -> StaticIndex:
    """Mirrors test_segment_stats.py's own helper of the same name/shape."""
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


def test_resolve_all_trip_stop_anchors_fully_trusted_trip_bypasses_geometry(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("resolve_stop_distances_for_pattern must not be called for a fully trusted trip")

    monkeypatch.setattr("family_a.interpolate.resolve_stop_distances_for_pattern", _boom)

    static_index = _make_static_index(
        trips={"trip1": ("routeA", "0")},
        stops={"trip1": [(0, "s0", 0, 0), (1, "s1", 60, 60), (2, "s2", 120, 120)]},
    )
    trip_shapes = {"trip1": "shape1"}
    shapes = {"shape1": _STRAIGHT_LINE}
    stop_locations = {"s0": (0.0, 0.0), "s1": (0.01, 0.0), "s2": (0.02, 0.0)}
    trusted_stop_dist = {("trip1", 0): 0.0, ("trip1", 1): 1111.9, ("trip1", 2): 2223.8}

    anchors = resolve_all_trip_stop_anchors(
        static_index, trip_shapes, shapes, stop_locations, trusted_stop_dist=trusted_stop_dist
    )

    assert anchors["trip1"] == [(0, "s0", 0.0), (1, "s1", 1111.9), (2, "s2", 2223.8)]


def test_resolve_all_trip_stop_anchors_non_trusted_trip_uses_pattern_resolver():
    static_index = _make_static_index(
        trips={"tripL": ("routeA", "0")},
        stops={
            "tripL": [
                (0, "s_start", 0, 0),
                (1, "s_turn", 0, 0),
                (2, "s_late", 0, 0),
            ]
        },
    )
    trip_shapes = {"tripL": "loopshape"}
    shapes = {"loopshape": _LOOP_LINE}
    stop_locations = {"s_start": (0.0, 0.0), "s_turn": (0.02, 0.0), "s_late": (0.01, 0.0)}
    cumulative = cumulative_distances(_LOOP_LINE)

    anchors = resolve_all_trip_stop_anchors(static_index, trip_shapes, shapes, stop_locations)

    # Same acceptance criterion as
    # test_resolve_stop_distances_for_pattern_late_stop_resolves_to_late_pass_on_duplicated_point:
    # s_late must resolve to the LATE pass (index 3), not the early one (index 1) a
    # context-free projection would pick.
    assert anchors["tripL"] == [
        (0, "s_start", cumulative[0]),
        (1, "s_turn", cumulative[2]),
        (2, "s_late", cumulative[3]),
    ]


def test_resolve_all_trip_stop_anchors_omitted_args_reproduce_pattern_resolved_default():
    # Two trips (not one) so "trip_ids omitted resolves every trip in the feed" is actually
    # distinguished from "happened to resolve the only trip present".
    static_index = _make_static_index(
        trips={"trip1": ("routeA", "0"), "trip2": ("routeA", "0")},
        stops={
            "trip1": [(0, "s0", 0, 0), (1, "s1", 60, 60)],
            "trip2": [(0, "s0", 0, 0), (1, "s1", 60, 60)],
        },
    )
    trip_shapes = {"trip1": "shape1", "trip2": "shape1"}
    shapes = {"shape1": _STRAIGHT_LINE}
    stop_locations = {"s0": (0.0, 0.0), "s1": (0.01, 0.0)}

    without_params = resolve_all_trip_stop_anchors(static_index, trip_shapes, shapes, stop_locations)
    with_none = resolve_all_trip_stop_anchors(
        static_index, trip_shapes, shapes, stop_locations,
        shape_cumulative_dist=None, trusted_stop_dist=None, trip_ids=None,
    )

    assert without_params == with_none
    assert set(without_params) == {"trip1", "trip2"}
    expected_s1 = project_point_to_polyline(0.01, 0.0, _STRAIGHT_LINE)[0]
    assert without_params["trip1"][1] == (1, "s1", expected_s1)
    assert without_params["trip2"][1] == (1, "s1", expected_s1)


def test_resolve_all_trip_stop_anchors_trip_ids_restricts_resolution():
    # FA-12 performance fix: a static feed's own trip roster can be far larger than the
    # subset a single day's RT data actually reports running (e.g. Gdańsk: ~93,600 trips
    # in stop_times.txt) - trip_ids restricts eager resolution to just the trips 'match'
    # could ever need, instead of the whole static feed.
    static_index = _make_static_index(
        trips={"trip1": ("routeA", "0"), "trip2": ("routeA", "0")},
        stops={
            "trip1": [(0, "s0", 0, 0), (1, "s1", 60, 60)],
            "trip2": [(0, "s0", 0, 0), (1, "s1", 60, 60)],
        },
    )
    trip_shapes = {"trip1": "shape1", "trip2": "shape1"}
    shapes = {"shape1": _STRAIGHT_LINE}
    stop_locations = {"s0": (0.0, 0.0), "s1": (0.01, 0.0)}

    anchors = resolve_all_trip_stop_anchors(
        static_index, trip_shapes, shapes, stop_locations, trip_ids={"trip1"}
    )

    assert "trip1" in anchors
    assert "trip2" not in anchors


def test_resolve_all_trip_stop_anchors_skips_trip_with_no_resolvable_shape():
    static_index = _make_static_index(
        trips={"trip1": ("routeA", "0")},
        stops={"trip1": [(0, "s0", 0, 0), (1, "s1", 60, 60)]},
    )
    trip_shapes: dict[str, str] = {}  # trip1 has no shape_id at all
    shapes: dict[str, list[tuple[float, float]]] = {}
    stop_locations = {"s0": (0.0, 0.0), "s1": (0.01, 0.0)}

    anchors = resolve_all_trip_stop_anchors(static_index, trip_shapes, shapes, stop_locations)

    assert "trip1" not in anchors


def test_resolve_all_trip_stop_anchors_skips_trip_with_no_known_stop_locations():
    static_index = _make_static_index(
        trips={"trip1": ("routeA", "0")},
        stops={"trip1": [(0, "s_missing", 0, 0)]},
    )
    trip_shapes = {"trip1": "shape1"}
    shapes = {"shape1": _STRAIGHT_LINE}
    stop_locations: dict[str, tuple[float, float]] = {}  # s_missing not in stops.txt

    anchors = resolve_all_trip_stop_anchors(static_index, trip_shapes, shapes, stop_locations)

    assert "trip1" not in anchors


# ---------------------------------------------------------------------------
# interpolate_stop_time
# ---------------------------------------------------------------------------


def test_interpolate_exact_linear_midpoint():
    # Constant velocity: 100 m every 10 s.
    series = [(_t(0), 0.0), (_t(10), 100.0), (_t(20), 200.0)]
    result = interpolate_stop_time(series, 50.0)
    assert result == _t(5)


def test_interpolate_exact_at_second_segment():
    series = [(_t(0), 0.0), (_t(10), 100.0), (_t(20), 200.0)]
    result = interpolate_stop_time(series, 150.0)
    assert result == _t(15)


def test_interpolate_outside_observed_range_above_max_returns_none():
    series = [(_t(0), 0.0), (_t(10), 100.0)]
    assert interpolate_stop_time(series, 150.0) is None


def test_interpolate_outside_observed_range_below_min_returns_none():
    series = [(_t(0), 50.0), (_t(10), 100.0)]
    assert interpolate_stop_time(series, 0.0) is None


def test_interpolate_fewer_than_two_observations_returns_none():
    assert interpolate_stop_time([], 50.0) is None
    assert interpolate_stop_time([(_t(0), 50.0)], 50.0) is None


def test_interpolate_degenerate_equal_distance_pair_returns_first_timestamp():
    series = [(_t(0), 50.0), (_t(10), 50.0), (_t(20), 200.0)]
    assert interpolate_stop_time(series, 50.0) == _t(0)


def test_interpolate_non_monotonic_series_first_crossing_wins():
    # Distance goes 0 -> 50 -> 100 -> 90 (backward GPS-noise blip) -> 200.
    # Target 95.0 is bracketed by three consecutive pairs: the true forward
    # crossing (50 -> 100), and two blip-adjacent pairs (100 -> 90, 90 ->
    # 200). The first bracketing pair encountered in time order - the true
    # forward crossing - must win, not either of the later, noise-adjacent
    # brackets.
    series = [(_t(0), 0.0), (_t(10), 50.0), (_t(15), 100.0), (_t(20), 90.0), (_t(25), 200.0)]
    result = interpolate_stop_time(series, 95.0)
    # (50 -> 100) pair spans t10..t15: frac = (95-50)/(100-50) = 0.9 -> t10 + 4.5s
    assert result == _t(10) + timedelta(seconds=4.5)


def test_interpolate_non_monotonic_series_does_not_crash():
    series = [(_t(0), 0.0), (_t(10), 50.0), (_t(15), 100.0), (_t(20), 90.0), (_t(25), 200.0)]
    # A target only reachable via the final (90 -> 200) climb, past the blip.
    result = interpolate_stop_time(series, 150.0)
    assert result == _t(20) + timedelta(seconds=5 * (60.0 / 110.0))


# ---------------------------------------------------------------------------
# interpolate_stop_time - bracket time-gap rejection (FA-14, PRD §7 #10)
# ---------------------------------------------------------------------------


def test_interpolate_wide_bracket_gap_rejected_at_default_threshold():
    # Bracketing pair spans 400s (> DEFAULT_MAX_BRACKET_GAP_S = 300s), even though the
    # target distance is well within the observed range - sparse GPS sampling at this
    # point of the route, not a real crossing measurement.
    series = [(_t(0), 0.0), (_t(400), 100.0)]
    assert interpolate_stop_time(series, 50.0) is None


def test_interpolate_narrow_bracket_gap_accepted_under_threshold():
    # Same shape, gap under the default threshold - normal interpolation, unchanged.
    series = [(_t(0), 0.0), (_t(200), 100.0)]
    assert interpolate_stop_time(series, 50.0) == _t(100)


def test_interpolate_max_bracket_gap_s_none_disables_check():
    series = [(_t(0), 0.0), (_t(400), 100.0)]
    assert interpolate_stop_time(series, 50.0, max_bracket_gap_s=None) == _t(200)


def test_interpolate_custom_max_bracket_gap_s_threshold():
    series = [(_t(0), 0.0), (_t(120), 100.0)]
    assert interpolate_stop_time(series, 50.0, max_bracket_gap_s=60.0) is None
    assert interpolate_stop_time(series, 50.0, max_bracket_gap_s=180.0) == _t(60)


def test_interpolate_degenerate_equal_distance_pair_wide_gap_rejected():
    # d0 == d1 branch gets the same gap check as the normal linear-interpolation branch.
    series = [(_t(0), 50.0), (_t(400), 50.0), (_t(410), 200.0)]
    assert interpolate_stop_time(series, 50.0) is None


def test_interpolate_bracket_gap_rejected_increments_counts():
    series = [(_t(0), 0.0), (_t(400), 100.0)]
    counts = {"bracket_gap_rejected": 0}
    result = interpolate_stop_time(series, 50.0, counts=counts)
    assert result is None
    assert counts["bracket_gap_rejected"] == 1


def test_interpolate_counts_not_incremented_when_accepted():
    series = [(_t(0), 0.0), (_t(200), 100.0)]
    counts = {"bracket_gap_rejected": 0}
    result = interpolate_stop_time(series, 50.0, counts=counts)
    assert result == _t(100)
    assert counts["bracket_gap_rejected"] == 0


def test_interpolate_omitted_counts_does_not_raise():
    series = [(_t(0), 0.0), (_t(400), 100.0)]
    assert interpolate_stop_time(series, 50.0) is None
