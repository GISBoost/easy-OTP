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
from family_a.interpolate import (
    DEFAULT_BACKWARD_TOLERANCE_M,
    DEFAULT_MAX_BRACKET_GAP_S,
    interpolate_stop_time,
    resolve_stop_distances_for_pattern,
    stop_distance_along_shape,
)

if TYPE_CHECKING:
    from family_a.build_gtfs import StaticIndex

# Reject observed segment travel times outside this range: non-positive is
# impossible, and > 2h is implausible for a single stop-to-stop segment -
# mirrors gtfsrt_realizer.py's collect_segment_times sanity filter, guarding
# against noisy interpolation polluting the percentile stats.
_MAX_PLAUSIBLE_SEG_TIME_S = 7200.0

# FA-13 (PRD FA-13, "Otwarte kwestie" #5): upper bound on implied average speed for a single
# stop-to-stop segment - defense-in-depth on top of FA-10/FA-11/FA-12, not a fix for their root
# cause. CONFIRMED BY MICHAL (PRD #5 answer): 100 km/h for "predkosc komunikacyjna" (commercial/
# operating speed, i.e. distance / elapsed real time including any incidental slow-downs) vs.
# 120 km/h for pure driving speed - seg_time here is wall-clock time between two GPS-derived stop
# crossings, which already absorbs traffic/congestion within the segment, so it is commercial
# speed -> 100 km/h applies.
# Known limitation (verified on real data, Poznan/Prague 07-18): a single flat threshold, at
# either value, still rejects legitimate regional-rail segments in feeds that include them (e.g.
# Prague's route_type=2 InterCity/rychlik routes routinely exceed 120 km/h) - accepted as a known
# gap for now; per-route/route_type-aware thresholds are a distinct, larger change, not in scope
# for FA-13.
_MAX_PLAUSIBLE_SPEED_MPS = 100.0 / 3.6  # 100 km/h ~= 27.78 m/s

