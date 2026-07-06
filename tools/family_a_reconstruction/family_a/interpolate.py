"""Interpolate stop-crossing times from a matched position series (FA-3).

Turns a per-trip (timestamp, distance_along_shape_m) series - FA-2's
match_snapshots output, sliced to one trip_id and sorted by timestamp - into
an estimated crossing time for any given point along the route (typically a
scheduled stop's own distance_along_shape_m).

Standalone tool code: never imports easy_otp/, never imports osgeo/QGIS, and
is never imported by the plugin.

No QGIS / GDAL imports. Run tests: pytest tests/test_interpolate.py -v
"""

from __future__ import annotations

from datetime import datetime

from family_a.matcher import project_point_to_polyline


def stop_distance_along_shape(
    stop_lat: float, stop_lon: float, polyline: list[tuple[float, float]]
) -> float:
    """distance_along_shape_m for a stop's location, via FA-2's projection.

    Thin wrapper around matcher.project_point_to_polyline: reuses the exact
    same projection FA-2 used to compute distance_along_shape_m for observed
    vehicle positions, so a stop's distance and an observation's distance are
    directly comparable on the same polyline. Discards the perpendicular
    component - a stop's own mismatch from the route geometry isn't relevant
    here, only where along the route it sits.

    Deliberately geometric-only: does not read a stop_times.txt
    shape_dist_traveled column even when the static feed provides one.
    Approved MVP simplification (kept consistent with FA-2, which projects
    every observation the same way) - a shape_dist_traveled fast path would
    be more accurate on feeds that publish it, but is left for a future
    milestone rather than silently mixing two different measurement methods
    for "distance along shape" within the same run.
    """
    dist_along_m, _perp_m = project_point_to_polyline(stop_lat, stop_lon, polyline)
    return dist_along_m


def interpolate_stop_time(
    position_series: list[tuple[datetime, float]],
    stop_distance_m: float,
) -> datetime | None:
    """Linearly interpolate the timestamp at which distance == stop_distance_m.

    position_series must be sorted by timestamp (FA-2's match_snapshots output
    already is, per-trip). Scans CONSECUTIVE pairs in that time order - not a
    globally sorted-by-distance search - because FA-2's matcher.py documents
    that distance_along_shape_m can have small backward GPS-noise jumps on
    ~9% of real trips; a search that assumed global monotonicity would either
    mis-bracket or need special-casing for that noise. Scanning pairs in time
    order handles it for free: the first consecutive pair (t0, d0), (t1, d1)
    encountered where min(d0, d1) <= stop_distance_m <= max(d0, d1) wins, even
    if a later pair would also bracket the target (e.g. near a noise blip
    that briefly crosses back). d0 == d1 (no movement between observations)
    resolves to t0.

    Returns None if stop_distance_m falls outside the observed range, no
    bracketing pair is found, or fewer than 2 observations exist - this is
    FA-3's "gap", handled the same way as an unobserved segment in RT-3: the
    stop keeps its scheduled time.

    Known limitation (MVP, same spirit as the backward-jump note above): a
    large transient FORWARD mismatch (e.g. the map-matcher briefly locking
    onto a distant point of a self-intersecting loop) could bracket the
    target first and win, before the vehicle's true, later passage of that
    point - with no flag raised. Not observed in practice yet; documented
    here so a future investigation knows where to look if segment times look
    implausibly short on loop-heavy routes.
    """
    if len(position_series) < 2:
        return None

    for i in range(len(position_series) - 1):
        t0, d0 = position_series[i]
        t1, d1 = position_series[i + 1]

        if not (min(d0, d1) <= stop_distance_m <= max(d0, d1)):
            continue

        if d0 == d1:
            return t0

        frac = (stop_distance_m - d0) / (d1 - d0)
        return t0 + (t1 - t0) * frac

    return None
