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

import logging
import math
from datetime import datetime
from typing import TYPE_CHECKING

from family_a.matcher import _project_onto_segment, cumulative_distances, project_point_to_polyline

if TYPE_CHECKING:
    from family_a.build_gtfs import StaticIndex

logger = logging.getLogger(__name__)

# PRD FA-11, "open questions" #3: backward slack (metres) allowed when
# restricting resolve_stop_distances_for_pattern's search to polyline points
# at/after the previous stop's resolved distance. Real feeds have minor,
# legitimate non-monotonicity at stop clusters (depots, transfer hubs) - a
# rigid ">=" with zero tolerance risks rejecting a legitimate case outright,
# forcing an unwanted fallback to the unrestricted global search.
#
# NOT YET CONFIRMED BY MICHAŁ - this is a documented starting point ("tens of
# metres"), not an empirically-tuned value. Flag any change, and report this
# value's effect on the Poznań 07-18 re-run (inflated-segment count) back to
# him per FA-11's acceptance criteria before treating it as final.
DEFAULT_BACKWARD_TOLERANCE_M = 50.0


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


def resolve_stop_distances_for_pattern(
    polyline: list[tuple[float, float]],
    ordered_stops: list[tuple[str, float, float]],
    cumulative: list[float] | None = None,
    backward_tolerance_m: float = DEFAULT_BACKWARD_TOLERANCE_M,
    shape_id: str | None = None,
    trip_id: str | None = None,
) -> list[float]:
    """Resolve a whole trip's ordered stop pattern's distance_along_shape_m together (FA-11).

    *ordered_stops* is [(stop_id, lat, lon), ...] in stop_sequence order for one trip's
    stop pattern. Returns a list of resolved distances, POSITIONALLY aligned with
    ordered_stops (not a stop_id-keyed dict) - a stop_id can legitimately occur more
    than once in one trip's pattern on a loop/out-and-back route, and collapsing to a
    dict would silently corrupt exactly that case.

    Replaces today's independent, context-free per-stop projection (each stop resolved
    on its own via stop_distance_along_shape/project_point_to_polyline, with zero
    awareness of the stop's place in the trip) with a SEQUENTIAL resolution that
    enforces monotonicity along the shape's own cumulative-distance axis:

    - The first stop (index 0) has no "previous" to anchor against, so it always uses
      today's unrestricted global nearest-point search over the whole polyline -
      unchanged, no special-cased distance-0 assumption (real trips don't always start
      their shape at distance 0).
    - Every later stop searches ONLY polyline points at cumulative_distance >=
      (previously resolved distance - backward_tolerance_m), among which it picks the
      perpendicular-nearest candidate (same lowest-index-wins tie-break as
      project_point_to_polyline, just scoped to this restricted range). A segment is
      only considered once its far endpoint reaches the threshold (cheap pre-filter),
      but a straddling segment's actual projected point is separately re-checked
      against the threshold too - otherwise its before-threshold portion could still
      win, defeating the whole point of the restriction. This is what prevents a stop
      that is late in stop_sequence from wrongly anchoring to an early pass of a
      self-repeating/out-and-back shape merely because that pass has a lower polyline
      index (the exact failure mode reproduced on Poznań route 151, shape 154679,
      trip 3_1256050^+, stops 411->385).
    - backward_tolerance_m is a real, non-zero slack, not a rigid ">=": real feeds have
      minor, legitimate non-monotonicity at stop clusters (depots, transfer hubs), and a
      rigid floor could reject a legitimate case outright. If NO segment satisfies the
      constraint even with this slack (rare), this falls back to the full unrestricted
      global search and logs a warning identifying shape_id/trip_id/stop_id - never a
      hard error, the build must still complete regardless.

    Does not eliminate anchoring ambiguity in 100% of cases (very densely overlapping
    loops may still have edge cases) - this is risk reduction backed by the reproduced
    example above, not a mathematical guarantee for every possible geometry.

    Intended to be called once per distinct (shape_id, stop pattern) - cheap, one-time
    cost, not per GPS observation (see segment_stats._resolve_pattern_distances, which
    caches by exactly that key).
    """
    if cumulative is None:
        cumulative = cumulative_distances(polyline)

    resolved: list[float] = []
    for idx, (stop_id, lat, lon) in enumerate(ordered_stops):
        if idx == 0:
            dist_along_m, _perp_m = project_point_to_polyline(lat, lon, polyline, cumulative=cumulative)
            resolved.append(dist_along_m)
            continue

        threshold = resolved[idx - 1] - backward_tolerance_m
        best_perp_m = math.inf
        best_dist_along_m: float | None = None

        for i in range(len(polyline) - 1):
            if cumulative[i + 1] < threshold:
                # Segment's far endpoint never reaches the threshold - no point on
                # it can either. Cheap skip before doing the projection math.
                continue
            lat1, lon1 = polyline[i]
            lat2, lon2 = polyline[i + 1]
            dist_along_m, perp_m = _project_onto_segment(lat, lon, lat1, lon1, lat2, lon2, cumulative[i])
            if dist_along_m < threshold:
                # A straddling segment (starts before the threshold, ends at/after
                # it) can still project onto its own before-threshold portion -
                # that candidate must not count, or monotonicity isn't actually
                # enforced despite the segment-level pre-filter above.
                continue
            if perp_m < best_perp_m:
                best_perp_m = perp_m
                best_dist_along_m = dist_along_m

        if best_dist_along_m is None:
            dist_along_m, _perp_m = project_point_to_polyline(lat, lon, polyline, cumulative=cumulative)
            logger.warning(
                "interpolate.py: no polyline segment satisfies the backward tolerance "
                "(%.1fm) for shape_id=%s trip_id=%s stop_id=%s (pattern position=%d) - "
                "falling back to unrestricted global search, resolved to %.1fm.",
                backward_tolerance_m, shape_id, trip_id, stop_id, idx, dist_along_m,
            )
            resolved.append(dist_along_m)
        else:
            resolved.append(best_dist_along_m)

    return resolved


