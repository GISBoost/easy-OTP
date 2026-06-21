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
from pathlib import Path
from typing import Callable, Optional

# segment_key = (route_id, direction_id, from_stop_id, to_stop_id)
SegmentKey = tuple[str, str, str, str]

# TripDescriptor.ScheduleRelationship
_SR_CANCELED = 3
# StopTimeUpdate.ScheduleRelationship
_STU_SKIPPED = 1


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
                arr_raw = row.get("arrival_time") or row.get("departure_time") or "0:0:0"
                dep_raw = row.get("departure_time") or row.get("arrival_time") or "0:0:0"
                arr_sec = parse_gtfs_time(arr_raw)
                dep_sec = parse_gtfs_time(dep_raw)
                trip_stops_raw[trip_id].append((seq, stop_id, arr_sec, dep_sec))
                stop_map[(trip_id, seq)] = (stop_id, arr_sec, dep_sec)

    trip_stops = {
        tid: sorted(stops, key=lambda x: x[0])
        for tid, stops in trip_stops_raw.items()
    }

    return StaticIndex(
        trip_route=trip_route,
        trip_stops=trip_stops,
        stop_map=stop_map,
        all_trip_ids=set(trip_route.keys()),
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


def collect_segment_times(
    snapshot_paths: list[Path],
    static_index: StaticIndex,
    canceled_policy: str = "skip",
    progress_cb: Optional[Callable[[float], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> tuple[dict[SegmentKey, list[float]], set[str]]:
    """Parse each .pb snapshot; return (segment_times, canceled_trip_ids).

    segment_times: SegmentKey -> list of observed travel times (seconds)
    canceled_trip_ids: trip_ids seen with CANCELED status (for drop logic)
    """
    segment_times: dict[SegmentKey, list[float]] = defaultdict(list)
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

                key: SegmentKey = (route_id, direction_id, from_stop_id, to_stop_id)
                segment_times[key].append(seg_time)

        if progress_cb:
            progress_cb((i + 1) / total)

    return dict(segment_times), canceled_trip_ids


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
) -> tuple[dict[tuple[str, int], tuple[int, int]], int, int]:
    """Compute corrected arrival/departure times for every stop in every trip.

    Returns:
      corrections: (trip_id, stop_sequence) -> (new_arr_sec, new_dep_sec)
      corrected_count: segments that used an observed segment time
      gap_count: segments that fell back to the scheduled duration
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
                key: SegmentKey = (route_id, direction_id, prev_stop_id, stop_id)
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
        except Exception:  # noqa: BLE001
            continue
        for entity in feed.entity:
            if entity.HasField("trip_update"):
                all_seen += 1
                if entity.trip_update.trip.trip_id in static_index.all_trip_ids:
                    in_static += 1
    if all_seen == 0:
        return 0.0
    return in_static / all_seen
