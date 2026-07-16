"""Map-match VehiclePosition observations onto static GTFS shapes (FA-2).

Turns raw GTFS-RT VehiclePosition snapshots (recorded by FA-1's recorder)
into a `(trip_id, timestamp, distance_along_shape_m)` series per trip — the
map-matching step of a Family A (Wessel 2017 / rt2gtfs 2026 style) realized-
GTFS reconstruction. FA-3 will later interpolate stop-crossing times from
this series.

Standalone tool code: never imports easy_otp/, never imports osgeo/QGIS, and
is never imported by the plugin. Read easy_otp/core/gtfsrt_realizer.py for
parsing-style reference only (not imported).

Projection is a simple 2D nearest-segment search treating (lon, lat) as
planar (x, y) in degrees, without correcting for longitude compression at
higher latitudes (cos(lat) narrowing near Poland's ~52N). At city scale this
occasionally picks a different "nearest" segment than a properly-scaled
metric projection would on near-tied candidates, but the chosen segment is
still always close to the true nearest one. Actual distances (both
along-route and perpendicular) are measured with haversine on the real
coordinates regardless of which segment was picked, so output distances are
never wildly wrong even when segment selection is imperfect. This matches
the PRD's documented limitation: inaccurate for very long regional/suburban
routes, sufficient at city scale. No pyproj/full geodesy.

Known limitation observed on real data (not a bug, out of scope for FA-2):
project_point_to_polyline has no awareness of trajectory continuity or route
topology. When a route passes close to itself (a loop, a nearby parallel
carriageway, a layover), GPS noise of only a few metres can flip the
"nearest" match between two points that are geometrically close but far
apart along the route, producing a small backward jump in
distance_along_shape_m between consecutive observations even though
perpendicular_dist_m stays small throughout. Observed on ~9% of trips in a
manual test against a real Warszawa archive. Fixing this would require
trajectory-aware matching (e.g. HMM/Viterbi with a penalty for improbable
jumps), which is out of scope for this MVP milestone. FA-3's interpolation
step must not assume this series is strictly monotonic.

Complexity: O(len(polyline)) per observation in project_point_to_polyline.
For large archives (thousands of observations x hundreds of shape points)
this could be vectorized with numpy if it proves slow in practice — not
required for this MVP.

No QGIS / GDAL imports. Run tests: pytest tests/test_matcher.py -v
"""

from __future__ import annotations

import csv
import io
import logging
import math
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

_R_M = 6_371_000.0

_MATCHED_COLUMNS = ["trip_id", "timestamp", "distance_along_shape_m", "perpendicular_dist_m"]


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two WGS-84 points."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * _R_M * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


# ---------------------------------------------------------------------------
# Static GTFS loaders
# ---------------------------------------------------------------------------


def load_shapes(gtfs_zip_path: str) -> dict[str, list[tuple[float, float]]]:
    """Load shapes.txt into shape_id -> ordered [(lat, lon), ...] by shape_pt_sequence.

    Returns an empty dict if shapes.txt is absent from the zip (does not
    raise) — callers use this to decide whether to invoke the
    stops-fallback via load_fallback_shapes_from_stops.
    """
    with zipfile.ZipFile(gtfs_zip_path) as zf:
        if "shapes.txt" not in zf.namelist():
            return {}
        raw: dict[str, list[tuple[int, float, float]]] = defaultdict(list)
        with zf.open("shapes.txt") as fh:
            reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig"))
            for row in reader:
                shape_id = row["shape_id"]
                seq = int(row["shape_pt_sequence"])
                lat = float(row["shape_pt_lat"])
                lon = float(row["shape_pt_lon"])
                raw[shape_id].append((seq, lat, lon))

    return {
        shape_id: [(lat, lon) for _, lat, lon in sorted(points, key=lambda p: p[0])]
        for shape_id, points in raw.items()
    }


def load_trip_shape_index(
    gtfs_zip_path: str, exclude_route_ids: frozenset[str] = frozenset()
) -> dict[str, str]:
    """Load trips.txt into trip_id -> shape_id.

    Trips with an empty/missing shape_id column are excluded — such a trip
    can never resolve to a real shape, so match_snapshots's plain dict
    lookup naturally buckets it under "unknown_shape" without special-casing.

    *exclude_route_ids* (route_id column, not trip_id) is excluded the same
    way — a trip whose route isn't in the returned index is simply
    "unknown_shape" to match_snapshots, no different from one lacking a
    shape_id. Added for feeds where a whole agency/mode's real-time trip_id
    isn't a reliable one-trip-per-day identifier (observed on Bucharest's
    Metrorex metro, 2026-07-16: the same trip_id recurs for unrelated real
    departures hours apart, and the simple trajectory-unaware matcher stitches
    them into one fictitious multi-hour "trip" — see this module's own
    docstring on trajectory continuity being out of scope for a proper fix).
    """
    trip_shapes: dict[str, str] = {}
    with zipfile.ZipFile(gtfs_zip_path) as zf:
        with zf.open("trips.txt") as fh:
            reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig"))
            for row in reader:
                if row.get("route_id", "") in exclude_route_ids:
                    continue
                shape_id = row.get("shape_id", "")
                if shape_id:
                    trip_shapes[row["trip_id"]] = shape_id
    return trip_shapes