def resolve_all_trip_stop_anchors(
    static_index: "StaticIndex",
    trip_shapes: dict[str, str],
    shapes: dict[str, list[tuple[float, float]]],
    stop_locations: dict[str, tuple[float, float]],
    shape_cumulative_dist: dict[str, list[float]] | None = None,
    trusted_stop_dist: dict[tuple[str, int], float] | None = None,
    backward_tolerance_m: float = DEFAULT_BACKWARD_TOLERANCE_M,
    trip_ids: set[str] | None = None,
) -> dict[str, list[tuple[int, str, float]]]:
    """Eagerly resolve stop anchor lists for trips in the static feed (FA-12).

    Returns trip_id -> [(stop_sequence, stop_id, distance_along_shape_m), ...], sorted by
    stop_sequence - the already-corrected FA-10/FA-11 stop anchors, in the exact form FA-12's
    matcher.match_snapshots needs to build a live observation's window
    ([dist(stop[idx-1]), dist(stop[idx+1])]). A trip with no resolvable shape/polyline, or no
    stops with a known location, is simply absent from the returned dict - callers treat a
    missing trip_id as "no anchors, fall back to unrestricted matching", same convention as
    every other fallback in this module.

    *trip_ids* (FA-12): when given, restricts resolution to only these trip_ids - see
    matcher.observed_trip_ids. A static feed's own trip roster can be tens of thousands of trips
    (e.g. Gdańsk: ~93,600), while any single day's RT data only ever reports the small subset
    actually running; resolving every trip in the whole feed regardless of whether 'match' could
    ever need it made this eager pass cost proportional to static feed size rather than RT data
    volume - the exact regression discovered running this milestone's own real-data verification
    against Gdańsk. Omitted (the default, None) resolves every trip in the feed, unchanged from
    this function's original contract - existing callers (and this function's own tests) that
    never pass it see no behaviour change.

    Per trip, picks exactly one of two paths - never mixes them - mirroring
    segment_stats.collect_segment_observations's own trusted-vs-pattern branch:
    - Fully trusted (every stop_sequence of this trip present in *trusted_stop_dist* -
      shape_dist.evaluate_trip_trust is all-or-nothing per trip): each stop's distance comes
      straight from *trusted_stop_dist*, no geometric projection at all.
    - Otherwise: the trip's whole ordered stop pattern is resolved together via FA-11's
      resolve_stop_distances_for_pattern (sequential, monotonicity-enforced).

    Deliberately a separate implementation rather than a shared extraction with segment_stats.py's
    private per-trip helpers (_cached_stop_distance/_resolve_pattern_distances) - same rationale
    matcher.resolve_trip_shapes's own docstring gives for its comparable choice: this runs ahead
    of any matched positions (match's own step, before any VehiclePosition has been seen), whereas
    segment_stats.py's version is lazily driven by whichever trips actually appear in the matched
    dataframe (build's step, after matching). Different drivers for the same underlying math, kept
    independent and separately testable rather than forcing one shared abstraction to serve both.

    *shape_cumulative_dist*/*trusted_stop_dist*/*backward_tolerance_m*: see
    shape_dist.evaluate_shape_trust/evaluate_trip_trust and resolve_stop_distances_for_pattern.
    Both optional args default to today's fully-geometric, non-trusted behaviour when omitted.
    """
    trusted_trip_ids = {tid for tid, _seq in trusted_stop_dist} if trusted_stop_dist else set()

    anchors: dict[str, list[tuple[int, str, float]]] = {}
    for trip_id, stops in static_index.trip_stops.items():
        if trip_ids is not None and trip_id not in trip_ids:
            continue
        shape_id = trip_shapes.get(trip_id)
        polyline = shapes.get(shape_id) if shape_id is not None else None
        if polyline is None:
            continue

        filtered = [(seq, stop_id) for seq, stop_id, *_rest in stops if stop_id in stop_locations]
        if not filtered:
            continue

        if trip_id in trusted_trip_ids:
            anchors[trip_id] = [
                (seq, stop_id, trusted_stop_dist[(trip_id, seq)]) for seq, stop_id in filtered
            ]
            continue

        cumulative = shape_cumulative_dist.get(shape_id) if shape_cumulative_dist else None
        ordered_stops = [(stop_id, *stop_locations[stop_id]) for seq, stop_id in filtered]
        distances = resolve_stop_distances_for_pattern(
            polyline,
            ordered_stops,
            cumulative=cumulative,
            backward_tolerance_m=backward_tolerance_m,
            shape_id=shape_id,
            trip_id=trip_id,
        )
        anchors[trip_id] = [
            (seq, stop_id, dist) for (seq, stop_id), dist in zip(filtered, distances)
        ]

    return anchors


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
