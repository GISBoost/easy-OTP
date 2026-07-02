"""Unit tests for easy_otp.core.route_ordering (no QGIS, plain Python)."""
import random

import pytest

from easy_otp.core.route_ordering import (
    haversine,
    nearest_neighbor_order,
    order_via_points,
    two_opt,
)


# ---------------------------------------------------------------------------
# haversine
# ---------------------------------------------------------------------------


def test_haversine_same_point():
    assert haversine(0.0, 0.0, 0.0, 0.0) == 0.0


def test_haversine_warsaw_pair():
    # Two Warsaw points ~950 m apart — verify order of magnitude.
    result = haversine(52.229, 21.012, 52.235, 21.025)
    assert abs(result - 950) < 200


def test_haversine_symmetric():
    d1 = haversine(52.229, 21.012, 52.235, 21.025)
    d2 = haversine(52.235, 21.025, 52.229, 21.012)
    assert abs(d1 - d2) < 1e-6


# ---------------------------------------------------------------------------
# nearest_neighbor_order
# ---------------------------------------------------------------------------


def test_nn_empty():
    assert nearest_neighbor_order((0.0, 0.0), [], (1.0, 0.0)) == []


def test_nn_single():
    assert nearest_neighbor_order((0.0, 0.0), [(0.5, 0.0)], (1.0, 0.0)) == [0]


def test_nn_collinear_visits_in_distance_order():
    # Points in shuffled order along a latitude line; from (0,0) toward (0,4).
    # idx0=(0,3), idx1=(0,1), idx2=(0,2) → NN should visit idx1, idx2, idx0.
    start = (0.0, 0.0)
    points = [(0.0, 3.0), (0.0, 1.0), (0.0, 2.0)]
    end = (0.0, 4.0)
    result = nearest_neighbor_order(start, points, end)
    assert result == [1, 2, 0]


def test_nn_covers_all_points():
    random.seed(42)
    points = [(random.uniform(-1, 1), random.uniform(-1, 1)) for _ in range(10)]
    result = nearest_neighbor_order((0.0, 0.0), points, (2.0, 2.0))
    assert sorted(result) == list(range(10))


def test_nn_two_points_picks_closer_first():
    start = (0.0, 0.0)
    near = (0.0, 1.0)
    far = (0.0, 5.0)
    # near is closer to start, so should be visited first
    result = nearest_neighbor_order(start, [far, near], (0.0, 6.0))
    assert result == [1, 0]


# ---------------------------------------------------------------------------
# two_opt
# ---------------------------------------------------------------------------


def test_two_opt_large_n_passthrough():
    order = list(range(21))
    points = [(float(i), 0.0) for i in range(21)]
    result = two_opt(order, points, (-1.0, 0.0), (22.0, 0.0))
    assert result == order  # returned unchanged, not a copy-check, just equality


def test_two_opt_never_worsens_route():
    start = (0.0, 0.0)
    points = [(0.0, 1.0), (0.0, 2.0), (0.0, 3.0)]
    end = (0.0, 4.0)
    nn = nearest_neighbor_order(start, points, end)
    opt = two_opt(nn, points, start, end)

    def route_dist(order):
        chain = [start] + [points[i] for i in order] + [end]
        return sum(haversine(*chain[k], *chain[k + 1]) for k in range(len(chain) - 1))

    assert route_dist(opt) <= route_dist(nn) + 1e-6


def test_two_opt_improves_or_matches_nn():
    # Deliberately shuffled 4 points; 2-opt should not increase total distance.
    start = (0.0, 0.0)
    points = [(0.0, 2.0), (2.0, 2.0), (0.0, 4.0), (2.0, 4.0)]
    end = (2.0, 6.0)
    nn = nearest_neighbor_order(start, points, end)
    opt = two_opt(nn, points, start, end)

    def route_dist(order):
        chain = [start] + [points[i] for i in order] + [end]
        return sum(haversine(*chain[k], *chain[k + 1]) for k in range(len(chain) - 1))

    assert route_dist(opt) <= route_dist(nn) + 1e-6


def test_two_opt_contains_all_indices():
    random.seed(13)
    points = [(random.uniform(-5, 5), random.uniform(-5, 5)) for _ in range(8)]
    nn = nearest_neighbor_order((0.0, 0.0), points, (10.0, 10.0))
    opt = two_opt(nn, points, (0.0, 0.0), (10.0, 10.0))
    assert sorted(opt) == list(range(8))


# ---------------------------------------------------------------------------
# order_via_points
# ---------------------------------------------------------------------------


def test_order_empty():
    assert order_via_points((0.0, 0.0), [], (1.0, 0.0)) == []


def test_order_single():
    assert order_via_points((0.0, 0.0), [(0.5, 0.0)], (1.0, 0.0)) == [0]


def test_order_returns_valid_permutation():
    random.seed(7)
    points = [(random.uniform(-2, 2), random.uniform(-2, 2)) for _ in range(6)]
    result = order_via_points((0.0, 0.0), points, (3.0, 3.0))
    assert sorted(result) == list(range(6))