def load_fallback_shapes_from_stops(
    gtfs_zip_path: str,
) -> dict[str, list[tuple[float, float]]]:
    """Build a synthetic straight-line "shape" per trip from stops.txt/stop_times.txt.

    Used when the static GTFS has no shapes.txt at all. Builds one polyline
    per trip found in stop_times.txt — deliberately NOT restricted to trips
    that already have a shape_id in trips.txt (per load_trip_shape_index),
    because a feed lacking shapes.txt typically leaves shape_id empty for
    every trip too; filtering by that index here would make the fallback a
    silent no-op for exactly the feeds it exists to support.

    Unlike load_shapes, the returned dict is keyed by trip_id, not shape_id —
    this fallback is inherently per-trip (built from that trip's own stop
    sequence), so reusing load_shapes's shape_id key space would be
    misleading. Callers (the CLI) are responsible for remapping this into a
    shape_id-keyed convention if they need a uniform lookup with
    load_shapes's output.

    Logs a single warning (not per-trip) noting reduced accuracy — this is a
    documented degradation, not an error, per the PRD.
    """
    logger.warning(
        "shapes.txt not found in static GTFS %s; falling back to straight-line "
        "stop-to-stop shapes (reduced map-matching accuracy).",
        gtfs_zip_path,
    )

    stop_latlon: dict[str, tuple[float, float]] = {}
    trip_stops_raw: dict[str, list[tuple[int, str]]] = defaultdict(list)

    with zipfile.ZipFile(gtfs_zip_path) as zf:
        with zf.open("stops.txt") as fh:
            reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig"))
            for row in reader:
                stop_latlon[row["stop_id"]] = (float(row["stop_lat"]), float(row["stop_lon"]))

        with zf.open("stop_times.txt") as fh:
            reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig"))
            for row in reader:
                trip_id = row["trip_id"]
                seq = int(row["stop_sequence"])
                trip_stops_raw[trip_id].append((seq, row["stop_id"]))

    fallback_shapes: dict[str, list[tuple[float, float]]] = {}
    for trip_id, stops in trip_stops_raw.items():
        ordered_stop_ids = [stop_id for _, stop_id in sorted(stops, key=lambda p: p[0])]
        polyline = [stop_latlon[sid] for sid in ordered_stop_ids if sid in stop_latlon]
        if polyline:
            fallback_shapes[trip_id] = polyline

    return fallback_shapes


def load_stop_locations(gtfs_zip_path: str) -> dict[str, tuple[float, float]]:
    """Load stops.txt into stop_id -> (stop_lat, stop_lon).

    A distinct, simpler loader from load_fallback_shapes_from_stops's inline
    stops.txt parsing (that one exists to build per-trip polylines; this one
    just exposes raw stop coordinates) - deliberately not shared code, to
    keep each loader's contract simple and independently readable.
    """
    stop_latlon: dict[str, tuple[float, float]] = {}
    with zipfile.ZipFile(gtfs_zip_path) as zf:
        with zf.open("stops.txt") as fh:
            reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig"))
            for row in reader:
                stop_latlon[row["stop_id"]] = (float(row["stop_lat"]), float(row["stop_lon"]))
    return stop_latlon


