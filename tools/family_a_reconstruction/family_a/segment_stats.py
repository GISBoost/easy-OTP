"""Collect and aggregate per-segment travel times from interpolated stop
crossings (FA-3).

Bridges FA-2's matched positions and FA-3's interpolate.py to the P50/P85
segment statistics consumed by build_gtfs.rebuild_stop_times - same
statistical method as RT-3's aggregate_segments (median / 85th percentile,
p85 >= p50 clamp), reimplemented independently here, not imported.

Standalone tool code: never imports easy_otp/, never imports osgeo/QGIS, and
is never imported by the plugin.

No QGIS / GDAL imports. Run tests: pytest tests/test_segment_stats.py -v
"""

from __future__ import annotations

import statistics
import zoneinfo
from collections import defaultdict
from typing import TYPE_CHECKING

import pandas as pd

from family_a.build_gtfs import SegmentKey, segment_key_for
from family_a.calendar_scope import day_type_for_date, time_bucket_for_seconds
from family_a.interpolate import interpolate_stop_time, stop_distance_along_shape

if TYPE_CHECKING:
    from family_a.build_gtfs import StaticIndex

# Reject observed segment travel times outside this range: non-positive is
# impossible, and > 2h is implausible for a single stop-to-stop segment -
# mirrors gtfsrt_realizer.py's collect_segment_times sanity filter, guarding
# against noisy interpolation polluting the percentile stats.
_MAX_PLAUSIBLE_SEG_TIME_S = 7200.0


def collect_segment_observations(
    matched: pd.DataFrame,
    static_index: "StaticIndex",
    trip_shapes: dict[str, str],
    shapes: dict[str, list[tuple[float, float]]],
    stop_locations: dict[str, tuple[float, float]],
    agency_tz: str,
    bucket_minutes: int = 120,
    shape_cumulative_dist: dict[str, list[float]] | None = None,
    trusted_stop_dist: dict[tuple[str, int], float] | None = None,
) -> tuple[dict[SegmentKey, list[float]], dict[str, int]]:
    """For each trip's matched position series, interpolate every consecutive
    scheduled stop pair's crossing time and derive an observed segment
    travel time.

    Returns (segment_times, counts). segment_times maps (route_id,
    direction_id, from_stop_id, to_stop_id, day_type, time_bucket) -> list of
    observed travel times (seconds). day_type/time_bucket are derived from
    the segment's own observation time, converted from UTC to agency_tz
    (see calendar_scope.py) before deriving the local calendar date/time of
    day.

    Groups matched's position series by (trip_id, recording_date) when a
    recording_date column is present (FA-6) - this prevents two physically
    distinct runs of the same trip_id on different days (GTFS-RT's trip_id
    is not date-qualified) from being concatenated into one chronologically
    sorted series, which could make interpolate_stop_time's consecutive-pair
    scan bracket a stop distance across the day boundary and produce a
    bogus segment time.

    Backward compatible: a matched table written before FA-6 (no
    recording_date column) falls back to grouping by trip_id alone,
    reproducing this function's pre-FA-6 behaviour exactly - this is
    intentional, not a defect, and only matters for tables produced by a
    single-directory match run predating FA-6 (every match run under FA-6
    always produces the column).

    counts reports:
    - trips_processed: trips with a resolvable shape and >=2 scheduled stops
      whose stop pairs were actually attempted.
    - trips_skipped_unresolvable: trips excluded before any stop pair was
      attempted, because they have no resolvable shape/polyline or fewer
      than 2 scheduled stops - these never reach the per-stop-pair loop, so
      they contribute to none of the counters below.
    - segments_observed: stop pairs where both interpolations succeeded and
      the derived travel time passed the sanity filter.
    - interpolation_gaps: both stops had a known location, but at least one
      crossing time couldn't be interpolated (the vehicle wasn't observed
      in that part of the route within the recording window) - a data
      density issue, not a data quality issue.
    - missing_stop_location: at least one of the pair's stop_ids has no
      entry in stops.txt - a static-feed data quality issue, distinct from
      interpolation_gaps.
    - rejected_seg_time: interpolation succeeded on both stops but the
      derived segment time was non-positive or implausibly long.

    *shape_cumulative_dist*/*trusted_stop_dist* (FA-10, both optional, both
    default to today's fully-geometric behaviour when omitted): see
    shape_dist.evaluate_shape_trust/evaluate_trip_trust. shape_cumulative_dist
    is looked up once per trip group by shape_id and threaded through to the
    geometric fallback so it lands on the same distance axis as a trustworthy
    feed's own shape_dist_traveled; trusted_stop_dist is looked up per stop
    by (trip_id, stop_sequence) and, when present, bypasses geometric
    projection for that stop entirely.
    """
    segment_times: dict[SegmentKey, list[float]] = defaultdict(list)
    counts = {
        "trips_processed": 0,
        "trips_skipped_unresolvable": 0,
        "segments_observed": 0,
        "interpolation_gaps": 0,
        "missing_stop_location": 0,
        "rejected_seg_time": 0,
    }

    if matched.empty:
        return dict(segment_times), counts

    zone = zoneinfo.ZoneInfo(agency_tz)

    # (shape_id, stop_id) -> distance_along_shape_m, scoped to this call only
    # (many trips share a shape/stop; avoids stale-cache risk across calls).
    distance_cache: dict[tuple[str, str], float] = {}

    group_cols = ["trip_id", "recording_date"] if "recording_date" in matched.columns else ["trip_id"]

    for group_key, group in matched.groupby(group_cols, sort=False):
        trip_id = group_key[0]  # group_cols is always a list -> always a tuple key
        stops = static_index.trip_stops.get(trip_id)
        if not stops or len(stops) < 2:
            counts["trips_skipped_unresolvable"] += 1
            continue

        shape_id = trip_shapes.get(trip_id)
        polyline = shapes.get(shape_id) if shape_id is not None else None
        if polyline is None:
            counts["trips_skipped_unresolvable"] += 1
            continue

        route_id, direction_id = static_index.trip_route.get(trip_id, ("", "0"))
        group = group.sort_values("timestamp")
        position_series = list(zip(group["timestamp"], group["distance_along_shape_m"]))
        counts["trips_processed"] += 1

        cumulative = shape_cumulative_dist.get(shape_id) if shape_cumulative_dist else None

        for idx in range(len(stops) - 1):
            seq_from, stop_from, _arr_from, _dep_from = stops[idx]
            seq_to, stop_to, _arr_to, _dep_to = stops[idx + 1]

            if stop_from not in stop_locations or stop_to not in stop_locations:
                counts["missing_stop_location"] += 1
                continue

            trusted_from = trusted_stop_dist.get((trip_id, seq_from)) if trusted_stop_dist else None
            trusted_to = trusted_stop_dist.get((trip_id, seq_to)) if trusted_stop_dist else None

            d_from = _cached_stop_distance(
                distance_cache, shape_id, stop_from, stop_locations, polyline, cumulative, trusted_from
            )
            d_to = _cached_stop_distance(
                distance_cache, shape_id, stop_to, stop_locations, polyline, cumulative, trusted_to
            )

            t_from = interpolate_stop_time(position_series, d_from)
            t_to = interpolate_stop_time(position_series, d_to)
            if t_from is None or t_to is None:
                counts["interpolation_gaps"] += 1
                continue

            seg_time = (t_to - t_from).total_seconds()
            if seg_time <= 0 or seg_time > _MAX_PLAUSIBLE_SEG_TIME_S:
                counts["rejected_seg_time"] += 1
                continue

            local_t_from = t_from.tz_convert(zone)
            # Known limitation, deliberately deferred (see README): day_type here is the
            # observation's local CALENDAR date, not GTFS's "service day" - an overnight trip
            # observed shortly after local midnight gets the next calendar date's day_type,
            # which can mismatch its own service's day_type (correctly attributed to the
            # previous service day by calendar.txt/calendar_dates.txt). FA-6's
            # (trip_id, recording_date) grouping above prevents cross-day series mixing, but
            # does not fix this separate day_type-vs-service-day nuance.
            day_type = day_type_for_date(local_t_from.date())
            time_bucket = time_bucket_for_seconds(
                local_t_from.hour * 3600 + local_t_from.minute * 60 + local_t_from.second,
                bucket_minutes,
            )
            key: SegmentKey = segment_key_for(
                route_id, direction_id, stop_from, stop_to, day_type, time_bucket
            )
            segment_times[key].append(seg_time)
            counts["segments_observed"] += 1

    return dict(segment_times), counts


