"""Rebuild stop_times.txt and repackage a realized GTFS zip (FA-3).

Reimplementation (not import) of the relevant slice of
easy_otp/core/gtfsrt_realizer.py's RT-3 logic: same segment-based P50/P85
timetable correction, same "gap = keep scheduled time" fallback, same
monotonic-clamp and >24:00:00 handling. Read that module for parsing-style
reference only - never imported, since this tool must never import
easy_otp/.

Family A has no trip-cancellation signal (VehiclePositions carries no
ScheduleRelationship/CANCELED concept the way TripUpdate does), so unlike
RT-3's rebuild_stop_times/repackage_gtfs, there is no drop_trip_ids
parameter here - there's no equivalent input to populate it from.

Standalone tool code: never imports easy_otp/, never imports osgeo/QGIS, and
is never imported by the plugin.

No QGIS / GDAL imports. Run tests: pytest tests/test_build_gtfs.py -v
"""

from __future__ import annotations

import csv
import io
import logging
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field

from family_a.calendar_scope import time_bucket_for_seconds

logger = logging.getLogger(__name__)

# segment_key = (route_id, direction_id, from_stop_id, to_stop_id, day_type, time_bucket)
SegmentKey = tuple[str, str, str, str, str, int]

# FA-15: fraction of the routes actually OBSERVED in a run's matched data that must end up with at
# least one corrected segment before the build is treated as normal. Below this, the build is
# reported as low-yield.
#
# The denominator is deliberately "routes observed in the RT data", not "corrected segments out of
# all segments in the static feed". The latter is what `build` already prints, and it is not
# comparable between cities or even between days of one city: it is capped by how much of the
# static feed's own validity window a single recording day can possibly cover (a feed valid for 9
# days, recorded for 1, cannot exceed roughly the share of days sharing that day_type no matter how
# perfect the recording is). Normalising by what was actually observed removes that confound.
#
# CALIBRATED ON REAL DATA (2026-07-28), replacing the initial 0.50 starting point. Measured this
# metric across two populations - 18 builds that produced a normal realized feed, and 2 that
# produced essentially nothing (Turin 2026-07-20, published anyway with 217 changed stop_times
# rows out of 1,416,230; Turin 2026-07-22, which never published at all). Both Turin days turned
# out to have the same cause: its VehiclePositions feed emitted almost no trip_id (99.5% and 100%
# of observations respectively).
#
#   healthy (n=18): min 61.1%, median 94.8%, max 100%   (the 61.1% floor is Prague 07-18, a
#                   Saturday; the next lowest is 86.8%)
#   broken  (n=2):  0.0% and 20.0%
#
# That leaves a clean 20.0%-61.1% separating gap, so any threshold in [25%, 60%] gives zero false
# alarms and zero misses on this data. 0.40 is chosen because it maximises the SMALLER of the two
# margins (20.0 pts above the worst broken run, 21.1 pts below the worst healthy one); 0.50 was
# valid but lopsided, leaving only 11.1 pts of headroom under Prague's Saturday.
#
# Two honest caveats, both worth revisiting if more data turns up: the broken population is n=2 and
# both points are the same city and the same failure mode, and the 61.1% healthy floor rests on a
# single Saturday. Note also that the match-side gate caught both broken days far more decisively
# (99.5%/100% reject share) than this one did - this is the secondary detector, not the primary.
DEFAULT_MIN_CORRECTED_ROUTE_SHARE = 0.40

# FA-16: share of the matched table's distinct trip_ids that the static feed may fail to
# recognise before `build` reports that its two inputs do not belong together.
#
# The invariant this rests on: `match` only ever emits a row once the trip_id resolved through
# trip_shapes, which is derived from the very same static feed's trips.txt - so when `build` is
# handed that same feed, the unknown share is ZERO by construction. Anything above zero means the
# matched table and --static came from different publications.
#
# CONFIRMED WITH MICHAŁ (2026-07-28): 0.20. Deliberately loose relative to a zero invariant,
# because once the numeric-trip_id defect above is fixed, the only remaining way to trip this is a
# genuinely mismatched pair - and that is never subtle: Łódź renumbers its whole trip_id namespace
# every 1-3 days (~99% unknown), Poznań per publication period (67-98%). Accepted gap: a partial
# corruption below 20% would pass unreported - immaterial, since the dtype fix removes its cause.
DEFAULT_MAX_UNKNOWN_TRIP_SHARE = 0.20