def resolve_trip_shapes(
    gtfs_zip_path: str, exclude_route_ids: frozenset[str] = frozenset()
) -> tuple[dict[str, str], dict[str, list[tuple[float, float]]], bool]:
    """Resolve (trip_shapes, shapes, fallback_used) for a static GTFS zip.

    Tries load_shapes + load_trip_shape_index first; if shapes.txt is absent,
    falls back to load_fallback_shapes_from_stops and remaps its trip_id-keyed
    polylines onto a pseudo-shape_id keyspace (the trip_id itself) so callers
    can do a uniform shapes.get(trip_shapes.get(trip_id)) lookup regardless of
    which loader produced the data.

    *exclude_route_ids* — see load_trip_shape_index. The fallback path has no
    route_id of its own (load_fallback_shapes_from_stops only reads
    stops.txt/stop_times.txt), so excluded trip_ids are looked up here via a
    second, minimal trips.txt pass and dropped before being merged in -
    default empty, callers that never pass it (build's own resolve_trip_shapes
    call) see no behavior change.

    Factored out of what was cli.py's _cmd_match inline logic so that FA-3's
    build command resolves shapes identically to FA-2's match command for the
    same --static input - duplicating this logic would risk the two drifting
    apart, which would silently misalign build's stop distances against the
    distance_along_shape_m values match already wrote out.
    """
    trip_shapes = load_trip_shape_index(gtfs_zip_path, exclude_route_ids=exclude_route_ids)
    shapes = load_shapes(gtfs_zip_path)

    fallback_used = False
    if not shapes:
        fallback_used = True
        fallback_shapes = load_fallback_shapes_from_stops(gtfs_zip_path)
        if exclude_route_ids:
            trip_routes: dict[str, str] = {}
            with zipfile.ZipFile(gtfs_zip_path) as zf:
                with zf.open("trips.txt") as fh:
                    reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig"))
                    for row in reader:
                        trip_routes[row["trip_id"]] = row.get("route_id", "")
            fallback_shapes = {
                trip_id: polyline
                for trip_id, polyline in fallback_shapes.items()
                if trip_routes.get(trip_id, "") not in exclude_route_ids
            }
        for trip_id, polyline in fallback_shapes.items():
            shapes[trip_id] = polyline
            trip_shapes[trip_id] = trip_id

    return trip_shapes, shapes, fallback_used


# ---------------------------------------------------------------------------
# Point-to-polyline projection
# ---------------------------------------------------------------------------


def project_point_to_polyline(
    lat: float, lon: float, polyline: list[tuple[float, float]]
) -> tuple[float, float]:
    """Project (lat, lon) onto the closest point of polyline.

    Returns (distance_along_polyline_m, perpendicular_distance_m):
    - distance_along_polyline_m: cumulative haversine distance from the
      first vertex to the projected point.
    - perpendicular_distance_m: haversine distance from (lat, lon) to its
      projected point.

    Nearest-segment search is done in raw (lon, lat) degree space treated as
    planar (x, y) — sufficient for choosing the closest segment at city
    scale; the returned distances are then measured with haversine on the
    real coordinates, so they are metre-accurate regardless. See module
    docstring for the full rationale and known limitation.

    A polyline with a single point has no segment: the projection is that
    point itself, at distance_along_m = 0.0. Ties between equally-near
    segments are resolved in favor of the earliest (lowest-index) one.
    """
    if len(polyline) == 0:
        raise ValueError("polyline must contain at least one point")
    if len(polyline) == 1:
        return 0.0, _haversine_m(lat, lon, *polyline[0])

    cumulative = [0.0] * len(polyline)
    for i in range(1, len(polyline)):
        cumulative[i] = cumulative[i - 1] + _haversine_m(*polyline[i - 1], *polyline[i])

    best_perp_m = math.inf
    best_dist_along_m = 0.0

    for i in range(len(polyline) - 1):
        lat1, lon1 = polyline[i]
        lat2, lon2 = polyline[i + 1]

        ax, ay = lon1, lat1
        bx, by = lon2, lat2
        px, py = lon, lat
        abx, aby = bx - ax, by - ay
        seg_len_sq = abx * abx + aby * aby

        if seg_len_sq == 0.0:
            # Degenerate zero-length segment (duplicate consecutive shape
            # points) — the segment start is the only candidate.
            t = 0.0
        else:
            t = ((px - ax) * abx + (py - ay) * aby) / seg_len_sq
            t = max(0.0, min(1.0, t))  # clamp: projection outside the segment -> nearest endpoint

        proj_lat = ay + t * aby
        proj_lon = ax + t * abx

        perp_m = _haversine_m(lat, lon, proj_lat, proj_lon)
        # Partial distance along THIS segment, measured with haversine on the
        # actual projected coordinate (not t * full segment length) to stay
        # consistent with how `cumulative` was built.
        partial_m = _haversine_m(lat1, lon1, proj_lat, proj_lon)
        dist_along_m = cumulative[i] + partial_m

        if perp_m < best_perp_m:
            best_perp_m = perp_m
            best_dist_along_m = dist_along_m

    return best_dist_along_m, best_perp_m


# ---------------------------------------------------------------------------
# Snapshot decoder + matcher
# ---------------------------------------------------------------------------


def _decode_snapshot(data: bytes):
    """Decode raw .pb bytes into a FeedMessage. Lazy import of gtfs_realtime_pb2."""
    from google.transit import gtfs_realtime_pb2  # noqa: PLC0415

    fm = gtfs_realtime_pb2.FeedMessage()
    fm.ParseFromString(data)
    return fm


