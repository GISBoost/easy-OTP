"""Pure helpers for GTFS-RT realized-timetable reconstruction (RT-3).

No QGIS / GDAL imports — unit-testable without a QGIS environment.
Run tests: py -m pytest easy_otp/test/test_build_realized_gtfs.py -v

Method: Braga et al. (2023) segment-based P50/P85 aggregation.
Aggregates observed travel times by stop-pair segment across all trips/days,
avoiding the per-trip_id fragility on feeds whose trip_ids regenerate daily (Gdańsk).
"""

from __future__ import annotations

import csv
import io
import statistics
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from .gtfsrt_recorder import snapshot_hash

# segment_key = (route_id, direction_id, from_stop_id, to_stop_id)
SegmentKey = tuple[str, str, str, str]

# TripDescriptor.ScheduleRelationship
_SR_CANCELED = 3
# StopTimeUpdate.ScheduleRelationship
_STU_SKIPPED = 1


def segment_key_for(
    route_id: str,
    direction_id: str,
    from_stop_id: str,
    to_stop_id: str,
    matching_mode: str,
) -> SegmentKey:
    """Build the SegmentKey for either matching mode. Sole source of truth for its shape.

    TRIP_ID: (route_id, direction_id, from_stop_id, to_stop_id) — unchanged from pre-RT3-5.
    ROUTE_STOP_FALLBACK: direction is unknowable without a matched trip, so direction_id
    is replaced with "" — an intentional, documented loss of direction distinction.
    """
    if matching_mode == "ROUTE_STOP_FALLBACK":
        return (route_id, "", from_stop_id, to_stop_id)
    if matching_mode == "TRIP_ID":
        return (route_id, direction_id, from_stop_id, to_stop_id)
    raise ValueError(f"Unresolved matching_mode: {matching_mode!r}")


# ---------------------------------------------------------------------------
# GTFS time helpers
# ---------------------------------------------------------------------------

def parse_gtfs_time(s: str) -> int:
    """Parse HH:MM:SS (HH may be >=24) → seconds since service-day midnight."""
    h, m, sec = s.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + int(sec)


def format_gtfs_time(seconds: int) -> str:
    """Inverse of parse_gtfs_time; preserves HH>24 for overnight trips."""
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


# ---------------------------------------------------------------------------
# Static GTFS index
# ---------------------------------------------------------------------------

def _read_zip_column(zf: zipfile.ZipFile, filename: str, column: str) -> set[str]:
    """Return the set of values in `column` of a CSV member, or an empty set."""
    try:
        with zf.open(filename) as fh:
            reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig"))
            if not reader.fieldnames or column not in reader.fieldnames:
                return set()
            return {row[column].strip() for row in reader if row.get(column)}
    except KeyError:
        return set()


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
    # all route_ids present in the static feed's routes.txt (RT3-5 fallback matching)
    all_route_ids: set[str]
    # all stop_ids present in the static feed's stops.txt (RT3-5 fallback matching)
    all_stop_ids: set[str]


def load_static_index(gtfs_zip_path: str) -> StaticIndex:
    """Parse trips.txt and stop_times.txt from the static GTFS zip."""
    trip_route: dict[str, tuple[str, str]] = {}
    trip_stops_raw: dict[str, list] = defaultdict(list)
    stop_map: dict[tuple[str, int], tuple[str, int, int]] = {}

    with zipfile.ZipFile(gtfs_zip_path) as zf:
        with zf.open("trips.txt") as fh:
            reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig"))
            for row in reader:
                trip_id = row["trip_id"]
                route_id = row.get("route_id", "")
                direction_id = row.get("direction_id", "0")
                trip_route[trip_id] = (route_id, direction_id)

        with zf.open("stop_times.txt") as fh:
            reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig"))
            for row in reader:
                trip_id = row["trip_id"]
                seq = int(row.get("stop_sequence", 0))
                stop_id = row.get("stop_id", "")
                # KNOWN DIVERGENCE from tools/family_a_reconstruction (FA-19, 2026-07-30): an
                # empty string is falsy, so a blank arrival_time AND departure_time falls through
                # to "0:0:0" here - midnight. Blanks at non-timepoint stops are legal GTFS the
                # consumer is meant to interpolate, and reading them as zero makes rebuild_stop_times
                # below book the NEXT timepoint's absolute clock time as a travel duration
                # (measured on Bucharest's feed: rows reaching 141 hours). family_a fixed this in
                # build_gtfs.interpolate_blank_stop_times; this copy has NOT been fixed yet.
                arr_raw = row.get("arrival_time") or row.get("departure_time") or "0:0:0"
                dep_raw = row.get("departure_time") or row.get("arrival_time") or "0:0:0"
                arr_sec = parse_gtfs_time(arr_raw)
                dep_sec = parse_gtfs_time(dep_raw)
                trip_stops_raw[trip_id].append((seq, stop_id, arr_sec, dep_sec))
                stop_map[(trip_id, seq)] = (stop_id, arr_sec, dep_sec)

        all_route_ids = _read_zip_column(zf, "routes.txt", "route_id")
        all_stop_ids = _read_zip_column(zf, "stops.txt", "stop_id")

    trip_stops = {
        tid: sorted(stops, key=lambda x: x[0])
        for tid, stops in trip_stops_raw.items()
    }

    return StaticIndex(
        trip_route=trip_route,
        trip_stops=trip_stops,
        stop_map=stop_map,
        all_trip_ids=set(trip_route.keys()),
        all_route_ids=all_route_ids,
        all_stop_ids=all_stop_ids,
    )