# D1 (2026-08-09): what the reconstructed timetable's accumulator is measuring.
#
# interpolate_stop_time returns the FIRST pair of real observations bracketing a stop's
# distance, so a vehicle standing at a stop is credited with crossing it at (or before) the
# moment it arrived. The observed segment time is therefore an ARRIVAL-TO-ARRIVAL interval,
# and it already contains the real dwell at the "from" stop.
#
# Until this date rebuild_stop_times took that interval and then added the SCHEDULED dwell on
# top of it:
#
#     new_arr[i] = running + travel          # travel already includes the real dwell at i-1
#     new_dep[i] = new_arr[i] + dwell[i]     # scheduled dwell counted a second time
#     running    = new_dep[i]
#
# so every stop of a trip inherited the whole accumulated scheduled dwell of the stops before
# it. This is a UNITS error, not a modelling choice: none of the four reference works has it,
# because all of them write a single time into both fields (Wessel et al., rt2gtfs, Braga
# et al.).
#
# MEASURED end to end on Prague 2026-07-18, P50 feed, mean arrival delay, changing only this
# switch (rows whose delay is exactly 0 dropped, as the published charts do):
#
#     route_type      scheduled dwell   production   passage     delta
#     0  tram         ~0                 +23.4 s     +23.4 s     0      <- natural control
#     11 trolleybus   0                  +84.2 s     +84.2 s     0      <- natural control
#     3  bus          ~0                 +27.4 s     +24.6 s     -2.8 s
#     1  metro        410 s/trip        +239.0 s     +16.1 s     -222.9 s
#     2  rail         large             +247.8 s     +12.2 s     -235.6 s
#     WHOLE FEED                         +49.7 s     +24.0 s     -51.7%
#
# The two zero-dwell route types do not move by a second, and the metro's shift matches the
# scheduled dwell its own static feed books (410 s/trip, ~205 s/row) - a prediction made from
# the static feed before the experiment. Whole-city controls agree: Lodz, which has no row at
# all with arrival_time != departure_time, rebuilds BYTE-IDENTICALLY under both modes, and
# Gdansk (845 such rows in 2.2M, 0.038%) changes 0.047% of rows and no printed statistic.
#
# (docs/reviews/family-a_experimental-verification.md §1 reports slightly different absolutes -
# +232.6/+8.9/-54% - because its prototype called collect_segment_observations without FA-10
# anchoring, which the CLI does use, and Prague is the one city of the four whose
# shape_dist_traveled is trustworthy. The mechanism, the controls and the size all agree.)
#
# "passage" runs the whole chain in passage times, which is the quantity actually measured, and
# writes arrival == departure. "production" reproduces the pre-2026-08-09 output and exists
# only to rebuild feeds published before that date.
DEFAULT_DWELL_MODE = "passage"


def segment_key_for(
    route_id: str,
    direction_id: str,
    from_stop_id: str,
    to_stop_id: str,
    day_type: str,
    time_bucket: int,
) -> SegmentKey:
    """Single source of truth for SegmentKey shape.

    Both collect_segment_observations (segment_stats.py) and rebuild_stop_times (this module)
    must build every SegmentKey through this function, never inline - so the two can never
    silently diverge (same discipline as the plugin's own RT3-5 segment_key_for).
    """
    return (route_id, direction_id, from_stop_id, to_stop_id, day_type, time_bucket)


# ---------------------------------------------------------------------------
# GTFS time helpers
# ---------------------------------------------------------------------------


def parse_gtfs_time(s: str) -> int:
    """Parse HH:MM:SS (HH may be >=24) -> seconds since service-day midnight."""
    h, m, sec = s.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + int(sec)