def snapshot_feed_timestamp(path: Path) -> datetime | None:
    """Decode a snapshot and return its FeedHeader.timestamp as a UTC datetime.

    This is the feed-generation time set by the transit agency's own server -
    unlike the snapshot filename (the recording machine's naive local clock,
    see recorder.parse_snapshot_filename), this value is absolute and
    timezone-independent, so it is safe to convert through agency_tz
    regardless of where `record` was run (FA-6 fix: recording_date must not
    assume the recording machine and the agency share a timezone).

    Returns None if the snapshot fails to decode, or if header.timestamp is
    0/unset - GTFS-RT's spec marks it "strongly recommended", not required,
    and some real feeds omit it (proto3 leaves the field at its 0 default).
    """
    try:
        feed = _decode_snapshot(path.read_bytes())
    except Exception:  # noqa: BLE001 - corrupt/unreadable snapshot
        return None
    if not feed.header.timestamp:
        return None
    return datetime.fromtimestamp(feed.header.timestamp, tz=timezone.utc)


def match_snapshots(
    snapshot_paths: list[Path],
    trip_shapes: dict[str, str],
    shapes: dict[str, list[tuple[float, float]]],
    max_perpendicular_dist_m: float = 100.0,
) -> pd.DataFrame:
    """Map-match VehiclePosition entities across all snapshot_paths onto shapes.

    Takes trip_shapes + shapes as two separate tables rather than a single
    bundled "static_index" object (the PRD's prose describes the latter) —
    a deliberate choice, not an oversight: load_shapes and
    load_trip_shape_index each have a simple, independently testable
    contract, and match_snapshots's lookup (shapes.get(trip_shapes.get(x)))
    is no more complex for it.

    Columns: trip_id, timestamp (UTC datetime64), distance_along_shape_m,
    perpendicular_dist_m. One row per accepted observation, sorted by
    (trip_id, timestamp).

    Decodes each .pb as a gtfs_realtime_pb2.FeedMessage and reads
    entity.vehicle (VehiclePosition), not entity.trip_update — entities with
    no "vehicle" field (e.g. TripUpdate-only feeds) are silently skipped, not
    counted as a rejection.

    A position is rejected (dropped, not kept with a flag) when:
    - its trip has no trip_id ("no_trip_id" — vehicle.trip.trip_id empty),
    - its trip_id has no resolvable shape ("unknown_shape"),
    - its perpendicular distance from the matched shape exceeds
      max_perpendicular_dist_m ("too_far_from_route"),
    - its snapshot file fails to decode ("corrupt_snapshot" — one bad file
      does not abort the whole run).

    Rejection counts and the number of snapshots processed are attached to
    the returned DataFrame via `df.attrs["reject_counts"]` and
    `df.attrs["snapshots_processed"]` — pandas' documented mechanism for
    frame-level metadata — since the return type is fixed to a plain
    DataFrame by this function's public contract. Callers (the CLI) read
    these attrs to print a summary.

    Snapshots are re-sorted by filename internally (chronological, given
    FA-1's snapshot_YYYYmmdd-HHMMSS.pb naming), independent of the order
    snapshot_paths is passed in.
    """
    ordered_paths = sorted(snapshot_paths, key=lambda p: p.name)

    rejects = {
        "no_trip_id": 0,
        "unknown_shape": 0,
        "corrupt_snapshot": 0,
        "too_far_from_route": 0,
    }
    rows: list[tuple[str, datetime, float, float]] = []

    for path in ordered_paths:
        try:
            feed = _decode_snapshot(path.read_bytes())
        except Exception:  # noqa: BLE001 — corrupt / unreadable snapshot
            rejects["corrupt_snapshot"] += 1
            continue

        for entity in feed.entity:
            if not entity.HasField("vehicle"):
                continue
            vp = entity.vehicle

            trip_id = vp.trip.trip_id
            if not trip_id:
                rejects["no_trip_id"] += 1
                continue

            shape_id = trip_shapes.get(trip_id)
            polyline = shapes.get(shape_id) if shape_id is not None else None
            if polyline is None:
                rejects["unknown_shape"] += 1
                continue

            lat = vp.position.latitude
            lon = vp.position.longitude
            dist_along_m, perp_m = project_point_to_polyline(lat, lon, polyline)

            if perp_m > max_perpendicular_dist_m:
                rejects["too_far_from_route"] += 1
                continue

            ts = datetime.fromtimestamp(vp.timestamp, tz=timezone.utc)
            rows.append((trip_id, ts, dist_along_m, perp_m))

    df = pd.DataFrame(rows, columns=_MATCHED_COLUMNS)
    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values(["trip_id", "timestamp"], kind="stable").reset_index(drop=True)

    df.attrs["reject_counts"] = rejects
    df.attrs["snapshots_processed"] = len(ordered_paths)
    return df
