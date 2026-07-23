"""Unit tests for family_a.interpolate (FA-3).

No QGIS, no network - pure stdlib + pytest.
Run: pytest tests/test_interpolate.py -v
"""

from datetime import datetime, timedelta, timezone

from family_a.interpolate import (
    interpolate_stop_time,
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