def format_gtfs_time(seconds: int) -> str:
    """Inverse of parse_gtfs_time; preserves HH>24 for overnight trips."""
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def interpolate_blank_stop_times(
    stops: list[tuple[int, str, int | None, int | None]],
) -> tuple[list[tuple[int, str, int, int]], set[int]]:
    """Fill in stops whose scheduled times were blank in stop_times.txt (FA-19).

    GTFS only requires arrival_time/departure_time at timepoints; leaving them blank in between
    is legal and standard, and the consumer is expected to interpolate. Before FA-19 this module
    coerced a blank to "0:0:0" - Python treats "" as falsy, so the `or` chain fell through to the
    default - which made rebuild_stop_times misbehave in three separate ways at once:

      1. prev -> blank booked `max(0, 0 - prev_dep)` = 0 s of travel, collapsing the blank stop
         onto the previous one;
      2. blank -> next timepoint booked `max(0, arr_sec - 0)` = that timepoint's ABSOLUTE clock
         time as a travel duration, compounding at every timepoint after a run of blanks
         (measured in Bucharest: 46h -> 69h -> 93h -> 117h -> 140h);
      3. the segment_stats lookup keys on time_bucket_for_seconds(prev_dep), so a fabricated 0
         sent it to the midnight bucket - a real 23:00 observation could never match, and the
         segment silently fell back to the very schedule that was broken.

    Measured on Bucharest 2026-07-22: 25,715 of 1,332,794 rows (1.93%) blank in 2,861 of 62,196
    trips (4.6%), and 0.67% of published rows carried 95.9% of the city's whole delay mass. The
    other 11 monitored cities have 0.00% blanks, so this defect is latent for any feed that
    starts using them.

    *stops* must be sorted by stop_sequence, with None for a stop whose arrival AND departure were
    both blank. A one-sided blank is NOT this case: load_static_index resolves it to the other
    field, which is correct and predates FA-19.

    Interpolation is linear by stop count, not weighted by distance. shape_dist_traveled would be
    the better basis where it exists, but Bucharest - the only affected feed - publishes not one
    value of it in stop_times.txt, so that branch would be dead code testable only synthetically.
    Note the structural obstacle too, before treating this as a small change: load_static_index
    runs BEFORE evaluate_shape_trust/evaluate_trip_trust (cli.py:896 vs cli.py:904-908) and has
    neither shapes.txt nor the trusted distances available. Adding the branch means reordering
    the load, not just adding a condition.

    Edge cases, none of which occur in any monitored feed (Bucharest has zero leading blanks, zero
    trailing blanks, zero all-blank trips, and blank runs of 1-6 always anchored on both sides):
      - leading blanks have no earlier anchor, so they clamp to the first known arrival;
      - trailing blanks clamp to the last known departure;
      - a trip with no times at all cannot be anchored to anything and keeps zeros.

    A non-monotonic pair of timepoints yields interpolated times running backwards; that is left
    alone deliberately, since rebuild_stop_times already clamps travel at >= 0 and would collapse
    such a run either way.

    Returns the stops with every None replaced by an int, and the set of stop_sequences filled in.
    """
    # Both fields required, not just arrival: load_static_index only ever produces both-or-neither,
    # but this function is public and separately tested, and a half-None row reaching the anchor
    # arithmetic below would raise TypeError instead of being treated as the blank it is.
    known = [
        i for i, (_seq, _sid, arr, dep) in enumerate(stops)
        if arr is not None and dep is not None
    ]

    if not known:
        return (
            [(seq, stop_id, 0, 0) for seq, stop_id, _arr, _dep in stops],
            {seq for seq, _sid, _arr, _dep in stops},
        )

    out: list[tuple[int, str, int, int]] = list(stops)  # type: ignore[assignment]
    filled: set[int] = set()

    def _set(idx: int, value: int) -> None:
        seq, stop_id, _arr, _dep = stops[idx]
        # Same value for both: a blank row is blank in both fields, so there is no dwell to model.
        out[idx] = (seq, stop_id, value, value)
        filled.add(seq)

    for idx in range(known[0]):
        _set(idx, stops[known[0]][2])

    for idx in range(known[-1] + 1, len(stops)):
        _set(idx, stops[known[-1]][3])

    for before, after in zip(known, known[1:]):
        gap = after - before - 1
        if gap <= 0:
            continue
        start = stops[before][3]   # previous stop's DEPARTURE
        end = stops[after][2]      # next stop's ARRIVAL
        for step in range(1, gap + 1):
            _set(before + step, int(round(start + (end - start) * step / (gap + 1))))

    return out, filled


# ---------------------------------------------------------------------------
# Static GTFS index
# ---------------------------------------------------------------------------


