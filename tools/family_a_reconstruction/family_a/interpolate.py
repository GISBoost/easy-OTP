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
    stop_lat: float,
    stop_lon: float,
    polyline: list[tuple[float, float]],
    cumulative: list[float] | None = None,
    trusted_dist_m: float | None = None,
) -> float:
    """distance_along_shape_m for a stop's location, via FA-2's projection.

    Thin wrapper around matcher.project_point_to_polyline: reuses the exact
    same projection FA-2 used to compute distance_along_shape_m for observed
    vehicle positions, so a stop's distance and an observation's distance are
    directly comparable on the same polyline. Discards the perpendicular
    component - a stop's own mismatch from the route geometry isn't relevant
    here, only where along the route it sits.

    FA-10 reverses this function's former "Approved MVP simplification"
    (deliberately geometric-only, never reading stop_times.txt's own
    shape_dist_traveled even when the static feed provides one) for feeds
    that earn trust via shape_dist.py's fill-rate and unit-consistency
    checks:

    - *trusted_dist_m*: when given (the stop's own trip/shape passed both
      checks), returned directly - no call to project_point_to_polyline at
      all. This is the preferred path FA-10 adds.
    - *cumulative*: when given (the stop's shape alone passed the
      unit-consistency check, but this specific stop's trip did not pass
      the fill-rate check), passed through to project_point_to_polyline so
      the geometric fallback still lands on the shape's own
      shape_dist_traveled distance axis rather than a haversine-derived one -
      keeping it comparable to a live vehicle observation matched onto the
      same shape (see matcher.match_snapshots's shape_cumulative_dist).

    Omitting both (every pre-FA-10 call site, and every feed without a
    trustworthy shape_dist_traveled) reproduces the original purely-geometric
    behaviour exactly.
    """
    if trusted_dist_m is not None:
        return trusted_dist_m
    dist_along_m, _perp_m = project_point_to_polyline(stop_lat, stop_lon, polyline, cumulative=cumulative)
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