def load_static_indices(paths: list[str]) -> tuple[StaticIndex, int]:
    """Load and merge one or more static GTFS zips into a single StaticIndex.

    Merges in the given order with first-file-wins trip_id semantics: if a
    trip_id already committed from an earlier file reappears in a later file,
    that later file's trip_route/trip_stops/stop_map entries for it are
    skipped entirely (never mixed key-by-key across files), so every trip's
    data always comes from exactly one source file. A single-element list
    produces output identical to calling load_static_index(paths[0]) directly.

    Returns (merged_index, collision_count) — collision_count is the number
    of distinct trip_ids seen in more than one input file, counted once per
    trip_id regardless of how many files (2 or more) it reappears in.

    all_route_ids/all_stop_ids are unioned across ALL input files unconditionally,
    independent of the first-file-wins trip_id collision policy above: fallback
    matching (RT3-5) only needs to know whether a route_id/stop_id exists anywhere
    in the combined static feed set, not which file's trip row "won" a collision.
    """
    trip_route: dict[str, tuple[str, str]] = {}
    trip_stops: dict[str, list[tuple]] = {}
    stop_map: dict[tuple[str, int], tuple[str, int, int]] = {}
    seen_trip_ids: set[str] = set()
    collided_trip_ids: set[str] = set()
    all_route_ids: set[str] = set()
    all_stop_ids: set[str] = set()

    for path in paths:
        try:
            idx = load_static_index(path)
        except Exception as exc:
            raise RuntimeError(f"{path}: {exc}") from exc

        new_trip_ids = idx.all_trip_ids - seen_trip_ids
        collided_trip_ids |= idx.all_trip_ids & seen_trip_ids

        for trip_id in new_trip_ids:
            trip_route[trip_id] = idx.trip_route[trip_id]
            if trip_id in idx.trip_stops:
                trip_stops[trip_id] = idx.trip_stops[trip_id]

        for (trip_id, seq), value in idx.stop_map.items():
            if trip_id in new_trip_ids:
                stop_map[(trip_id, seq)] = value

        seen_trip_ids |= new_trip_ids
        all_route_ids |= idx.all_route_ids
        all_stop_ids |= idx.all_stop_ids

    return (
        StaticIndex(
            trip_route=trip_route,
            trip_stops=trip_stops,
            stop_map=stop_map,
            all_trip_ids=set(seen_trip_ids),
            all_route_ids=all_route_ids,
            all_stop_ids=all_stop_ids,
        ),
        len(collided_trip_ids),
    )


# ---------------------------------------------------------------------------
# Snapshot decoder (lazy protobuf import — only called if bootstrap succeeded)
# ---------------------------------------------------------------------------

def decode_snapshot(data: bytes):
    """Decode raw .pb bytes → FeedMessage. Lazy import of gtfs_realtime_pb2."""
    from google.transit import gtfs_realtime_pb2  # noqa: PLC0415
    fm = gtfs_realtime_pb2.FeedMessage()
    fm.ParseFromString(data)
    return fm


# ---------------------------------------------------------------------------
# Segment-time collector
# ---------------------------------------------------------------------------