@dataclass
class StaticIndex:
    # trip_id -> (route_id, direction_id)
    trip_route: dict[str, tuple[str, str]]
    # trip_id -> [(seq, stop_id, arr_sec, dep_sec), ...] sorted by seq
    trip_stops: dict[str, list[tuple]]
    # (trip_id, stop_sequence) -> (stop_id, arr_sec, dep_sec)
    stop_map: dict[tuple[str, int], tuple[str, int, int]]
    # all trip_ids present in the static feed
    all_trip_ids: set[str]
    # trip_id -> service_id, for calendar_scope.load_service_day_types lookup
    trip_service_id: dict[str, str]
    # (trip_id, stop_sequence) -> shape_dist_traveled (None if blank/missing) - FA-10.
    # Defaults to {} so existing test helpers that build a StaticIndex directly
    # (not via load_static_index) don't need updating for a field they don't test.
    stop_time_dist_traveled: dict[tuple[str, int], float | None] = field(default_factory=dict)
    # (trip_id, stop_sequence) of every stop whose blank scheduled times were filled in by
    # interpolate_blank_stop_times - FA-19. Diagnostics only; nothing downstream branches on it.
    # Same default-empty precedent as stop_time_dist_traveled above.
    interpolated_time_stops: set[tuple[str, int]] = field(default_factory=set)


def load_static_index(gtfs_zip_path: str) -> StaticIndex:
    """Parse trips.txt and stop_times.txt from the static GTFS zip.

    Stops whose arrival_time and departure_time are both blank are interpolated between the
    surrounding timepoints rather than read as midnight - see interpolate_blank_stop_times
    (FA-19) for why that mattered and what it measured.
    """
    trip_route: dict[str, tuple[str, str]] = {}
    trip_service_id: dict[str, str] = {}
    trip_stops_raw: dict[str, list] = defaultdict(list)
    stop_map: dict[tuple[str, int], tuple[str, int, int]] = {}
    stop_time_dist_traveled: dict[tuple[str, int], float | None] = {}

    with zipfile.ZipFile(gtfs_zip_path) as zf:
        with zf.open("trips.txt") as fh:
            reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig"))
            for row in reader:
                trip_id = row["trip_id"]
                route_id = row.get("route_id", "")
                direction_id = row.get("direction_id", "0")
                trip_route[trip_id] = (route_id, direction_id)
                trip_service_id[trip_id] = row.get("service_id", "")

        with zf.open("stop_times.txt") as fh:
            reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig"))
            for row in reader:
                trip_id = row["trip_id"]
                seq = int(row.get("stop_sequence", 0))
                stop_id = row.get("stop_id", "")
                # FA-19: a blank is NOT midnight - it is a stop the feed expects the consumer to
                # interpolate, and is carried as None until interpolate_blank_stop_times fills it
                # below. A one-sided blank still resolves to the other field, exactly as before.
                arr_raw = (row.get("arrival_time") or "").strip()
                dep_raw = (row.get("departure_time") or "").strip()
                if arr_raw or dep_raw:
                    arr_sec = parse_gtfs_time(arr_raw or dep_raw)
                    dep_sec = parse_gtfs_time(dep_raw or arr_raw)
                else:
                    arr_sec = dep_sec = None
                trip_stops_raw[trip_id].append((seq, stop_id, arr_sec, dep_sec))
                # FA-10: never coerce a blank shape_dist_traveled to 0.0 - shape_dist.py's
                # fill-rate check needs to tell "genuinely zero" apart from "absent" to
                # correctly reject the Łódź/Vilnius trap (column present, every row blank).
                dist_raw = row.get("shape_dist_traveled") or ""
                stop_time_dist_traveled[(trip_id, seq)] = (
                    float(dist_raw) if dist_raw.strip() else None
                )

    trip_stops: dict[str, list[tuple]] = {}
    interpolated_time_stops: set[tuple[str, int]] = set()
    for tid, raw_stops in trip_stops_raw.items():
        ordered = sorted(raw_stops, key=lambda x: x[0])
        # Guarded so a feed without blanks - which is 11 of the 12 monitored cities - never pays
        # for the FA-19 path at all. stop_times.txt reaches 93.7MB on the largest of them.
        if any(arr is None for _seq, _sid, arr, _dep in ordered):
            # Read off the INPUT rather than inferred from len(filled_seqs) == len(ordered): that
            # comparison silently relies on stop_sequence being unique, and a feed with no
            # stop_sequence column at all gives every row seq 0 (see the int() default above).
            anchored = any(arr is not None for _seq, _sid, arr, _dep in ordered)
            ordered, filled_seqs = interpolate_blank_stop_times(ordered)
            if not anchored:
                logger.warning(
                    "build_gtfs.py: trip_id=%s has no scheduled times at all - nothing to "
                    "anchor an interpolation to, so its stops keep 00:00:00 and its rebuilt "
                    "times are not meaningful.",
                    tid,
                )
            interpolated_time_stops.update((tid, seq) for seq in filled_seqs)
        trip_stops[tid] = ordered
        # Derived from the finished list rather than filled during the parse loop above: the two
        # must never disagree about a stop's times, and interpolation happens after sorting.
        for seq, stop_id, arr_sec, dep_sec in ordered:
            stop_map[(tid, seq)] = (stop_id, arr_sec, dep_sec)

    return StaticIndex(
        trip_route=trip_route,
        trip_stops=trip_stops,
        stop_map=stop_map,
        all_trip_ids=set(trip_route.keys()),
        trip_service_id=trip_service_id,
        stop_time_dist_traveled=stop_time_dist_traveled,
        interpolated_time_stops=interpolated_time_stops,
    )