# FA-18: lower bound on the same quantity. FA-13 above deliberately had none ("slow segments
# (traffic, long dwells) are fully legitimate") - true as far as it went, but it was decided
# without measuring, and what it lets through is a vehicle standing on a terminus during its
# layover, whose wait interpolate_stop_time then books as travel time.
#
# Calibrated 2026-07-30 on 823,081 RAW segment observations from gtfs-manual-test/raw_snapshots -
# 7 city-days covering all three FA-12 position-signal classes (Gdansk 07-21/07-22 "none", Lodz
# 07-19/07-21 + Bucharest 07-21 "sequence", Vilnius 07-19/07-21 "stop_id"). The positive class of
# that calibration was "first pairs of trips with no FA-12 window" (2,728) simply because those
# were the pairs FA-17 had already shown to be layover-dominated; negatives = mid-trip pairs in
# windowed cities (637,196). It describes how the number was picked, not a rule that still runs -
# FA-20 removed the signal condition entirely (see collect_segment_observations' docstring):
#
#     threshold   caught (bad)   lost (good)   discrimination
#       1.0 km/h        19.0%        0.005%          4025x
#       1.5 km/h        25.9%        0.024%          1086x
#       2.0 km/h        31.1%        0.063%           496x    <- chosen
#       2.5 km/h        35.0%        0.125%           280x
#       3.0 km/h        39.8%        0.215%           185x
#
# "Positives" is a proxy, not a set of confirmed stationary vehicles - it was the population
# FA-17's own measurement showed to be dominated by layovers, but it also contains legitimately
# moving trips, so the catch column is a lower bound on real-world precision rather than a
# "69% of stopped vehicles get through" figure.
#
# 2 km/h is picked on physics rather than on the curve: over the median 472 m segment it means
# 14.2 minutes to cross one stop pair. The rejected population's signature confirms it - those
# 399 mid-trip casualties average 806 s against a mean of 107 s for what is kept, while being
# SHORTER (median 342 m vs 473 m). They are not slow journeys, they are stopped vehicles.
# Going to 3 km/h
# triples the cost for +8.7pp of catch, and 2-3 km/h is the only band where a genuinely extreme
# jam could plausibly live; a false rejection biases delay DOWNWARD, i.e. the opposite direction
# to the defect being fixed, and is correspondingly harder to notice.
#
# A minimum-distance guard was measured and REJECTED - do not add one "to be safe". An earlier
# calibration against pooled P50s with straight-line distances suggested requiring >100 m,
# because its false positives had a median length of 69 m. On raw observations with real
# shape-polyline distances that effect disappears (median 342 m), and the guard then cuts the
# catch from 31.1% to 23.4% while lowering the cost only from 0.063% to 0.058%.
#
# Division of labour, as of FA-20: the first stop pair of every trip is now dropped before it is
# ever interpolated, so this bound no longer sees the pairs it was calibrated on. What it still
# does - and the only reason it is not redundant - is catch stationary observations MID-TRIP: a
# vehicle held at a terminus that the schedule places mid-route, a driver break, or a layover that
# spills past stop 2. The 10.6% of "sequence" first pairs and 3.4% of "stop_id" ones it was
# measured to reject are historical evidence that the artifact reached beyond signal "none"; they
# are not work this bound is doing today.
#
# Second-order effect worth knowing about: this filter drops individual OBSERVATIONS, so a
# segment key that loses enough of them can fall under --min-observations-per-segment and
# silently revert to its scheduled time - indistinguishable downstream from a stop pair that
# was never observed. FA-13 has the same property but rejects ~0.09% where this rejects up to
# ~0.69%, so it matters more here.
#
# One caveat on transferability: the calibration cities have a median segment of 472 m. In a
# feed with very dense downtown stop spacing (40-60 m pairs) 2 km/h corresponds to only ~90 s,
# which is an ordinary "stop, traffic light, stop" crawl rather than a layover. Nothing in the
# measured set behaves that way, but that is where to look first if a newly added city starts
# reporting an unexpectedly high rejected_stationary.
DEFAULT_MIN_PLAUSIBLE_SPEED_KMH = 2.0
_MIN_PLAUSIBLE_SPEED_MPS = DEFAULT_MIN_PLAUSIBLE_SPEED_KMH / 3.6  # ~= 0.56 m/s


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
    backward_tolerance_m: float = DEFAULT_BACKWARD_TOLERANCE_M,
    max_bracket_gap_s: float | None = DEFAULT_MAX_BRACKET_GAP_S,
    skip_first_segment: bool = True,
    min_plausible_speed_mps: float | None = _MIN_PLAUSIBLE_SPEED_MPS,
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
      in that part of the route within the recording window, OR its
      bracketing pair was rejected for a too-wide time gap - see
      bracket_gap_rejected below) - a data density issue, not a data
      quality issue.
    - bracket_gap_rejected (FA-14, PRD §7 open question #10): counts individual
      interpolate_stop_time CALLS (not stop pairs) rejected because the
      bracketing pair of real GPS observations they found was spaced wider
      than max_bracket_gap_s in time (sparse sampling at that point of the
      route, not an actual gap in coverage). Each stop pair makes up to two
      such calls (one for its "from" stop, one for its "to" stop), so this
      counter is NOT a subset of interpolation_gaps and can exceed it
      numerically - interpolation_gaps only increments once per stop pair
      (when at least one of the two calls returns None, for any reason),
      while bracket_gap_rejected increments once per rejected call, so a
      single gap-affected stop pair can contribute up to 2 here but only 1
      there.
    - missing_stop_location: at least one of the pair's stop_ids has no
      entry in stops.txt - a static-feed data quality issue, distinct from
      interpolation_gaps.
    - rejected_seg_time: interpolation succeeded on both stops but the
      derived segment time was non-positive, implausibly long, or implied a
      physically-impossible average speed (FA-13, safety net - see
      _MAX_PLAUSIBLE_SPEED_MPS). Takes PRECEDENCE over rejected_stationary
      below: an observation failing both lands here only. That has a real
      consequence worth knowing - a stationary observation whose seg_time
      exceeds _MAX_PLAUSIBLE_SEG_TIME_S (2h) is counted here, not there, so
      the very slowest cases (below ~0.24 km/h over a median 472 m segment)
      systematically bypass the counter built to count them.
    - first_segment_skipped (FA-17, generalised in FA-20): trips whose FIRST
      stop pair was not even attempted - see *skip_first_segment* below. Counted
      once per trip group, and those pairs contribute to none of the counters
      above. Since FA-20 the skip is unconditional, so with the default this
      equals trips_processed; it stays a counter rather than becoming implicit
      because it is what makes the cost of the rule visible in a build log.
    - rejected_stationary (FA-18): interpolation succeeded on both stops but the
      implied average speed was below *min_plausible_speed_mps* - a vehicle
      standing still, not travelling slowly. Kept deliberately SEPARATE from
      rejected_seg_time: a counter that mixes causes cannot be acted on, which
      is exactly why trips_skipped_unresolvable (three causes in one number) had
      to be bypassed in FA-16. Counts only observations that got past
      rejected_seg_time - see the precedence note above.

    *min_plausible_speed_mps* (FA-18, default 2 km/h, None disables): reject an
    observation whose implied average speed is below this. See the constant's
    own comment for the calibration behind the number - 823,081 raw observations
    across 7 city-days - including why no minimum-distance guard accompanies it.

    *skip_first_segment* (FA-17, made unconditional in FA-20, default on): drop
    the first stop pair of EVERY trip, whatever its position_signal, and whether
    or not the matched table has that column at all.

    Why the first pair: a vehicle idling on its origin terminus already carries
    the next trip's trip_id, so its whole layover is matched to that trip. The
    layover positions all project at (or near) stop 1's distance, and
    interpolate_stop_time returns the FIRST bracketing pair it finds - so the
    moment the vehicle ARRIVED to wait is recorded as the moment it crossed
    stop 1, and the entire wait is booked as travel time on the first stop pair.
    Nothing in that chain depends on FA-12 windowing.

    Why the FA-17 signal condition was dropped (measured 2026-07-29 across nine
    cities, median implied speed of the first pair vs mid-trip, km/h):

        Rome     sequence 100%    2.46 vs 19.06    39.9% under 2 km/h
        Prague   sequence  72%    3.11 vs 24.54    37.9%
        Boston   sequence  94%    4.32 vs 20.40    22.2%
        Gdansk   none             4.72 vs 19.77    23.4%
        Szczecin sequence 100%    5.03 vs 20.76    20.1%
        Vilnius  stop_id  100%   10.57 vs 20.67     3.4%
        Sofia    stop_id  100%   12.00 vs 18.54     0.1%
        Lodz     sequence 100%     ~18  vs 18.65      ~0%

    Rome and Szczecin have full sequence coverage and are among the worst;
    Sofia has stop_id and is nearly clean; Gdansk - the only "none", and the
    single city FA-17 was calibrated on - is only fourth; and Lodz, the one
    city with no artifact at all, is "sequence" like the worst two. The signal
    class does not predict the artifact, so FA-17's condition was a coincidence
    of its sample rather than a mechanism.

    What is lost: the first pair also carries genuine departure lateness, and it
    is the only pair that can, since rebuild_stop_times anchors each trip on its
    SCHEDULED first departure. Measured on the same recordings, that loss is
    negligible against the artifact - median layover 120-502 s per city against
    a median departure lateness of -16 to +15 s - and since observations are
    pooled and reduced to a P50 per segment key, the median is what survives
    anyway.

    Note this reverses FA-17's backward-compatibility rule deliberately: a
    matched table with no position_signal column (written before FA-17) is now
    skipped too. Under FA-17 such a table was left untouched, which would today
    mean silently keeping the artifact in every archived pre-FA-17 table.
    position_signal itself stays in the table as FA-12/FA-17 diagnostics - match
    still resolves and prints it - it just no longer drives anything here.

    *shape_cumulative_dist*/*trusted_stop_dist* (FA-10, both optional, both
    default to today's fully-geometric behaviour when omitted): see
    shape_dist.evaluate_shape_trust/evaluate_trip_trust. shape_cumulative_dist
    is looked up once per trip group by shape_id and threaded through to the
    geometric fallback so it lands on the same distance axis as a trustworthy
    feed's own shape_dist_traveled.

    trusted_stop_dist (FA-10) is strictly all-or-nothing per trip
    (shape_dist.evaluate_trip_trust only adds an entry for EVERY stop_sequence
    of a trip once that trip's own shape_dist_traveled fill-rate is 100%,
    never a subset) - so per trip group this function picks exactly one of two
    paths, never mixes them within a trip:
    - Fully trusted trip: every stop's distance comes straight from
      trusted_stop_dist[(trip_id, seq)] - no geometric projection at all.
    - Not trusted (the common case for feeds without a usable
      shape_dist_traveled, e.g. Poznań/Szczecin/Gdańsk): the trip's whole
      ordered stop pattern is resolved together, once, via FA-11's
      interpolate.resolve_stop_distances_for_pattern (sequential, monotonicity-
      enforced) instead of resolving each stop independently - see
      _resolve_pattern_distances below. *backward_tolerance_m* is the slack
      (metres) that resolver allows a later stop to sit behind the previous
      stop's resolved distance before treating it as non-monotonic.
    """
    segment_times: dict[SegmentKey, list[float]] = defaultdict(list)
    counts = {
        "trips_processed": 0,
        "trips_skipped_unresolvable": 0,
        "segments_observed": 0,
        "interpolation_gaps": 0,
        "bracket_gap_rejected": 0,
        "missing_stop_location": 0,
        "rejected_seg_time": 0,
        "first_segment_skipped": 0,
        "rejected_stationary": 0,
    }

    if matched.empty:
        return dict(segment_times), counts

    zone = zoneinfo.ZoneInfo(agency_tz)

    # (shape_id, tuple of stop_ids in trip order) -> {stop_sequence: distance_along_shape_m},
    # scoped to this call only (many trips share a shape/pattern; avoids stale-cache risk
    # across calls). Keyed by the FULL stop pattern (FA-11), not bare shape_id, so trips
    # sharing a shape but a different stop subset/order (e.g. express vs. local variants
    # on the same physical route) never reuse each other's resolution.
    pattern_cache: dict[tuple[str, tuple[str, ...]], dict[int, float]] = {}

    # Trips with ANY trusted_stop_dist entry have EVERY stop_sequence trusted
    # (shape_dist.evaluate_trip_trust is all-or-nothing per trip) - precomputed once so
    # the per-trip branch below is a single cheap membership check.
    trusted_trip_ids = {tid for tid, _seq in trusted_stop_dist} if trusted_stop_dist else set()

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

        trip_fully_trusted = trip_id in trusted_trip_ids
        pattern_dist: dict[int, float] | None = None
        if not trip_fully_trusted:
            pattern_dist = _resolve_pattern_distances(
                pattern_cache, shape_id, trip_id, stops, stop_locations, polyline, cumulative, backward_tolerance_m
            )

        # FA-20: unconditional - position_signal does not predict the terminus-layover
        # artifact, see the docstring. A 2-stop trip needs no special case: the loop below
        # becomes range(1, 1), i.e. no segments and no exception.
        first_idx = 1 if skip_first_segment else 0
        if skip_first_segment:
            counts["first_segment_skipped"] += 1

        for idx in range(first_idx, len(stops) - 1):
            seq_from, stop_from, _arr_from, _dep_from = stops[idx]
            seq_to, stop_to, _arr_to, _dep_to = stops[idx + 1]

            if stop_from not in stop_locations or stop_to not in stop_locations:
                counts["missing_stop_location"] += 1
                continue

            if trip_fully_trusted:
                d_from = trusted_stop_dist[(trip_id, seq_from)]
                d_to = trusted_stop_dist[(trip_id, seq_to)]
            else:
                d_from = pattern_dist[seq_from]
                d_to = pattern_dist[seq_to]

            t_from = interpolate_stop_time(
                position_series, d_from, max_bracket_gap_s=max_bracket_gap_s, counts=counts
            )
            t_to = interpolate_stop_time(
                position_series, d_to, max_bracket_gap_s=max_bracket_gap_s, counts=counts
            )
            if t_from is None or t_to is None:
                counts["interpolation_gaps"] += 1
                continue

            seg_time = (t_to - t_from).total_seconds()
            seg_distance_m = abs(d_to - d_from)
            implausible_speed = (
                seg_time > 0 and seg_distance_m / seg_time > _MAX_PLAUSIBLE_SPEED_MPS
            )
            if seg_time <= 0 or seg_time > _MAX_PLAUSIBLE_SEG_TIME_S or implausible_speed:
                counts["rejected_seg_time"] += 1
                continue

            # FA-18. Checked after the block above so the two counters stay disjoint: an
            # observation already rejected as implausible never also lands here. Strict `<`,
            # so an observation exactly at the threshold is kept.
            if min_plausible_speed_mps is not None and seg_distance_m / seg_time < min_plausible_speed_mps:
                counts["rejected_stationary"] += 1
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


def _resolve_pattern_distances(
    cache: dict[tuple[str, tuple[str, ...]], dict[int, float]],
    shape_id: str,
    trip_id: str,
    stops: list[tuple],
    stop_locations: dict[str, tuple[float, float]],
    polyline: list[tuple[float, float]],
    cumulative: list[float] | None,
    backward_tolerance_m: float,
) -> dict[int, float]:
    """Memoizing wrapper around resolve_stop_distances_for_pattern (FA-11).

    Cache key: (shape_id, tuple of stop_ids in trip order) - trips sharing a shape but a
    different stop pattern/subset (e.g. express vs. local variants on the same physical
    route) never reuse each other's resolution. Only used for trips that are NOT fully
    covered by trusted_stop_dist (see collect_segment_observations's docstring on the
    all-or-nothing-per-trip invariant this relies on).

    Stops with no entry in stop_locations are excluded before resolving - mirrors
    collect_segment_observations's own missing_stop_location check, which independently
    counts them per stop pair - and are simply absent from the returned dict.
    """
    filtered = [(seq, stop_id) for seq, stop_id, *_rest in stops if stop_id in stop_locations]
    seqs, stop_ids = zip(*filtered) if filtered else ((), ())
    cache_key = (shape_id, stop_ids)

    if cache_key not in cache:
        ordered_stops = [(stop_id, *stop_locations[stop_id]) for stop_id in stop_ids]
        distances = resolve_stop_distances_for_pattern(
            polyline,
            ordered_stops,
            cumulative=cumulative,
            backward_tolerance_m=backward_tolerance_m,
            shape_id=shape_id,
            trip_id=trip_id,
        )
        cache[cache_key] = dict(zip(seqs, distances))

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