def _get_observed_departure(stu) -> tuple[int, int]:
    """Return (abs_time, delay) for the departure (or arrival if no departure)."""
    if stu.HasField("departure"):
        return stu.departure.time, stu.departure.delay
    if stu.HasField("arrival"):
        return stu.arrival.time, stu.arrival.delay
    return 0, 0


def _get_observed_arrival(stu) -> tuple[int, int]:
    """Return (abs_time, delay) for the arrival (or departure if no arrival)."""
    if stu.HasField("arrival"):
        return stu.arrival.time, stu.arrival.delay
    if stu.HasField("departure"):
        return stu.departure.time, stu.departure.delay
    return 0, 0


def _has_event(stu) -> bool:
    return stu.HasField("arrival") or stu.HasField("departure")


def _collect_segment_times_cross_snapshot(
    snapshot_paths: list[Path],
    static_index: StaticIndex,
    canceled_policy: str,
    progress_cb: Optional[Callable[[float], None]],
    cancel_check: Optional[Callable[[], bool]],
    matching_mode: str,
) -> tuple[dict[SegmentKey, list[float]], set[str], int]:
    """CROSS_SNAPSHOT segment collection (RT3-6, #18): stitch per-stop observations
    for the same trip_id across the whole snapshot archive, for feeds whose
    TripUpdates carry only the next stop (e.g. Poznan), so collect_segment_times'
    PER_MESSAGE adjacent-pair loop never has a second stop to pair with.

    Pass 1: chronological walk over snapshot_paths, recording one observed
    (arrival, departure, stop_id) anchor per (trip_id, stop_sequence) — a later
    snapshot's observation overwrites an earlier one (chronological order means
    "last write wins" == "most mature observation wins", the same principle as
    reconcile_last_snapshot, applied per-stop instead of per-pair; consequently
    reconcile_last_snapshot has no additional effect in this mode). A SKIPPED
    StopTimeUpdate is never anchored (treated as unobserved, same as today's
    _STU_SKIPPED filter) — it also does not overwrite or invalidate an existing
    anchor from an earlier snapshot for the same (trip_id, stop_sequence); it is
    simply not written, leaving whatever was already recorded in place.

    Pass 2: per trip_id, pairs of strictly consecutive observed stop_sequence
    values are turned into segments exactly as in the PER_MESSAGE branch (same
    absolute-time-preferred / scheduled+delay fallback for TRIP_ID mode, same
    absolute-time requirement for ROUTE_STOP_FALLBACK mode, same >2h/non-positive
    filter, same segment_key_for). A sequence gap (an intermediate stop never
    observed) is skipped and counted, never interpolated.

    Returns the same 3-tuple shape as collect_segment_times; the 3rd value here
    counts stop-pairs skipped in Pass 2 (sequence gaps, or — in
    ROUTE_STOP_FALLBACK matching mode — pairs lacking an absolute observed
    time) — a different meaning from PER_MESSAGE's fallback_time_skipped.
    """
    # (trip_id, stop_sequence) -> (arr_abs, arr_delay, dep_abs, dep_delay, stop_id)
    anchors: dict[tuple[str, int], tuple[int, int, int, int, str]] = {}
    trip_meta: dict[str, tuple[str, str]] = {}
    canceled_trip_ids: set[str] = set()
    total = len(snapshot_paths)

    for i, path in enumerate(snapshot_paths):
        if cancel_check and cancel_check():
            break

        try:
            feed = decode_snapshot(path.read_bytes())
        except Exception:  # noqa: BLE001 — corrupt / unreadable snapshot
            if progress_cb:
                progress_cb((i + 1) / total)
            continue

        for entity in feed.entity:
            if not entity.HasField("trip_update"):
                continue
            tu = entity.trip_update
            trip_id = tu.trip.trip_id

            if tu.trip.schedule_relationship == _SR_CANCELED:
                canceled_trip_ids.add(trip_id)
                if canceled_policy == "skip":
                    continue

            if matching_mode == "ROUTE_STOP_FALLBACK":
                route_id = tu.trip.route_id
                if not route_id or route_id not in static_index.all_route_ids:
                    continue
                direction_id = ""
            else:
                if trip_id not in static_index.trip_route:
                    continue
                route_id, direction_id = static_index.trip_route[trip_id]

            trip_meta[trip_id] = (route_id, direction_id)

            for stu in tu.stop_time_update:
                if getattr(stu, "schedule_relationship", 0) == _STU_SKIPPED:
                    continue
                if not _has_event(stu):
                    continue
                arr_abs, arr_delay = _get_observed_arrival(stu)
                dep_abs, dep_delay = _get_observed_departure(stu)
                anchors[(trip_id, stu.stop_sequence)] = (
                    arr_abs, arr_delay, dep_abs, dep_delay, stu.stop_id
                )

        if progress_cb:
            progress_cb((i + 1) / total)

    segment_times: dict[SegmentKey, list[float]] = defaultdict(list)
    skipped_pairs = 0

    seqs_by_trip: dict[str, list[int]] = defaultdict(list)
    for trip_id, seq in anchors:
        seqs_by_trip[trip_id].append(seq)

    for trip_id, seqs in seqs_by_trip.items():
        route_id, direction_id = trip_meta[trip_id]
        seqs.sort()

        for k in range(len(seqs) - 1):
            from_seq = seqs[k]
            to_seq = seqs[k + 1]
            if to_seq != from_seq + 1:
                skipped_pairs += 1
                continue

            arr_abs_from, arr_delay_from, dep_abs_from, dep_delay_from, from_stop_id = (
                anchors[(trip_id, from_seq)]
            )
            arr_abs_to, arr_delay_to, dep_abs_to, dep_delay_to, to_stop_id = (
                anchors[(trip_id, to_seq)]
            )

            if matching_mode == "ROUTE_STOP_FALLBACK":
                if not from_stop_id or from_stop_id not in static_index.all_stop_ids:
                    continue
                if not to_stop_id or to_stop_id not in static_index.all_stop_ids:
                    continue
                if dep_abs_from <= 0 or arr_abs_to <= 0:
                    skipped_pairs += 1
                    continue
                seg_time = float(arr_abs_to - dep_abs_from)
            else:
                from_entry = static_index.stop_map.get((trip_id, from_seq))
                to_entry = static_index.stop_map.get((trip_id, to_seq))
                if not from_entry or not to_entry:
                    continue

                from_stop_id = from_entry[0]
                to_stop_id = to_entry[0]
                sched_dep = from_entry[2]   # dep_sec at from_stop
                sched_arr = to_entry[1]     # arr_sec at to_stop

                if dep_abs_from > 0 and arr_abs_to > 0:
                    seg_time = float(arr_abs_to - dep_abs_from)
                else:
                    seg_time = float(
                        (sched_arr + arr_delay_to) - (sched_dep + dep_delay_from)
                    )

            # Reject non-positive or implausibly long segments (> 2 h)
            if seg_time <= 0 or seg_time > 7200:
                continue

            key = segment_key_for(
                route_id, direction_id, from_stop_id, to_stop_id, matching_mode
            )
            segment_times[key].append(seg_time)

    return dict(segment_times), canceled_trip_ids, skipped_pairs