def _cached_stop_distance(
    cache: dict[tuple[str, str], float],
    shape_id: str,
    stop_id: str,
    stop_locations: dict[str, tuple[float, float]],
    polyline: list[tuple[float, float]],
    cumulative: list[float] | None = None,
    trusted_dist_m: float | None = None,
) -> float:
    """Memoizing wrapper around stop_distance_along_shape, keyed by (shape_id, stop_id).

    *trusted_dist_m* (FA-10) short-circuits before touching this cache entirely - it's
    a plain per-(trip_id, stop_sequence) value with no geometric computation to
    memoize, and this cache's (shape_id, stop_id) key is deliberately coarser than
    that (shared across every occurrence of a stop_id on a shape, an ambiguity FA-10
    exists to bypass for a loop/out-and-back shape) - caching a trusted value under
    this key could let one occurrence's value leak into another's lookup.
    """
    if trusted_dist_m is not None:
        return trusted_dist_m
    cache_key = (shape_id, stop_id)
    if cache_key not in cache:
        lat, lon = stop_locations[stop_id]
        cache[cache_key] = stop_distance_along_shape(lat, lon, polyline, cumulative=cumulative)
    return cache[cache_key]


def filter_min_observations(
    segment_times: dict[SegmentKey, list[float]],
    min_observations: int,
) -> tuple[dict[SegmentKey, list[float]], int]:
    """Drop segments with fewer than min_observations observations.

    A dropped segment's key is then simply absent from what reaches
    aggregate_segments/rebuild_stop_times, which already treats an absent
    key as a gap - no separate code path needed downstream.
    """
    filtered = {key: values for key, values in segment_times.items() if len(values) >= min_observations}
    dropped_count = len(segment_times) - len(filtered)
    return filtered, dropped_count


def aggregate_segments(
    collected: dict[SegmentKey, list[float]],
) -> tuple[dict[SegmentKey, float], dict[SegmentKey, float]]:
    """Return (p50_stats, p85_stats) using stdlib statistics.

    P50 = median; P85 = 85th percentile. Invariant: p85 >= p50 per segment.
    """
    p50: dict[SegmentKey, float] = {}
    p85: dict[SegmentKey, float] = {}
    for key, values in collected.items():
        p50_val = statistics.median(values)
        if len(values) >= 2:
            p85_val = statistics.quantiles(values, n=100)[84]
        else:
            p85_val = values[0]
        p85_val = max(p85_val, p50_val)
        p50[key] = p50_val
        p85[key] = p85_val
    return p50, p85