# ---------------------------------------------------------------------------
# Rebuild + repackage
# ---------------------------------------------------------------------------


def rebuild_stop_times(
    static_index: StaticIndex,
    segment_stats: dict[SegmentKey, float],
    service_day_types: dict[str, set[str]],
    bucket_minutes: int = 120,
    route_counts: dict[str, dict[str, int]] | None = None,
    dwell_mode: str = DEFAULT_DWELL_MODE,
) -> tuple[dict[tuple[str, int], tuple[int, int]], int, int]:
    """Compute corrected arrival/departure times for every stop in every trip.

    A correction is only accepted if the trip's own service actually runs on a day_type the
    recording covered (service_day_types), at the same scheduled time_bucket the recording
    observed - see calendar_scope.py. A trip whose service_id has no known active dates maps
    to an empty day_type set, which never matches any segment_stats key - it always falls back
    to the scheduled time, by design (never "matches everything").

    *route_counts* (FA-15, optional, mutated in place - same convention as
    interpolate_stop_time's own counts dict): when given, accumulates
    route_id -> {"corrected": int, "gap": int}, the per-route split of the two whole-feed totals
    this function already returns. Passed in rather than returned so the function's existing
    3-tuple contract - and every existing caller and test - is untouched.

    Deliberately computed here rather than by a second function re-deriving which segment keys
    matched: segment_key_for exists precisely so two code paths cannot silently disagree about
    key construction, and a parallel re-derivation would be exactly that risk.

    *dwell_mode* (D1) selects what the accumulator carries - see DEFAULT_DWELL_MODE above for
    the defect and its measurement:

    - "passage" (default): the whole chain runs in passage times, matching what the observed
      segment time measures. Passage 0 is the trip's scheduled DEPARTURE from its origin (that
      is when the vehicle leaves); every later passage is an arrival, so a gap segment's
      scheduled equivalent is arr[i] - dep[i-1] for the first pair and arr[i] - arr[i-1]
      afterwards. Output has arrival == departure at every stop but the origin, exactly as
      Wessel et al. / rt2gtfs / Braga et al. write it. Total journey time is preserved: an
      uncorrected trip still ends at its scheduled final arrival.
    - "production": the pre-2026-08-09 behaviour, kept only to rebuild already-published feeds.
      Scheduled dwell is added on top of an interval that already contains the real one.

    Both modes anchor each trip on its scheduled first departure and clamp monotonically.

    Returns:
      corrections: (trip_id, stop_sequence) -> (new_arr_sec, new_dep_sec)
      corrected_count: segments that used an observed segment time
      gap_count: segments that fell back to the scheduled duration
    """
    corrections: dict[tuple[str, int], tuple[int, int]] = {}
    corrected_count = 0
    gap_count = 0

    for trip_id, stops in static_index.trip_stops.items():
        if not stops:
            continue

        route_id, direction_id = static_index.trip_route.get(trip_id, ("", "0"))
        # Resolved once per trip rather than per stop_time: this loop runs over every row of the
        # whole static feed (millions on a large city), and setdefault's dict literal would
        # otherwise be allocated on every iteration just to be thrown away.
        route_bucket = (
            route_counts.setdefault(route_id, {"corrected": 0, "gap": 0})
            if route_counts is not None
            else None
        )
        service_id = static_index.trip_service_id.get(trip_id, "")
        trip_day_types = service_day_types.get(service_id, set())
        # Anchor the reconstructed timetable to the first stop's scheduled departure
        running_time = float(stops[0][3])

        for idx, (seq, stop_id, arr_sec, dep_sec) in enumerate(stops):
            if idx == 0:
                new_arr = float(arr_sec)
                new_dep = float(dep_sec)
            else:
                prev_seq, prev_stop_id, prev_arr, prev_dep = stops[idx - 1]
                if dwell_mode == "production":
                    sched_travel = max(0.0, float(arr_sec - prev_dep))
                    dwell = max(0.0, float(dep_sec - arr_sec))
                else:
                    # Passage 0 is the origin's DEPARTURE, every later passage an arrival - so
                    # the first pair anchors on prev_dep and all others on prev_arr. Written as
                    # the `else` deliberately: an unrecognised mode then lands on the corrected
                    # semantics, not the defective one.
                    anchor = prev_dep if idx == 1 else prev_arr
                    sched_travel = max(0.0, float(arr_sec - anchor))
                    dwell = 0.0

                time_bucket = time_bucket_for_seconds(prev_dep, bucket_minutes)
                travel = None
                # sorted(): trip_day_types is a set, whose iteration order is not
                # guaranteed stable across runs - a deterministic (alphabetical) order
                # matters once a trip's service spans >1 day_type (e.g. "runs every
                # day") and segment_stats has matches for more than one of them.
                for day_type in sorted(trip_day_types):
                    candidate_key = segment_key_for(
                        route_id, direction_id, prev_stop_id, stop_id, day_type, time_bucket
                    )
                    if candidate_key in segment_stats:
                        travel = segment_stats[candidate_key]
                        break

                if travel is not None:
                    corrected_count += 1
                    if route_bucket is not None:
                        route_bucket["corrected"] += 1
                else:
                    travel = sched_travel
                    gap_count += 1
                    if route_bucket is not None:
                        route_bucket["gap"] += 1

                new_arr = running_time + travel
                new_arr = max(new_arr, running_time)  # monotonic clamp
                new_dep = new_arr + dwell
                new_dep = max(new_dep, new_arr)       # monotonic clamp

            running_time = new_dep
            corrections[(trip_id, seq)] = (int(round(new_arr)), int(round(new_dep)))

    return corrections, corrected_count, gap_count


