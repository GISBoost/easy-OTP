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

import bisect
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

# PRD FA-12, "otwarte kwestie" #4: fraction of a feed/day's VehiclePosition entities (with a
# non-empty trip_id) that must carry a usable current_stop_sequence (or, failing that, stop_id)
# before that whole day is treated as having "capability" for windowed live-position matching.
# Prague's real measured coverage is exactly the borderline case the PRD flags as needing a real
# decision rather than an arbitrary cutoff (68% current_stop_sequence, 0.5% stop_id) - CONFIRMED
# WITH MICHAŁ (2026-07-23): 0.60, so Prague qualifies via current_stop_sequence. See
# tests/test_matcher.py's threshold-comparison test (0.60 vs 0.90 on a Prague-like ~68%-coverage
# synthetic day) and this milestone's real-data verification on archived Prague 07-17/07-18 data
# for the empirical difference this choice makes.
DEFAULT_POSITION_SIGNAL_COVERAGE_THRESHOLD = 0.60


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two WGS-84 points.

    Public (not module-private) since FA-10's shape_dist.py also needs it, for the
    same haversine-based polyline-length computation this module already does
    internally - no separate reimplementation.
    """
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


def load_shape_dist_traveled(gtfs_zip_path: str) -> dict[str, list[float | None]]:
    """Load shapes.txt's own shape_dist_traveled column (FA-10).

    shape_id -> ordered [value, ...] by shape_pt_sequence, same ordering
    convention as load_shapes. A blank/missing value is None, never coerced to
    0.0 - shape_dist.py's unit-consistency check needs to tell "genuinely
    zero" apart from "absent" to correctly reject the Łódź/Vilnius trap
    (column present in the header, every row's value blank).

    A deliberately separate single pass over shapes.txt from load_shapes,
    not a shared return value - same "simple, independently testable
    contract" preference this module already documents for
    load_shapes/load_trip_shape_index (see resolve_trip_shapes's docstring).

    Returns an empty dict if shapes.txt is absent, or present but without a
    shape_dist_traveled column at all - mirrors load_shapes's "does not
    raise" contract, and lets callers treat "column never existed" and
    "column found nothing" identically as "no trust data available".
    """
    with zipfile.ZipFile(gtfs_zip_path) as zf:
        if "shapes.txt" not in zf.namelist():
            return {}
        with zf.open("shapes.txt") as fh:
            reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig"))
            if "shape_dist_traveled" not in (reader.fieldnames or []):
                return {}
            raw: dict[str, list[tuple[int, float | None]]] = defaultdict(list)
            for row in reader:
                shape_id = row["shape_id"]
                seq = int(row["shape_pt_sequence"])
                raw_val = row.get("shape_dist_traveled") or ""
                dist = float(raw_val) if raw_val.strip() else None
                raw[shape_id].append((seq, dist))

    return {
        shape_id: [dist for _, dist in sorted(points, key=lambda p: p[0])]
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
                lat, lon = row["stop_lat"], row["stop_lon"]
                if not lat or not lon:
                    # See load_stop_locations: some feeds leave these blank for
                    # location_type entries that never appear in stop_times.
                    continue
                stop_latlon[row["stop_id"]] = (float(lat), float(lon))

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
    skipped = 0
    with zipfile.ZipFile(gtfs_zip_path) as zf:
        with zf.open("stops.txt") as fh:
            reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig"))
            for row in reader:
                lat, lon = row["stop_lat"], row["stop_lon"]
                if not lat or not lon:
                    # Real-world feeds (e.g. MBTA) leave stop_lat/stop_lon blank for
                    # some location_type entries (stations, boarding areas, generic
                    # nodes) that are never a stop_times reference point. Downstream
                    # segment_stats.py already treats a missing stop_id as "skip this
                    # segment", so dropping these here is safe, not a data loss.
                    skipped += 1
                    continue
                stop_latlon[row["stop_id"]] = (float(lat), float(lon))
    if skipped:
        logger.warning(
            "load_stop_locations: skipped %d stop(s) in %s with missing stop_lat/stop_lon.",
            skipped, gtfs_zip_path,
        )
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


def cumulative_distances(polyline: list[tuple[float, float]]) -> list[float]:
    """Per-vertex cumulative haversine distance from the first vertex, in metres.

    Extracted from project_point_to_polyline (FA-11) so FA-11's sequential
    per-pattern resolver can build this once per shape and reuse it, same as
    project_point_to_polyline already did internally when cumulative was
    omitted. Same length as polyline; polyline[0] always maps to 0.0.
    """
    cumulative = [0.0] * len(polyline)
    for i in range(1, len(polyline)):
        cumulative[i] = cumulative[i - 1] + haversine_m(*polyline[i - 1], *polyline[i])
    return cumulative


def _project_onto_segment(
    lat: float,
    lon: float,
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
    seg_start_cum: float,
) -> tuple[float, float]:
    """Project (lat, lon) onto segment (lat1,lon1)->(lat2,lon2). Returns (dist_along_m, perp_m).

    dist_along_m is measured from the polyline's first vertex, i.e.
    seg_start_cum (the segment start's own cumulative distance) plus the
    haversine distance from the segment start to the projected point.

    Extracted from project_point_to_polyline's per-segment loop body (FA-11)
    so the same projection math can be reused by a restricted (index-range-
    limited) search without duplicating it. Behaviour is unchanged - see
    project_point_to_polyline's docstring for the planar-projection/haversine
    rationale and the degenerate zero-length-segment handling below.
    """
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

    perp_m = haversine_m(lat, lon, proj_lat, proj_lon)
    # Partial distance along THIS segment, measured with haversine on the
    # actual projected coordinate (not t * full segment length) to stay
    # consistent with how `cumulative` was built.
    partial_m = haversine_m(lat1, lon1, proj_lat, proj_lon)
    dist_along_m = seg_start_cum + partial_m

    return dist_along_m, perp_m


def project_point_to_polyline(
    lat: float,
    lon: float,
    polyline: list[tuple[float, float]],
    cumulative: list[float] | None = None,
) -> tuple[float, float]:
    """Project (lat, lon) onto the closest point of polyline.

    Returns (distance_along_polyline_m, perpendicular_distance_m):
    - distance_along_polyline_m: cumulative distance from the first vertex to
      the projected point.
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

    *cumulative* (FA-10): an optional precomputed per-vertex cumulative
    distance array, same length as polyline. When given, it REPLACES the
    haversine-based cumulative build below - this lets a trustworthy static
    feed's own shape_dist_traveled values drive this projection's distance
    axis, so a live vehicle observation and a shape_dist_traveled-anchored
    stop stay comparable (see shape_dist.py). When omitted (every pre-FA-10
    call site), behaviour is unchanged: cumulative distance is derived via
    haversine summation, exactly as before.
    """
    if len(polyline) == 0:
        raise ValueError("polyline must contain at least one point")
    if len(polyline) == 1:
        return 0.0, haversine_m(lat, lon, *polyline[0])

    if cumulative is None:
        cumulative = cumulative_distances(polyline)

    best_perp_m = math.inf
    best_dist_along_m = 0.0

    for i in range(len(polyline) - 1):
        lat1, lon1 = polyline[i]
        lat2, lon2 = polyline[i + 1]

        dist_along_m, perp_m = _project_onto_segment(lat, lon, lat1, lon1, lat2, lon2, cumulative[i])

        if perp_m < best_perp_m:
            best_perp_m = perp_m
            best_dist_along_m = dist_along_m

    return best_dist_along_m, best_perp_m


def project_point_to_polyline_windowed(
    lat: float,
    lon: float,
    polyline: list[tuple[float, float]],
    cumulative: list[float],
    window_lo_m: float,
    window_hi_m: float,
) -> tuple[float, float] | None:
    """Like project_point_to_polyline, but restricted to [window_lo_m, window_hi_m] (FA-12).

    Same per-segment search and tie-break as project_point_to_polyline (lowest-index wins),
    but a segment is only considered when its own projected point's dist_along_m falls inside
    the window - this is what stops a live observation from ever being matched to a distant,
    geometrically-close pass of a loop/out-and-back shape outside its own neighboring scheduled
    stops, even after FA-10/FA-11 corrected the stop anchors themselves.

    Mirrors interpolate.resolve_stop_distances_for_pattern's own segment-selection pattern
    (cheap pre-filter via cumulative distance, then a straddling-segment re-check against the
    actual projected point - a segment whose far endpoint is inside the window can still project
    onto its own outside-the-window portion) generalized from one bound (backward-only) to two.

    Returns None - never raises, never falls back itself - when no segment's projected point
    lands inside the window at all; the caller decides the fallback (today's unrestricted
    project_point_to_polyline), same "never a hard error" convention FA-11 established.
    """
    best_perp_m = math.inf
    best_dist_along_m: float | None = None

    for i in range(len(polyline) - 1):
        if cumulative[i + 1] < window_lo_m or cumulative[i] > window_hi_m:
            continue
        lat1, lon1 = polyline[i]
        lat2, lon2 = polyline[i + 1]
        dist_along_m, perp_m = _project_onto_segment(lat, lon, lat1, lon1, lat2, lon2, cumulative[i])
        if dist_along_m < window_lo_m or dist_along_m > window_hi_m:
            continue
        if perp_m < best_perp_m:
            best_perp_m = perp_m
            best_dist_along_m = dist_along_m

    if best_dist_along_m is None:
        return None
    return best_dist_along_m, best_perp_m


def _bracket_window_for_sequence(
    anchors: list[tuple[int, str, float]], current_seq: int
) -> tuple[float, float]:
    """Window [dist(stop[idx-1]), dist(stop[idx+1])] for a current_stop_sequence value (FA-12).

    anchors is one trip's own resolved stop anchor list (see
    interpolate.resolve_all_trip_stop_anchors), sorted ascending by stop_sequence. idx is the
    anchor position whose stop_sequence matches current_seq; when current_seq isn't an exact
    match (a real feed's RT producer numbering doesn't perfectly track its own static
    stop_sequence, or a gap - PRD Ograniczenia: consecutive snapshots can jump e.g. 15->17),
    idx defensively clamps to the nearest bracketing position via bisect rather than raising.

    PRD FA-12 point 4: the stop_sequence numbering base (0- vs 1-indexed) must be derived per
    feed, never hardcoded - confirmed empirically: Poznań is 0-indexed, Prague is 1-indexed.
    This lookup satisfies that without any explicit base arithmetic at all: it always searches
    for the real stop_sequence *value* via bisect, never treats current_seq as a raw list index
    into anchors - so a 0-indexed and a 1-indexed feed both work correctly unchanged (see
    test_match_snapshots_windowing_works_for_0_indexed_and_1_indexed_stop_sequence).
    """
    seqs = [seq for seq, _stop_id, _dist in anchors]
    idx = bisect.bisect_left(seqs, current_seq)
    if idx >= len(seqs):
        idx = len(seqs) - 1
    lo_idx = max(idx - 1, 0)
    hi_idx = min(idx + 1, len(anchors) - 1)
    return anchors[lo_idx][2], anchors[hi_idx][2]


def _bracket_window_for_stop_id(
    anchors: list[tuple[int, str, float]],
    stop_id: str,
    last_confirmed_seq: int | None,
) -> tuple[tuple[float, float], int] | None:
    """Window + chosen stop_sequence for a stop_id signal observation (FA-12).

    A stop_id can legitimately occur more than once in one trip's stop_sequence on a loop/
    out-and-back shape (PRD Ograniczenia point 5) - a naive stop_id -> index lookup would be
    ambiguous, exactly the class of bug this whole FA-10..FA-12 series exists to fix. Among all
    occurrences, picks the smallest stop_sequence that is >= last_confirmed_seq (monotonic
    continuation of this same trip's own prior confirmed position in time order), or the first
    occurrence if this trip has no prior confirmed state yet. Falls back to the last occurrence
    only in the degenerate case where every occurrence is already behind last_confirmed_seq
    (implausible in practice; never an arbitrary first/last pick in the normal case).

    Returns None if stop_id is not part of this trip's pattern at all - signals the caller to
    fall back to unrestricted matching for this observation.
    """
    occurrences = [(seq, dist) for seq, sid, dist in anchors if sid == stop_id]
    if not occurrences:
        return None

    if last_confirmed_seq is None:
        chosen_seq, _chosen_dist = occurrences[0]
    else:
        candidates = [(seq, dist) for seq, dist in occurrences if seq >= last_confirmed_seq]
        chosen_seq, _chosen_dist = candidates[0] if candidates else occurrences[-1]

    return _bracket_window_for_sequence(anchors, chosen_seq), chosen_seq


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


def observed_trip_ids(snapshot_paths: list[Path]) -> set[str]:
    """Every distinct non-empty vehicle.trip.trip_id seen across snapshot_paths (FA-12).

    A cheap, geometry-free decode pass (a real static feed's own trip roster can be tens of
    thousands of trips, e.g. Gdańsk: ~93,600 - but any single day's RT feed only ever reports
    the small subset of trips actually running, typically a few hundred to a few thousand
    distinct trip_ids). Used by the CLI to restrict interpolate.resolve_all_trip_stop_anchors to
    only the trips this match run could possibly need a window for, instead of eagerly resolving
    every trip in the entire static feed regardless of whether it was ever observed - the latter
    made 'match' (which never touched stop_times.txt at all before FA-12) pay a whole-feed cost
    proportional to static feed size rather than RT data volume.

    Corrupt/unreadable snapshots are silently skipped here (this is a sampling pass, not the
    real matching pass - match_snapshots's own decode pass is what counts a "corrupt_snapshot"
    rejection).
    """
    trip_ids: set[str] = set()
    for path in snapshot_paths:
        try:
            feed = _decode_snapshot(path.read_bytes())
        except Exception:  # noqa: BLE001 - corrupt/unreadable snapshot, skip
            continue
        for entity in feed.entity:
            if not entity.HasField("vehicle"):
                continue
            trip_id = entity.vehicle.trip.trip_id
            if trip_id:
                trip_ids.add(trip_id)
    return trip_ids


def match_snapshots(
    snapshot_paths: list[Path],
    trip_shapes: dict[str, str],
    shapes: dict[str, list[tuple[float, float]]],
    max_perpendicular_dist_m: float = 100.0,
    shape_cumulative_dist: dict[str, list[float]] | None = None,
    trip_stop_anchors: dict[str, list[tuple[int, str, float]]] | None = None,
    position_signal_coverage_threshold: float = DEFAULT_POSITION_SIGNAL_COVERAGE_THRESHOLD,
) -> pd.DataFrame:
    """Map-match VehiclePosition entities across all snapshot_paths onto shapes.

    Takes trip_shapes + shapes as two separate tables rather than a single
    bundled "static_index" object (the PRD's prose describes the latter) —
    a deliberate choice, not an oversight: load_shapes and
    load_trip_shape_index each have a simple, independently testable
    contract, and match_snapshots's lookup (shapes.get(trip_shapes.get(x)))
    is no more complex for it.

    *shape_cumulative_dist* (FA-10): optional shape_id -> trustworthy
    cumulative shape_dist_traveled array (see shape_dist.evaluate_shape_trust),
    passed straight through to project_point_to_polyline's own *cumulative*
    parameter for the matched shape - keeps a live vehicle observation's
    distance_along_shape_m on the same axis as a shape_dist_traveled-anchored
    stop. Omitted (the default) or missing an entry for a given shape_id
    falls back to geometric (haversine) projection exactly as before.

    *trip_stop_anchors* (FA-12): optional trip_id -> this trip's own resolved
    stop anchor list (see interpolate.resolve_all_trip_stop_anchors), each a
    [(stop_sequence, stop_id, distance_along_shape_m), ...] sorted by
    stop_sequence - the already-corrected FA-10/FA-11 anchors. When given
    (non-empty), a per-day capability is detected once from a single decode
    pass over every VehiclePosition with a non-empty trip_id: the fraction
    with a usable current_stop_sequence (proto HasField-checkable) and,
    separately, with a non-empty stop_id, each compared against
    *position_signal_coverage_threshold*. Priority: current_stop_sequence,
    then stop_id, else "none" (today's fully unrestricted behaviour) - see
    _bracket_window_for_sequence/_bracket_window_for_stop_id. This decision is
    per-day (one match_snapshots call = one recording session, per cli.py's
    own per-directory loop), never per-observation: a single observation
    missing the day's chosen signal field simply falls back to unrestricted
    matching for that one observation, without changing the day's decision.
    Omitting *trip_stop_anchors* (the default) or passing {} reproduces
    today's behaviour exactly, zero windowing, same "empty ⇒ no trust data"
    convention shape_dist.evaluate_shape_trust already established.
    current_status is never read anywhere in this module - GTFS-RT marks it
    optional and real feeds were found to leave it unset/default almost
    always, so no window logic depends on it.

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
    these attrs to print a summary. FA-12 adds `df.attrs["position_signal"]`
    ("sequence" | "stop_id" | "none") and `df.attrs["position_signal_coverage"]`
    ({"sequence": float, "stop_id": float}, both 0.0 when trip_stop_anchors was
    omitted) for the same reason.

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

    # Pass 1: decode every snapshot exactly once into an in-memory record list. Replaces (not
    # duplicates) what used to be the whole loop body - the two extra fields read here
    # (current_stop_sequence/stop_id) cost nothing extra file-I/O-wise, and this same in-memory
    # list is reused below both for FA-12's capability sampling and for the actual matching pass,
    # so a whole day's snapshots are still only ever decoded once.
    #
    # FA-12's per-trip last_confirmed_seq (below) relies on records for the same trip_id being
    # encountered here in time order - true given ordered_paths' filename sort (FA-1's
    # snapshot_YYYYmmdd-HHMMSS.pb naming) and one entity per vehicle per snapshot instant, same
    # assumption the rest of this function already makes (see this docstring's "Snapshots are
    # re-sorted..." paragraph and interpolate_stop_time's own chronological-scan design).
    records: list[tuple[str, datetime, float, float, bool, int, str]] = []
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

            lat = vp.position.latitude
            lon = vp.position.longitude
            ts = datetime.fromtimestamp(vp.timestamp, tz=timezone.utc)
            has_seq = vp.HasField("current_stop_sequence")
            seq_val = vp.current_stop_sequence if has_seq else 0
            stop_id_val = vp.stop_id
            records.append((trip_id, ts, lat, lon, has_seq, seq_val, stop_id_val))

    # FA-12: per-day capability detection. trip_stop_anchors omitted/empty short-circuits to
    # "none" immediately - zero behaviour change, same convention as
    # shape_dist.evaluate_shape_trust's empty-dict case.
    signal = "none"
    coverage_seq = 0.0
    coverage_stop_id = 0.0
    if trip_stop_anchors:
        total = len(records)
        if total:
            coverage_seq = sum(1 for r in records if r[4]) / total
            coverage_stop_id = sum(1 for r in records if r[6]) / total
        if coverage_seq >= position_signal_coverage_threshold:
            signal = "sequence"
        elif coverage_stop_id >= position_signal_coverage_threshold:
            signal = "stop_id"
        logger.info(
            "matcher.py: FA-12 position signal=%s (sequence coverage=%.1f%%, stop_id "
            "coverage=%.1f%%, threshold=%.1f%%, %d entities sampled).",
            signal, coverage_seq * 100, coverage_stop_id * 100,
            position_signal_coverage_threshold * 100, total,
        )

    rows: list[tuple[str, datetime, float, float]] = []
    last_confirmed_seq: dict[str, int] = {}

    for trip_id, ts, lat, lon, has_seq, seq_val, stop_id_val in records:
        shape_id = trip_shapes.get(trip_id)
        polyline = shapes.get(shape_id) if shape_id is not None else None
        if polyline is None:
            rejects["unknown_shape"] += 1
            continue

        cumulative = shape_cumulative_dist.get(shape_id) if shape_cumulative_dist else None
        if cumulative is None:
            cumulative = cumulative_distances(polyline)

        anchors = trip_stop_anchors.get(trip_id) if (signal != "none" and trip_stop_anchors) else None
        window: tuple[float, float] | None = None
        confirmed_seq: int | None = None

        if anchors:
            if signal == "sequence" and has_seq:
                window = _bracket_window_for_sequence(anchors, seq_val)
                confirmed_seq = seq_val
            elif signal == "stop_id" and stop_id_val:
                bracket = _bracket_window_for_stop_id(anchors, stop_id_val, last_confirmed_seq.get(trip_id))
                if bracket is not None:
                    window, confirmed_seq = bracket

        dist_along_m: float | None = None
        perp_m: float | None = None
        if window is not None:
            window_lo_m, window_hi_m = window
            windowed = project_point_to_polyline_windowed(
                lat, lon, polyline, cumulative, window_lo_m, window_hi_m
            )
            if windowed is None:
                logger.warning(
                    "matcher.py: FA-12 windowed search found nothing for trip_id=%s within "
                    "[%.1fm, %.1fm] - falling back to unrestricted search.",
                    trip_id, window_lo_m, window_hi_m,
                )
            else:
                dist_along_m, perp_m = windowed

        if dist_along_m is None:
            dist_along_m, perp_m = project_point_to_polyline(lat, lon, polyline, cumulative=cumulative)

        # Deliberately updated before the too-far-from-route check below: the vehicle's own
        # current_stop_sequence/stop_id is a claim about its position in the trip, independent
        # of this observation's geometric match quality - an observation that gets rejected as
        # too far from the route still genuinely represents the trip having reached (at least)
        # this stop_sequence, and future stop_id disambiguation on this trip should account for it.
        if confirmed_seq is not None:
            last_confirmed_seq[trip_id] = confirmed_seq

        if perp_m > max_perpendicular_dist_m:
            rejects["too_far_from_route"] += 1
            continue

        rows.append((trip_id, ts, dist_along_m, perp_m))

    df = pd.DataFrame(rows, columns=_MATCHED_COLUMNS)
    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values(["trip_id", "timestamp"], kind="stable").reset_index(drop=True)

    df.attrs["reject_counts"] = rejects
    df.attrs["snapshots_processed"] = len(ordered_paths)
    df.attrs["position_signal"] = signal
    df.attrs["position_signal_coverage"] = {"sequence": coverage_seq, "stop_id": coverage_stop_id}
    return df
