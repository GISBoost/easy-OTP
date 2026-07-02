"""Route ordering heuristics for RouteViaPoints (N-6).

Pure Python — no QGIS, no OTP, no third-party dependencies.
Unit-testable with plain pytest without a QGIS interpreter.
"""
from __future__ import annotations

import math

__all__ = ["haversine", "nearest_neighbor_order", "two_opt", "order_via_points"]

_R_M = 6_371_000.0
_TWO_OPT_MAX_N = 20  # consistent with _VIA_POINTS_WARN in route_via_points.py


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two WGS-84 points."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * _R_M * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def nearest_neighbor_order(
    start: tuple[float, float],
    points: list[tuple[float, float]],
    end: tuple[float, float],
) -> list[int]:
    """Greedy nearest-neighbour order for via-points.

    Args:
        start: (lat, lon) of the route start.
        points: Via-point coordinates as (lat, lon) tuples.
        end: (lat, lon) of the route end — not part of greedy selection, always last.

    Returns:
        Indices into ``points`` in the computed visit order.
    """
    n = len(points)
    if n == 0:
        return []
    if n == 1:
        return [0]

    unvisited: set[int] = set(range(n))
    order: list[int] = []
    current = start
    while unvisited:
        nearest = min(unvisited, key=lambda i: haversine(*current, *points[i]))
        order.append(nearest)
        current = points[nearest]
        unvisited.remove(nearest)
    return order


def two_opt(
    order: list[int],
    points: list[tuple[float, float]],
    start: tuple[float, float],
    end: tuple[float, float],
) -> list[int]:
    """2-opt local search over a nearest-neighbour route.

    Skipped when ``len(order) > _TWO_OPT_MAX_N`` (consistent with UI warning threshold).

    Args:
        order: Current visit order (list of indices into ``points``).
        points: Via-point coordinates as (lat, lon) tuples.
        start: Route start (lat, lon).
        end: Route end (lat, lon).

    Returns:
        Improved (or unchanged) list of indices.
    """
    if len(order) > _TWO_OPT_MAX_N:
        return order

    order = list(order)

    def _chain_dist(ord_: list[int]) -> float:
        chain = [start] + [points[i] for i in ord_] + [end]
        return sum(haversine(*chain[k], *chain[k + 1]) for k in range(len(chain) - 1))

    current_dist = _chain_dist(order)
    improved = True
    while improved:
        improved = False
        for i in range(1, len(order) - 1):
            for j in range(i + 1, len(order) + 1):
                candidate = order[:i] + order[i:j][::-1] + order[j:]
                new_dist = _chain_dist(candidate)
                if new_dist < current_dist - 1e-9:
                    order = candidate
                    current_dist = new_dist
                    improved = True
    return order


def order_via_points(
    start: tuple[float, float],
    points: list[tuple[float, float]],
    end: tuple[float, float],
) -> list[int]:
    """Return an ordered list of indices into ``points`` using NN + 2-opt.

    Public entry point used by RouteViaPoints.
    """
    nn = nearest_neighbor_order(start, points, end)
    return two_opt(nn, points, start, end)