def repackage_gtfs(
    src_zip_path: str,
    out_zip_path: str,
    corrections: dict[tuple[str, int], tuple[int, int]],
) -> None:
    """Write a new GTFS zip identical to src_zip except for stop_times.txt.

    stop_times.txt is rewritten with corrected arrival/departure times. All
    other columns and all other files are preserved unchanged, byte-for-byte.
    """
    with zipfile.ZipFile(src_zip_path, "r") as src:
        with zipfile.ZipFile(out_zip_path, "w", zipfile.ZIP_DEFLATED) as out:
            for member in src.infolist():
                name = member.filename

                if name == "stop_times.txt":
                    # Streamed row-by-row (not buffered whole into memory) - the dominant
                    # RAM cost of `build` on large static feeds (e.g. Warsaw's 93.7MB
                    # stop_times.txt). newline="" on both wrappers is required: without it,
                    # universal-newline translation would double-process the explicit \r\n
                    # csv.DictWriter emits below (today's buffered version never hits this
                    # since it works on in-memory str, never a wrapped binary file object).
                    with src.open(name) as fh_in, out.open(member, "w") as fh_out:
                        text_in = io.TextIOWrapper(fh_in, encoding="utf-8-sig", newline="")
                        text_out = io.TextIOWrapper(fh_out, encoding="utf-8", newline="")
                        reader = csv.DictReader(text_in, restval="")
                        fieldnames = list(reader.fieldnames or [])
                        writer = csv.DictWriter(
                            text_out, fieldnames=fieldnames, lineterminator="\r\n",
                            extrasaction="ignore",
                        )
                        writer.writeheader()
                        for row in reader:
                            trip_id = row.get("trip_id", "")
                            seq = int(row.get("stop_sequence", 0))
                            key = (trip_id, seq)
                            if key in corrections:
                                new_arr, new_dep = corrections[key]
                                row["arrival_time"] = format_gtfs_time(new_arr)
                                row["departure_time"] = format_gtfs_time(new_dep)
                            writer.writerow(row)
                        # Must flush before the `with` block closes fh_out - TextIOWrapper
                        # buffers internally and the last chunk can be silently lost
                        # otherwise; do not rely on close()/GC to flush it.
                        text_out.flush()

                else:
                    out.writestr(member, src.read(name))