def collect_segment_times(
    snapshot_paths: list[Path],
    static_index: StaticIndex,
    canceled_policy: str = "skip",
    progress_cb: Optional[Callable[[float], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    reconcile_last_snapshot: bool = True,
    matching_mode: str = "TRIP_ID",
    segment_source_mode: str = "PER_MESSAGE",
) -> tuple[dict[SegmentKey, list[float]], set[str], int]:
    """Parse each .pb snapshot; return (segment_times, canceled_trip_ids, fallback_time_skipped).

    segment_times: SegmentKey -> list of observed travel times (seconds)
    canceled_trip_ids: trip_ids seen with CANCELED status (for drop logic)
    fallback_time_skipped: pairs skipped in ROUTE_STOP_FALLBACK mode for lacking an
    absolute observed time (no matched static trip to anchor a bare delay against).
    Always 0 in TRIP_ID mode. In CROSS_SNAPSHOT mode this slot instead counts
    stop-pairs skipped in its second pass (non-consecutive stop_sequence gaps, or
    — in ROUTE_STOP_FALLBACK matching mode — pairs missing an absolute observed
    time) — see _collect_segment_times_cross_snapshot.

    reconcile_last_snapshot: when True (default), keep only the chronologically
    last snapshot's observation per (trip_id, from_seq, to_seq) — predictions
    made closer to the actual event are more accurate, so repeated observations
    of the same trip-segment across snapshots are collapsed to one. When False,
    every snapshot's observation is appended (pre-0.7 behavior). Assumes
    snapshot_paths is already in chronological order. Applies identically in both
    matching modes: grouping uses the RT-side trip_id regardless of whether it
    matches the static feed, so it needs no knowledge of matching_mode. Has no
    additional effect when segment_source_mode is CROSS_SNAPSHOT — that mode
    already reconciles per-stop, unconditionally, while collecting.

    matching_mode: "TRIP_ID" (default) requires an exact static trip_id match, as
    before RT3-5. "ROUTE_STOP_FALLBACK" instead resolves route_id/stop_id directly
    from the RT message and validates them against the static feed's known ids —
    for feeds (Poznan, Krakow) whose trip_id namespace is permanently disjoint from
    the static feed's. direction_id cannot be recovered in this mode (no matched
    trip); segment_key_for zeroes it.

    segment_source_mode: "PER_MESSAGE" (default) is the exact pre-RT3-6 code path
    below, unchanged — segments are computed only from adjacent StopTimeUpdate
    pairs within a single message. "CROSS_SNAPSHOT" (RT3-6, #18) instead stitches
    per-stop observations for the same trip_id across the whole snapshot archive,
    for feeds (e.g. Poznan) whose TripUpdates always carry a single StopTimeUpdate,
    so the PER_MESSAGE adjacent-pair loop never has a second stop to pair with —
    see _collect_segment_times_cross_snapshot.
    """
    if segment_source_mode == "CROSS_SNAPSHOT":
        return _collect_segment_times_cross_snapshot(
            snapshot_paths, static_index, canceled_policy,
            progress_cb, cancel_check, matching_mode,
        )

    # --- PER_MESSAGE: everything below is unchanged from before RT3-6 ---
    segment_times: dict[SegmentKey, list[float]] = defaultdict(list)
    canceled_trip_ids: set[str] = set()
    fallback_time_skipped = 0
    # (trip_id, from_seq, to_seq) -> (seg_time, SegmentKey) of the chronologically
    # last snapshot with a complete, passing observation for that key.
    # Only populated/consumed when reconcile_last_snapshot=True.
    latest_per_trip_segment: dict[tuple[str, int, int], tuple[float, SegmentKey]] = {}
    total = len(snapshot_paths)

    for i, path in enumerate(snapshot_paths):
        if cancel_check and cancel_check():
            break

        try:
            feed = decode_snapshot(path.read_bytes())
        except Exception:  # noqa: BLE001 — corrupt / unreadable snapshot
            if progress_cb:
                progress_cb((i + 1) / total)
            continue

        for entity in feed.entity:
            if not entity.HasField("trip_update"):
                continue
            tu = entity.trip_update
            trip_id = tu.trip.trip_id

            if tu.trip.schedule_relationship == _SR_CANCELED:
                canceled_trip_ids.add(trip_id)
                if canceled_policy == "skip":
                    continue

            if matching_mode == "ROUTE_STOP_FALLBACK":
                route_id = tu.trip.route_id
                if not route_id or route_id not in static_index.all_route_ids:
                    continue
                direction_id = ""
            else:
                if trip_id not in static_index.trip_route:
                    continue
                route_id, direction_id = static_index.trip_route[trip_id]

            updates = [
                s for s in tu.stop_time_update
                if getattr(s, "schedule_relationship", 0) != _STU_SKIPPED
            ]
            updates.sort(key=lambda s: s.stop_sequence)

            for j in range(len(updates) - 1):
                stu_from = updates[j]
                stu_to = updates[j + 1]

                if not _has_event(stu_from) or not _has_event(stu_to):
                    continue

                from_seq = stu_from.stop_sequence
                to_seq = stu_to.stop_sequence

                if matching_mode == "ROUTE_STOP_FALLBACK":
                    from_stop_id = stu_from.stop_id
                    to_stop_id = stu_to.stop_id
                    if not from_stop_id or from_stop_id not in static_index.all_stop_ids:
                        continue
                    if not to_stop_id or to_stop_id not in static_index.all_stop_ids:
                        continue

                    dep_abs, _dep_delay = _get_observed_departure(stu_from)
                    arr_abs, _arr_delay = _get_observed_arrival(stu_to)
                    if dep_abs <= 0 or arr_abs <= 0:
                        fallback_time_skipped += 1
                        continue
                    seg_time = float(arr_abs - dep_abs)
                else:
                    from_entry = static_index.stop_map.get((trip_id, from_seq))
                    to_entry = static_index.stop_map.get((trip_id, to_seq))
                    if not from_entry or not to_entry:
                        continue

                    from_stop_id = from_entry[0]
                    to_stop_id = to_entry[0]
                    sched_dep = from_entry[2]   # dep_sec at from_stop
                    sched_arr = to_entry[1]     # arr_sec at to_stop

                    dep_abs, dep_delay = _get_observed_departure(stu_from)
                    arr_abs, arr_delay = _get_observed_arrival(stu_to)

                    if dep_abs > 0 and arr_abs > 0:
                        seg_time = float(arr_abs - dep_abs)
                    else:
                        seg_time = float((sched_arr + arr_delay) - (sched_dep + dep_delay))

                # Reject non-positive or implausibly long segments (> 2 h)
                if seg_time <= 0 or seg_time > 7200:
                    continue

                key = segment_key_for(
                    route_id, direction_id, from_stop_id, to_stop_id, matching_mode
                )
                if reconcile_last_snapshot:
                    latest_per_trip_segment[(trip_id, from_seq, to_seq)] = (seg_time, key)
                else:
                    segment_times[key].append(seg_time)

        if progress_cb:
            progress_cb((i + 1) / total)

    if reconcile_last_snapshot:
        for seg_time, key in latest_per_trip_segment.values():
            segment_times[key].append(seg_time)

    return dict(segment_times), canceled_trip_ids, fallback_time_skipped


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------

def aggregate_segments(
    collected: dict[SegmentKey, list[float]],
) -> tuple[dict[SegmentKey, float], dict[SegmentKey, float]]:
    """Return (p50_stats, p85_stats) using stdlib statistics.

    P50 = median; P85 = 85th percentile.  Invariant: p85 >= p50 per segment.
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


# ---------------------------------------------------------------------------
# Stop-times rebuilder
# ---------------------------------------------------------------------------

def rebuild_stop_times(
    static_index: StaticIndex,
    segment_stats: dict[SegmentKey, float],
    drop_trip_ids: frozenset[str] = frozenset(),
    matching_mode: str = "TRIP_ID",
) -> tuple[dict[tuple[str, int], tuple[int, int]], int, int]:
    """Compute corrected arrival/departure times for every stop in every trip.

    Returns:
      corrections: (trip_id, stop_sequence) -> (new_arr_sec, new_dep_sec)
      corrected_count: segments that used an observed segment time
      gap_count: segments that fell back to the scheduled duration

    matching_mode must match whatever mode segment_stats was built with
    (collect_segment_times' matching_mode), since it determines the SegmentKey
    shape via segment_key_for — this function still walks the static feed's
    trip_stops (always trip-keyed) regardless of mode; only the lookup key changes.
    """
    corrections: dict[tuple[str, int], tuple[int, int]] = {}
    corrected_count = 0
    gap_count = 0

    for trip_id, stops in static_index.trip_stops.items():
        if trip_id in drop_trip_ids or not stops:
            continue

        route_id, direction_id = static_index.trip_route.get(trip_id, ("", "0"))
        # Anchor the reconstructed timetable to the first stop's scheduled departure
        running_time = float(stops[0][3])

        for idx, (seq, stop_id, arr_sec, dep_sec) in enumerate(stops):
            if idx == 0:
                new_arr = float(arr_sec)
                new_dep = float(dep_sec)
            else:
                prev_seq, prev_stop_id, _prev_arr, prev_dep = stops[idx - 1]
                key = segment_key_for(
                    route_id, direction_id, prev_stop_id, stop_id, matching_mode
                )
                sched_travel = max(0.0, float(arr_sec - prev_dep))
                dwell = max(0.0, float(dep_sec - arr_sec))

                if key in segment_stats:
                    travel = segment_stats[key]
                    corrected_count += 1
                else:
                    travel = sched_travel
                    gap_count += 1

                new_arr = running_time + travel
                new_arr = max(new_arr, running_time)  # monotonic clamp
                new_dep = new_arr + dwell
                new_dep = max(new_dep, new_arr)       # monotonic clamp

            running_time = new_dep
            corrections[(trip_id, seq)] = (int(round(new_arr)), int(round(new_dep)))

    return corrections, corrected_count, gap_count


# ---------------------------------------------------------------------------
# GTFS repackager
# ---------------------------------------------------------------------------

def repackage_gtfs(
    src_zip_path: str,
    out_zip_path: str,
    corrections: dict[tuple[str, int], tuple[int, int]],
    drop_trip_ids: frozenset[str] = frozenset(),
) -> None:
    """Write a new GTFS zip identical to src_zip except for stop_times.txt.

    stop_times.txt is rewritten with corrected arrival/departure times.
    All other columns and all other files are preserved unchanged.
    If drop_trip_ids is non-empty, those trips are also removed from trips.txt.
    """
    with zipfile.ZipFile(src_zip_path, "r") as src:
        with zipfile.ZipFile(out_zip_path, "w", zipfile.ZIP_DEFLATED) as out:
            for member in src.infolist():
                name = member.filename

                if name == "stop_times.txt":
                    raw = src.read(name)
                    reader = csv.DictReader(
                        io.StringIO(raw.decode("utf-8-sig")),
                        restval="",
                    )
                    fieldnames = list(reader.fieldnames or [])
                    buf = io.StringIO()
                    writer = csv.DictWriter(
                        buf, fieldnames=fieldnames, lineterminator="\r\n",
                        extrasaction="ignore",
                    )
                    writer.writeheader()
                    for row in reader:
                        trip_id = row.get("trip_id", "")
                        if trip_id in drop_trip_ids:
                            continue
                        seq = int(row.get("stop_sequence", 0))
                        key = (trip_id, seq)
                        if key in corrections:
                            new_arr, new_dep = corrections[key]
                            row["arrival_time"] = format_gtfs_time(new_arr)
                            row["departure_time"] = format_gtfs_time(new_dep)
                        writer.writerow(row)
                    out.writestr(member, buf.getvalue().encode("utf-8"))

                elif name == "trips.txt" and drop_trip_ids:
                    raw = src.read(name)
                    reader = csv.DictReader(
                        io.StringIO(raw.decode("utf-8-sig")),
                        restval="",
                    )
                    fieldnames = list(reader.fieldnames or [])
                    buf = io.StringIO()
                    writer = csv.DictWriter(
                        buf, fieldnames=fieldnames, lineterminator="\r\n",
                        extrasaction="ignore",
                    )
                    writer.writeheader()
                    for row in reader:
                        if row.get("trip_id", "") not in drop_trip_ids:
                            writer.writerow(row)
                    out.writestr(member, buf.getvalue().encode("utf-8"))

                else:
                    out.writestr(member, src.read(name))


# ---------------------------------------------------------------------------
# Pre-flight overlap check
# ---------------------------------------------------------------------------

def check_trip_overlap(
    snapshot_paths: list[Path],
    static_index: StaticIndex,
) -> float:
    """Sample up to 5 snapshots; return fraction of TripUpdate trip_ids in static.

    Returns 0.0 if no snapshots are readable or contain trip updates.
    A result < 0.05 suggests the static feed does not match the archive's service date.
    """
    sample = snapshot_paths[:5]
    all_seen = 0
    in_static = 0
    for path in sample:
        try:
            feed = decode_snapshot(path.read_bytes())
        except Exception:  # noqa: BLE001  # nosec B112 — corrupt/unreadable snapshot, best-effort sample
            continue
        for entity in feed.entity:
            if entity.HasField("trip_update"):
                all_seen += 1
                if entity.trip_update.trip.trip_id in static_index.all_trip_ids:
                    in_static += 1
    if all_seen == 0:
        return 0.0
    return in_static / all_seen


def sample_feed_capabilities(
    snapshot_paths: list[Path],
    static_index: StaticIndex,
) -> dict:
    """Sample up to 5 snapshots and report what a fallback join could rely on.

    Returns a dict with keys: route_id_overlap, stop_id_presence_ratio, stop_id_overlap,
    absolute_time_ratio — each a float in [0, 1]. Does not raise; returns zeros if no
    snapshots are readable or the relevant denominator is empty.
    """
    sample = snapshot_paths[:5]
    route_id_entities = 0
    route_id_in_static = 0
    stu_total = 0
    stop_id_present = 0
    stop_id_in_static = 0
    event_total = 0
    abs_time_present = 0

    for path in sample:
        try:
            feed = decode_snapshot(path.read_bytes())
        except Exception:  # noqa: BLE001  # nosec B112 — corrupt/unreadable snapshot, best-effort sample
            continue
        for entity in feed.entity:
            if not entity.HasField("trip_update"):
                continue
            tu = entity.trip_update

            route_id = tu.trip.route_id
            if route_id:
                route_id_entities += 1
                if route_id in static_index.all_route_ids:
                    route_id_in_static += 1

            for stu in tu.stop_time_update:
                stu_total += 1
                stop_id = stu.stop_id
                if stop_id:
                    stop_id_present += 1
                    if stop_id in static_index.all_stop_ids:
                        stop_id_in_static += 1

                if stu.HasField("arrival"):
                    event_total += 1
                    if stu.arrival.time != 0:
                        abs_time_present += 1
                if stu.HasField("departure"):
                    event_total += 1
                    if stu.departure.time != 0:
                        abs_time_present += 1

    return {
        "route_id_overlap": (
            route_id_in_static / route_id_entities if route_id_entities else 0.0
        ),
        "stop_id_presence_ratio": (
            stop_id_present / stu_total if stu_total else 0.0
        ),
        "stop_id_overlap": (
            stop_id_in_static / stop_id_present if stop_id_present else 0.0
        ),
        "absolute_time_ratio": (
            abs_time_present / event_total if event_total else 0.0
        ),
    }


def sample_message_shape(snapshot_paths: list[Path]) -> dict:
    """Sample up to 5 snapshots; report the StopTimeUpdate-per-TripUpdate shape.

    Returns a dict with keys: median_stop_updates_per_trip (int) — the median
    StopTimeUpdate count per TripUpdate, pooled across all sampled TripUpdates
    (one median over the pooled counts, not averaged per-snapshot); and
    single_stop_fraction (float in [0, 1]) — the fraction of sampled TripUpdates
    carrying exactly one StopTimeUpdate (the Poznan-shaped next-stop-only
    signature, RT3-6 / #18). Does not raise; returns zeros if no snapshots are
    readable or no TripUpdates are found in the sample.
    """
    sample = snapshot_paths[:5]
    counts: list[int] = []
    for path in sample:
        try:
            feed = decode_snapshot(path.read_bytes())
        except Exception:  # noqa: BLE001  # nosec B112 — corrupt/unreadable snapshot, best-effort sample
            continue
        for entity in feed.entity:
            if entity.HasField("trip_update"):
                counts.append(len(entity.trip_update.stop_time_update))

    if not counts:
        return {"median_stop_updates_per_trip": 0, "single_stop_fraction": 0.0}
    return {
        "median_stop_updates_per_trip": int(round(statistics.median(counts))),
        "single_stop_fraction": sum(1 for c in counts if c == 1) / len(counts),
    }


def check_snapshot_time_span(snapshot_paths: list[Path]) -> float:
    """Return the time span (seconds) between the earliest and latest snapshot filename.

    Parses ``snapshot_YYYYmmdd-HHMMSS.pb`` filenames (the format written by
    ``gtfsrt_recorder.snapshot_filename``). Returns 0.0 if fewer than 2 paths are given or
    if filenames don't match the expected pattern (best-effort — never raises).
    """
    timestamps: list[datetime] = []
    for path in snapshot_paths:
        try:
            timestamps.append(datetime.strptime(path.stem, "snapshot_%Y%m%d-%H%M%S"))
        except ValueError:
            continue

    if len(timestamps) < 2:
        return 0.0
    return (max(timestamps) - min(timestamps)).total_seconds()


def deduplicate_snapshots(snapshot_paths: list[Path]) -> tuple[list[Path], int]:
    """Drop snapshots whose bytes are identical to the immediately preceding kept snapshot.

    Collapses a run of byte-identical consecutive snapshots (e.g. a frozen
    upstream feed — the mkuran.pl Polish national rail aggregate feed is
    documented to stop updating for over an hour, once or twice a day) down
    to a single kept snapshot, so a frozen period contributes at most one
    observation to the P50/P85 aggregation pool instead of N.

    Compares each snapshot against the last successfully *kept* snapshot,
    not merely the previous file in the list — this is what correctly
    collapses a long frozen run into one kept snapshot instead of keeping
    every other one. A snapshot that fails to read is kept as-is (never
    silently dropped on error) and does not reset the comparison, so an
    isolated read glitch in the middle of a genuine frozen run does not
    re-admit a duplicate into the pool.

    Returns (kept_paths, dropped_count).
    """
    kept: list[Path] = []
    last_hash: Optional[str] = None
    dropped = 0

    for path in snapshot_paths:
        try:
            data = path.read_bytes()
        except OSError:
            kept.append(path)
            continue

        h = snapshot_hash(data)
        if h == last_hash:
            dropped += 1
            continue

        kept.append(path)
        last_hash = h

    return kept, dropped
