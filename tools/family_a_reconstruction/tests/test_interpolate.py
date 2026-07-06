"""Unit tests for family_a.interpolate (FA-3).

No QGIS, no network - pure stdlib + pytest.
Run: pytest tests/test_interpolate.py -v
"""

from datetime import datetime, timedelta, timezone

from family_a.interpolate import interpolate_stop_time, stop_distance_along_shape
from family_a.matcher import project_point_to_polyline

# Same straight north-south line used in test_matcher.py.
_STRAIGHT_LINE = [(0.0, 0.0), (0.01, 0.0), (0.02, 0.0)]

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
