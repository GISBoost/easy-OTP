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
import zipfile
from collections import defaultdict
from dataclasses import dataclass

from family_a.calendar_scope import time_bucket_for_seconds

# segment_key = (route_id, direction_id, from_stop_id, to_stop_id, day_type, time_bucket)
SegmentKey = tuple[str, str, str, str, str, int]


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


def load_static_index(gtfs_zip_path: str) -> StaticIndex:
    """Parse trips.txt and stop_times.txt from the static GTFS zip."""
    trip_route: dict[str, tuple[str, str]] = {}
    trip_service_id: dict[str, str] = {}
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
                trip_service_id[trip_id] = row.get("service_id", "")

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
        trip_service_id=trip_service_id,
    )


# ---------------------------------------------------------------------------
# Rebuild + repackage
# ---------------------------------------------------------------------------


def rebuild_stop_times(
    static_index: StaticIndex,
    segment_stats: dict[SegmentKey, float],
    service_day_types: dict[str, set[str]],
    bucket_minutes: int = 120,
) -> tuple[dict[tuple[str, int], tuple[int, int]], int, int]:
    """Compute corrected arrival/departure times for every stop in every trip.

    A correction is only accepted if the trip's own service actually runs on a day_type the
    recording covered (service_day_types), at the same scheduled time_bucket the recording
    observed - see calendar_scope.py. A trip whose service_id has no known active dates maps
    to an empty day_type set, which never matches any segment_stats key - it always falls back
    to the scheduled time, by design (never "matches everything").

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
        service_id = static_index.trip_service_id.get(trip_id, "")
        trip_day_types = service_day_types.get(service_id, set())
        # Anchor the reconstructed timetable to the first stop's scheduled departure
        running_time = float(stops[0][3])

        for idx, (seq, stop_id, arr_sec, dep_sec) in enumerate(stops):
            if idx == 0:
                new_arr = float(arr_sec)
                new_dep = float(dep_sec)
            else:
                prev_seq, prev_stop_id, _prev_arr, prev_dep = stops[idx - 1]
                sched_travel = max(0.0, float(arr_sec - prev_dep))
                dwell = max(0.0, float(dep_sec - arr_sec))

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
