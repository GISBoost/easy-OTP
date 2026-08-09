"""Unit tests for family_a.matcher (FA-2).

No QGIS, no network — pure stdlib + pandas + pytest.
Run: pytest tests/test_matcher.py -v
"""

import math
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from google.transit import gtfs_realtime_pb2

from family_a.matcher import (
    cumulative_distances,
    load_fallback_shapes_from_stops,
    load_shape_dist_traveled,
    load_shapes,
    load_stop_locations,
    load_trip_route_index,
    load_trip_shape_index,
    match_snapshots,
    observed_trip_ids,
    project_point_to_polyline,
    project_point_to_polyline_windowed,
    resolve_trip_shapes,
    snapshot_feed_timestamp,
)

# A straight north-south line, ~0.01 deg lat apart (~1.1km per segment) at the equator.
_STRAIGHT_LINE = [(0.0, 0.0), (0.01, 0.0), (0.02, 0.0)]

# Out-and-back loop mirroring shape 154679's exact structure (FA-11 handoff) and
# test_interpolate.py's own _LOOP_LINE: the coordinate (0.01, 0.0) occurs at a low index (1)
# and a high index (3), an exact match both times - the same class of ambiguity FA-12 protects
# live-position matching against, now that FA-10/FA-11 already protect stop anchoring.
_LOOP_LINE = [(0.0, 0.0), (0.01, 0.0), (0.02, 0.0), (0.01, 0.0), (0.0, 0.0)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_gtfs_zip(tmp_path: Path, *, with_shapes: bool = True) -> Path:
    """Write a minimal synthetic GTFS zip: 2 trips on 1 shape, 3 stops.

    When with_shapes=False, trips.txt also leaves shape_id empty for every
    trip — this mirrors the realistic feed shape that motivates the
    stops-fallback (a feed missing shapes.txt typically never populates
    shape_id either), not just the absence of the shapes.txt file alone.
    """
    path = tmp_path / "gtfs.zip"
    shape_col = "shape1" if with_shapes else ""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "trips.txt",
            "trip_id,route_id,shape_id\n"
            f"trip1,routeA,{shape_col}\n"
            f"trip2,routeA,{shape_col}\n",
        )
        zf.writestr(
            "stops.txt",
            "stop_id,stop_lat,stop_lon\n"
            "s1,0.0,0.0\n"
            "s2,0.01,0.0\n"
            "s3,0.02,0.0\n",
        )
        zf.writestr(
            "stop_times.txt",
            "trip_id,stop_id,stop_sequence\n"
            "trip1,s1,0\n"
            "trip1,s2,1\n"
            "trip1,s3,2\n"
            "trip2,s1,0\n"
            "trip2,s2,1\n",
        )
        if with_shapes:
            # Deliberately out-of-order shape_pt_sequence to verify re-sort.
            zf.writestr(
                "shapes.txt",
                "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\n"
                "shape1,0.02,0.0,2\n"
                "shape1,0.0,0.0,0\n"
                "shape1,0.01,0.0,1\n",
            )
    return path


def _make_feed_message(entities: list[gtfs_realtime_pb2.FeedEntity]) -> bytes:
    feed = gtfs_realtime_pb2.FeedMessage(
        header=gtfs_realtime_pb2.FeedHeader(gtfs_realtime_version="2.0"),
        entity=entities,
    )
    return feed.SerializeToString()


def _vehicle_entity(
    entity_id: str,
    trip_id: str,
    lat: float,
    lon: float,
    timestamp: int,
    current_stop_sequence: int | None = None,
    stop_id: str = "",
) -> gtfs_realtime_pb2.FeedEntity:
    """*current_stop_sequence*/*stop_id* (FA-12): optional VehiclePosition fields used for
    windowed live-position matching. current_stop_sequence is omitted (proto HasField-checkable,
    absent by default) unless explicitly given; stop_id defaults to the proto's own empty-string
    default when not given - mirroring real feeds that never populate it (see the handoff's
    cross-city coverage matrix).
    """
    kwargs = {}
    if current_stop_sequence is not None:
        kwargs["current_stop_sequence"] = current_stop_sequence
    return gtfs_realtime_pb2.FeedEntity(
        id=entity_id,
        vehicle=gtfs_realtime_pb2.VehiclePosition(
            trip=gtfs_realtime_pb2.TripDescriptor(trip_id=trip_id),
            position=gtfs_realtime_pb2.Position(latitude=lat, longitude=lon),
            timestamp=timestamp,
            stop_id=stop_id,
            **kwargs,
        ),
    )


def _f5_entity(entity_id: str, trip_id: str, lat: float, *, rich: bool):
    """One entity with every F5 field set, or one with none of them (the proto defaults)."""
    position = gtfs_realtime_pb2.Position(latitude=lat, longitude=0.0)
    vp = gtfs_realtime_pb2.VehiclePosition(
        trip=gtfs_realtime_pb2.TripDescriptor(trip_id=trip_id),
        position=position,
        timestamp=1_700_000_000,
    )
    if rich:
        vp.vehicle.id = f"veh_{entity_id}"
        vp.current_status = gtfs_realtime_pb2.VehiclePosition.STOPPED_AT
        vp.position.bearing = 90.0
        vp.position.speed = 5.0
        vp.position.odometer = 1234.0
    return gtfs_realtime_pb2.FeedEntity(id=entity_id, vehicle=vp)


def test_f5_field_coverage_and_vehicle_id_column(tmp_path):
    """F5: measure what the feed publishes, and carry vehicle_id into the matched table.

    Two entities, one fully populated and one bare, so every share must be exactly 0.5 - a
    counter wired to the wrong field or the wrong denominator cannot produce that by accident.
    HasField is what makes the bare one count as absent: GTFS-RT defaults current_status to
    IN_TRANSIT_TO and bearing/speed/odometer to 0.0, so a value test would report 100%.
    """
    trip_shapes, shapes = _shapes_and_trips()
    path = _write_pb(
        tmp_path / "snapshot_20260101-000000.pb",
        _make_feed_message([
            _f5_entity("a", "trip1", 0.000, rich=True),
            _f5_entity("b", "trip1", 0.005, rich=False),
        ]),
    )

    df = match_snapshots([path], trip_shapes, shapes)

    assert df.attrs["field_coverage"] == {
        "vehicle_id": 0.5, "current_status": 0.5, "bearing": 0.5, "speed": 0.5, "odometer": 0.5,
    }
    assert sorted(df["vehicle_id"]) == ["", "veh_a"]


def _write_pb(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


# ---------------------------------------------------------------------------
# project_point_to_polyline
# ---------------------------------------------------------------------------


def test_project_point_on_line_exact():
    dist_along, perp = project_point_to_polyline(0.005, 0.0, _STRAIGHT_LINE)
    assert perp < 1.0  # essentially zero
    # Halfway along the first ~1.11km segment.
    assert 500 < dist_along < 620


def test_project_point_near_vertex():
    dist_along, perp = project_point_to_polyline(0.01, 0.0001, _STRAIGHT_LINE)
    # Close to the middle vertex -> distance-along ~= cumulative to that vertex.
    full_segment_m = project_point_to_polyline(0.01, 0.0, _STRAIGHT_LINE)[0]
    assert abs(dist_along - full_segment_m) < 50
    assert perp < 50


def test_project_point_off_to_the_side():
    # Offset perpendicular to the (north-south) line by 0.01 deg longitude.
    dist_along, perp = project_point_to_polyline(0.005, 0.01, _STRAIGHT_LINE)
    assert perp > 500  # clearly off the line, roughly ~1.1km at this latitude
    assert dist_along > 0


def test_project_point_single_vertex_polyline():
    dist_along, perp = project_point_to_polyline(0.001, 0.001, [(0.0, 0.0)])
    assert dist_along == 0.0
    assert perp > 0


def test_project_point_clamped_before_first_vertex():
    dist_along, perp = project_point_to_polyline(-0.01, 0.0, _STRAIGHT_LINE)
    assert dist_along == 0.0


def test_project_point_clamped_after_last_vertex():
    dist_along, perp = project_point_to_polyline(0.03, 0.0, _STRAIGHT_LINE)
    total_length = project_point_to_polyline(0.02, 0.0, _STRAIGHT_LINE)[0]
    assert math.isclose(dist_along, total_length, rel_tol=1e-6)


def test_project_point_degenerate_zero_length_segment():
    # Duplicate consecutive shape points (a real-world GTFS quirk) must not
    # raise a ZeroDivisionError; the degenerate segment collapses to its
    # (single) point.
    polyline_with_duplicate = [(0.0, 0.0), (0.01, 0.0), (0.01, 0.0), (0.02, 0.0)]
    dist_along, perp = project_point_to_polyline(0.01, 0.0001, polyline_with_duplicate)
    expected_dist_to_dup_vertex = project_point_to_polyline(0.01, 0.0, _STRAIGHT_LINE)[0]
    assert math.isclose(dist_along, expected_dist_to_dup_vertex, rel_tol=1e-6)
    assert perp < 50


def test_project_point_cumulative_none_matches_default_haversine_build():
    # FA-10: omitting *cumulative* must reproduce today's haversine-derived behaviour.
    with_default = project_point_to_polyline(0.005, 0.0, _STRAIGHT_LINE)
    with_explicit_none = project_point_to_polyline(0.005, 0.0, _STRAIGHT_LINE, cumulative=None)
    assert with_default == with_explicit_none


def test_project_point_to_polyline_duplicate_point_on_loop_shape_ties_to_lowest_index():
    # FA-11 hard constraint: project_point_to_polyline's own tie-break must stay
    # unchanged (FA-11 only changes how a whole trip's STOP pattern is resolved, via
    # a new, separate function - this one keeps resolving a single point independently
    # and in isolation, exactly as before). Out-and-back polyline mirroring shape
    # 154679's structure: (0.01, 0.0) occurs at both a low index (1) and a high index
    # (3), an exact coordinate match both times.
    loop_line = [(0.0, 0.0), (0.01, 0.0), (0.02, 0.0), (0.01, 0.0), (0.0, 0.0)]
    dist_along, perp = project_point_to_polyline(0.01, 0.0, loop_line)
    assert perp == 0.0
    # Low-index occurrence (index 1) must win, not the high-index one (index 3).
    low_index_cum = project_point_to_polyline(0.01, 0.0, loop_line[:2])[0]
    assert math.isclose(dist_along, low_index_cum, rel_tol=1e-9)


def test_project_point_cumulative_override_changes_distance_along():
    # A point strictly inside the second segment - no perpendicular-distance tie
    # between segments, so the effect of a custom cumulative array is unambiguous.
    point_lat, point_lon = 0.015, 0.0
    custom_cumulative = [0.0, 500.0, 1000.0]

    dist_along_custom, perp_custom = project_point_to_polyline(
        point_lat, point_lon, _STRAIGHT_LINE, cumulative=custom_cumulative
    )
    dist_along_default, perp_default = project_point_to_polyline(
        point_lat, point_lon, _STRAIGHT_LINE
    )

    assert dist_along_custom != dist_along_default
    # perpendicular distance is always geometric, unaffected by *cumulative*.
    assert math.isclose(perp_custom, perp_default, abs_tol=1e-9)


# ---------------------------------------------------------------------------
# load_shapes / load_trip_shape_index
# ---------------------------------------------------------------------------


def test_load_shapes_happy_path(tmp_path):
    gtfs = _make_gtfs_zip(tmp_path, with_shapes=True)
    shapes = load_shapes(str(gtfs))
    assert list(shapes.keys()) == ["shape1"]
    assert shapes["shape1"] == [(0.0, 0.0), (0.01, 0.0), (0.02, 0.0)]


def test_load_shapes_missing_file_returns_empty_dict(tmp_path):
    gtfs = _make_gtfs_zip(tmp_path, with_shapes=False)
    assert load_shapes(str(gtfs)) == {}


def test_load_trip_shape_index_happy_path(tmp_path):
    gtfs = _make_gtfs_zip(tmp_path, with_shapes=True)
    index = load_trip_shape_index(str(gtfs))
    assert index == {"trip1": "shape1", "trip2": "shape1"}


def test_load_trip_shape_index_skips_trips_without_shape_id(tmp_path):
    path = tmp_path / "gtfs.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "trips.txt",
            "trip_id,route_id,shape_id\ntrip1,routeA,shape1\ntrip2,routeA,\n",
        )
    index = load_trip_shape_index(str(path))
    assert index == {"trip1": "shape1"}


def test_load_trip_shape_index_excludes_given_route_ids(tmp_path):
    path = tmp_path / "gtfs.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "trips.txt",
            "trip_id,route_id,shape_id\n"
            "trip1,routeA,shape1\n"
            "trip2,routeB,shape1\n",
        )
    index = load_trip_shape_index(str(path), exclude_route_ids=frozenset({"routeB"}))
    assert index == {"trip1": "shape1"}


def test_resolve_trip_shapes_excludes_given_route_ids(tmp_path):
    gtfs = _make_gtfs_zip(tmp_path, with_shapes=True)
    trip_shapes, shapes, fallback_used = resolve_trip_shapes(
        str(gtfs), exclude_route_ids=frozenset({"routeA"})
    )
    assert trip_shapes == {}
    assert fallback_used is False


# ---------------------------------------------------------------------------
# load_shape_dist_traveled (FA-10)
# ---------------------------------------------------------------------------


def test_load_shape_dist_traveled_fully_filled(tmp_path):
    path = tmp_path / "gtfs.zip"
    with zipfile.ZipFile(path, "w") as zf:
        # Deliberately out-of-order shape_pt_sequence, same as _make_gtfs_zip, to
        # verify re-sort applies to shape_dist_traveled too.
        zf.writestr(
            "shapes.txt",
            "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence,shape_dist_traveled\n"
            "shape1,0.02,0.0,2,2224.0\n"
            "shape1,0.0,0.0,0,0.0\n"
            "shape1,0.01,0.0,1,1112.0\n",
        )
    result = load_shape_dist_traveled(str(path))
    assert result == {"shape1": [0.0, 1112.0, 2224.0]}


def test_load_shape_dist_traveled_missing_shapes_file_returns_empty_dict(tmp_path):
    path = tmp_path / "gtfs.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("trips.txt", "trip_id,route_id,shape_id\ntrip1,routeA,\n")
    assert load_shape_dist_traveled(str(path)) == {}


def test_load_shape_dist_traveled_column_absent_returns_empty_dict(tmp_path):
    path = tmp_path / "gtfs.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "shapes.txt",
            "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\n"
            "shape1,0.0,0.0,0\nshape1,0.01,0.0,1\n",
        )
    assert load_shape_dist_traveled(str(path)) == {}


def test_load_shape_dist_traveled_entirely_blank_values_are_none_not_zero(tmp_path):
    # The Łódź/Vilnius trap: column present in the header, every row's value blank.
    path = tmp_path / "gtfs.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "shapes.txt",
            "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence,shape_dist_traveled\n"
            "shape1,0.0,0.0,0,\nshape1,0.01,0.0,1,\n",
        )
    result = load_shape_dist_traveled(str(path))
    assert result == {"shape1": [None, None]}


def test_load_shape_dist_traveled_partial_fill(tmp_path):
    path = tmp_path / "gtfs.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "shapes.txt",
            "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence,shape_dist_traveled\n"
            "shape1,0.0,0.0,0,0.0\nshape1,0.01,0.0,1,\n",
        )
    result = load_shape_dist_traveled(str(path))
    assert result == {"shape1": [0.0, None]}


# ---------------------------------------------------------------------------
# load_fallback_shapes_from_stops
# ---------------------------------------------------------------------------


def test_load_fallback_shapes_from_stops(tmp_path):
    gtfs = _make_gtfs_zip(tmp_path, with_shapes=False)
    fallback = load_fallback_shapes_from_stops(str(gtfs))
    assert fallback["trip1"] == [(0.0, 0.0), (0.01, 0.0), (0.02, 0.0)]
    assert fallback["trip2"] == [(0.0, 0.0), (0.01, 0.0)]


def test_load_fallback_shapes_from_stops_when_shape_id_also_empty(tmp_path):
    """Regression test: the realistic case a feed with no shapes.txt also
    leaves shape_id empty in trips.txt, so load_trip_shape_index returns {}.
    The fallback must still build shapes for every trip found in
    stop_times.txt — it must not be gated on trip_shape_index membership.
    """
    gtfs = _make_gtfs_zip(tmp_path, with_shapes=False)
    trip_shapes = load_trip_shape_index(str(gtfs))
    assert trip_shapes == {}  # confirms the realistic precondition

    fallback = load_fallback_shapes_from_stops(str(gtfs))
    assert fallback["trip1"] == [(0.0, 0.0), (0.01, 0.0), (0.02, 0.0)]
    assert fallback["trip2"] == [(0.0, 0.0), (0.01, 0.0)]


def test_load_fallback_shapes_skips_missing_stop(tmp_path):
    path = tmp_path / "gtfs.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("trips.txt", "trip_id,route_id,shape_id\ntrip1,routeA,shape1\n")
        zf.writestr("stops.txt", "stop_id,stop_lat,stop_lon\ns1,0.0,0.0\n")
        zf.writestr(
            "stop_times.txt",
            "trip_id,stop_id,stop_sequence\ntrip1,s1,0\ntrip1,sMISSING,1\n",
        )
    fallback = load_fallback_shapes_from_stops(str(path))
    assert fallback["trip1"] == [(0.0, 0.0)]


def test_load_fallback_logs_warning(tmp_path, caplog):
    gtfs = _make_gtfs_zip(tmp_path, with_shapes=False)
    with caplog.at_level("WARNING"):
        load_fallback_shapes_from_stops(str(gtfs))
    assert any("shapes.txt" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# load_stop_locations
# ---------------------------------------------------------------------------


def test_load_stop_locations_happy_path(tmp_path):
    gtfs = _make_gtfs_zip(tmp_path)
    locations = load_stop_locations(str(gtfs))
    assert locations == {"s1": (0.0, 0.0), "s2": (0.01, 0.0), "s3": (0.02, 0.0)}


def test_load_stop_locations_skips_blank_coordinates(tmp_path, caplog):
    """Regression test: real-world feeds (e.g. MBTA) leave stop_lat/stop_lon
    blank for some location_type entries (stations, boarding areas, generic
    nodes) that never appear as a stop_times reference point. These rows must
    be skipped, not crash the whole build.
    """
    path = tmp_path / "gtfs.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "stops.txt",
            "stop_id,stop_lat,stop_lon\n"
            "s1,0.0,0.0\n"
            "s2,,\n"
            "s3,0.02,0.0\n",
        )
    with caplog.at_level("WARNING"):
        locations = load_stop_locations(str(path))
    assert locations == {"s1": (0.0, 0.0), "s3": (0.02, 0.0)}
    assert "s2" not in locations
    assert any("skipped 1 stop" in record.message for record in caplog.records)


def test_load_fallback_shapes_from_stops_skips_blank_coordinates(tmp_path):
    path = tmp_path / "gtfs.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("trips.txt", "trip_id,route_id,shape_id\ntrip1,routeA,\n")
        zf.writestr(
            "stops.txt",
            "stop_id,stop_lat,stop_lon\ns1,0.0,0.0\ns2,,\ns3,0.02,0.0\n",
        )
        zf.writestr(
            "stop_times.txt",
            "trip_id,stop_id,stop_sequence\ntrip1,s1,0\ntrip1,s2,1\ntrip1,s3,2\n",
        )
    fallback = load_fallback_shapes_from_stops(str(path))
    assert fallback["trip1"] == [(0.0, 0.0), (0.02, 0.0)]


# ---------------------------------------------------------------------------
# match_snapshots
# ---------------------------------------------------------------------------


def _shapes_and_trips():
    return {"trip1": "shape1"}, {"shape1": _STRAIGHT_LINE}


def test_match_snapshots_accepts_close_position(tmp_path):
    trip_shapes, shapes = _shapes_and_trips()
    feed = _make_feed_message([_vehicle_entity("e1", "trip1", 0.005, 0.0, 1_700_000_000)])
    path = _write_pb(tmp_path / "snapshot_20260101-000000.pb", feed)

    df = match_snapshots([path], trip_shapes, shapes)

    assert len(df) == 1
    assert df.iloc[0]["trip_id"] == "trip1"
    assert df.attrs["reject_counts"] == {
        "no_trip_id": 0,
        "unknown_shape": 0,
        "corrupt_snapshot": 0,
        "too_far_from_route": 0,
    }
    assert df.attrs["snapshots_processed"] == 1


def test_match_snapshots_rejects_too_far(tmp_path):
    trip_shapes, shapes = _shapes_and_trips()
    feed = _make_feed_message([_vehicle_entity("e1", "trip1", 0.005, 0.01, 1_700_000_000)])
    path = _write_pb(tmp_path / "snapshot_20260101-000000.pb", feed)

    df = match_snapshots([path], trip_shapes, shapes, max_perpendicular_dist_m=100.0)

    assert len(df) == 0
    assert df.attrs["reject_counts"]["too_far_from_route"] == 1


def test_match_snapshots_rejects_missing_trip_id(tmp_path):
    trip_shapes, shapes = _shapes_and_trips()
    feed = _make_feed_message([_vehicle_entity("e1", "", 0.005, 0.0, 1_700_000_000)])
    path = _write_pb(tmp_path / "snapshot_20260101-000000.pb", feed)

    df = match_snapshots([path], trip_shapes, shapes)

    assert len(df) == 0
    assert df.attrs["reject_counts"]["no_trip_id"] == 1


def test_match_snapshots_rejects_unknown_shape(tmp_path):
    trip_shapes, shapes = _shapes_and_trips()
    feed = _make_feed_message([_vehicle_entity("e1", "trip_unknown", 0.005, 0.0, 1_700_000_000)])
    path = _write_pb(tmp_path / "snapshot_20260101-000000.pb", feed)

    df = match_snapshots([path], trip_shapes, shapes)

    assert len(df) == 0
    assert df.attrs["reject_counts"]["unknown_shape"] == 1


def test_match_snapshots_skips_corrupt_snapshot(tmp_path):
    trip_shapes, shapes = _shapes_and_trips()
    good_feed = _make_feed_message([_vehicle_entity("e1", "trip1", 0.005, 0.0, 1_700_000_000)])
    good_path = _write_pb(tmp_path / "snapshot_20260101-000000.pb", good_feed)
    bad_path = _write_pb(tmp_path / "snapshot_20260101-000001.pb", b"\xff\xfenot-a-feed")

    df = match_snapshots([good_path, bad_path], trip_shapes, shapes)

    assert len(df) == 1
    assert df.attrs["reject_counts"]["corrupt_snapshot"] == 1


def test_match_snapshots_ignores_trip_update_entities(tmp_path):
    trip_shapes, shapes = _shapes_and_trips()
    feed = gtfs_realtime_pb2.FeedMessage(
        header=gtfs_realtime_pb2.FeedHeader(gtfs_realtime_version="2.0"),
        entity=[
            gtfs_realtime_pb2.FeedEntity(
                id="e1",
                trip_update=gtfs_realtime_pb2.TripUpdate(
                    trip=gtfs_realtime_pb2.TripDescriptor(trip_id="trip1")
                ),
            )
        ],
    )
    path = _write_pb(tmp_path / "snapshot_20260101-000000.pb", feed.SerializeToString())

    df = match_snapshots([path], trip_shapes, shapes)

    assert len(df) == 0
    assert sum(df.attrs["reject_counts"].values()) == 0


def test_match_snapshots_sorted_by_trip_then_timestamp(tmp_path):
    trip_shapes = {"trip1": "shape1", "trip2": "shape1"}
    shapes = {"shape1": _STRAIGHT_LINE}

    # Interleave trip1/trip2 observations out of chronological order across snapshots.
    feed_a = _make_feed_message(
        [
            _vehicle_entity("e1", "trip2", 0.0, 0.0, 1_700_000_010),
            _vehicle_entity("e2", "trip1", 0.01, 0.0, 1_700_000_020),
        ]
    )
    feed_b = _make_feed_message(
        [
            _vehicle_entity("e3", "trip1", 0.0, 0.0, 1_700_000_000),
            _vehicle_entity("e4", "trip2", 0.01, 0.0, 1_700_000_030),
        ]
    )
    path_a = _write_pb(tmp_path / "snapshot_20260101-000010.pb", feed_a)
    path_b = _write_pb(tmp_path / "snapshot_20260101-000000.pb", feed_b)

    df = match_snapshots([path_a, path_b], trip_shapes, shapes)

    assert list(df["trip_id"]) == ["trip1", "trip1", "trip2", "trip2"]
    trip1_rows = df[df["trip_id"] == "trip1"]
    trip2_rows = df[df["trip_id"] == "trip2"]
    assert list(trip1_rows["timestamp"]) == sorted(trip1_rows["timestamp"])
    assert list(trip2_rows["timestamp"]) == sorted(trip2_rows["timestamp"])


def test_match_snapshots_glob_order_independent_of_input_order(tmp_path):
    trip_shapes, shapes = _shapes_and_trips()
    feed_a = _make_feed_message([_vehicle_entity("e1", "trip1", 0.0, 0.0, 1_700_000_000)])
    feed_b = _make_feed_message([_vehicle_entity("e2", "trip1", 0.01, 0.0, 1_700_000_010)])
    path_a = _write_pb(tmp_path / "snapshot_20260101-000000.pb", feed_a)
    path_b = _write_pb(tmp_path / "snapshot_20260101-000010.pb", feed_b)

    df_forward = match_snapshots([path_a, path_b], trip_shapes, shapes)
    df_reversed = match_snapshots([path_b, path_a], trip_shapes, shapes)

    assert df_forward.equals(df_reversed)


def test_match_snapshots_empty_input_returns_empty_dataframe_with_columns():
    trip_shapes, shapes = _shapes_and_trips()
    df = match_snapshots([], trip_shapes, shapes)
    assert df.empty
    assert list(df.columns) == [
        "trip_id",
        "timestamp",
        "distance_along_shape_m",
        "perpendicular_dist_m",
        "vehicle_id",
    ]


def test_match_snapshots_shape_cumulative_dist_overrides_distance_axis(tmp_path):
    # FA-10: a point strictly inside the second segment, so there's no
    # perpendicular-distance tie masking the effect of a custom cumulative array.
    trip_shapes, shapes = _shapes_and_trips()
    feed = _make_feed_message([_vehicle_entity("e1", "trip1", 0.015, 0.0, 1_700_000_000)])
    path = _write_pb(tmp_path / "snapshot_20260101-000000.pb", feed)

    df_default = match_snapshots([path], trip_shapes, shapes)
    df_custom = match_snapshots(
        [path], trip_shapes, shapes, shape_cumulative_dist={"shape1": [0.0, 500.0, 1000.0]}
    )

    assert df_default.iloc[0]["distance_along_shape_m"] != df_custom.iloc[0]["distance_along_shape_m"]


def test_match_snapshots_shape_cumulative_dist_omitted_is_unchanged(tmp_path):
    trip_shapes, shapes = _shapes_and_trips()
    feed = _make_feed_message([_vehicle_entity("e1", "trip1", 0.005, 0.0, 1_700_000_000)])
    path = _write_pb(tmp_path / "snapshot_20260101-000000.pb", feed)

    df_without_param = match_snapshots([path], trip_shapes, shapes)
    df_with_none = match_snapshots([path], trip_shapes, shapes, shape_cumulative_dist=None)

    assert df_without_param.equals(df_with_none)


# ---------------------------------------------------------------------------
# project_point_to_polyline_windowed (FA-12)
# ---------------------------------------------------------------------------


def test_project_point_to_polyline_windowed_excludes_out_of_window_pass():
    cumulative = cumulative_distances(_LOOP_LINE)
    d = cumulative[1]

    # The duplicated point (0.01, 0.0) occurs at cumulative[1]==d (early pass) and
    # cumulative[3]==3*d (late pass). Restricting the window to [2d, 4d] excludes the
    # early pass entirely, unlike the unrestricted project_point_to_polyline, which
    # ties to the lowest index (see the sibling test below).
    result = project_point_to_polyline_windowed(0.01, 0.0, _LOOP_LINE, cumulative, 2 * d, 4 * d)

    assert result is not None
    dist_along_m, perp_m = result
    assert dist_along_m == cumulative[3]
    assert perp_m == 0.0


def test_project_point_to_polyline_windowed_unrestricted_would_tie_to_early_pass():
    cumulative = cumulative_distances(_LOOP_LINE)
    d = cumulative[1]
    assert project_point_to_polyline(0.01, 0.0, _LOOP_LINE)[0] == d


def test_project_point_to_polyline_windowed_returns_none_when_nothing_in_range():
    cumulative = cumulative_distances(_LOOP_LINE)
    total_len = cumulative[-1]
    # A window entirely past the end of the polyline can never contain a projected point.
    result = project_point_to_polyline_windowed(
        0.0, 0.0, _LOOP_LINE, cumulative, total_len + 1000.0, total_len + 2000.0
    )
    assert result is None


# ---------------------------------------------------------------------------
# match_snapshots windowed live-position matching (FA-12)
# ---------------------------------------------------------------------------


def _loop_trip_anchors():
    """One trip's hand-built FA-10/FA-11-style anchor list on _LOOP_LINE.

    Mirrors the exact ambiguity test_interpolate.py's own FA-11 test targets (stop_id
    "sX" physically visited twice, at the loop's early and late pass of the same
    duplicated coordinate) - built directly rather than via
    interpolate.resolve_all_trip_stop_anchors, keeping this test focused purely on
    matcher.py's own windowing logic.
    """
    cumulative = cumulative_distances(_LOOP_LINE)
    return [
        (0, "s0", cumulative[0]),
        (1, "sX", cumulative[1]),
        (2, "s2", cumulative[2]),
        (3, "sX", cumulative[3]),
        (4, "s4", cumulative[4]),
    ], cumulative


def test_match_snapshots_windowed_sequence_avoids_distant_loop_pass(tmp_path):
    # Priority acceptance criterion (PRD FA-12): a live observation at a duplicated
    # loop coordinate, reporting current_stop_sequence for the LATE stop, must resolve
    # to the late pass - not the early pass project_point_to_polyline's own
    # context-free tie-break would pick (see the sibling test above), and not the
    # early pass a stop-anchor-only fix (FA-10/FA-11) would still be vulnerable to
    # for the live position axis itself.
    trip_shapes = {"tripL": "loopshape"}
    shapes = {"loopshape": _LOOP_LINE}
    anchors, cumulative = _loop_trip_anchors()
    trip_stop_anchors = {"tripL": anchors}

    feed = _make_feed_message(
        [
            _vehicle_entity("e1", "tripL", 0.0, 0.0, 1_700_000_000, current_stop_sequence=0),
            _vehicle_entity("e2", "tripL", 0.02, 0.0, 1_700_000_010, current_stop_sequence=2),
            _vehicle_entity("e3", "tripL", 0.01, 0.0, 1_700_000_020, current_stop_sequence=3),
        ]
    )
    path = _write_pb(tmp_path / "snapshot_20260101-000000.pb", feed)

    df = match_snapshots([path], trip_shapes, shapes, trip_stop_anchors=trip_stop_anchors)

    assert df.attrs["position_signal"] == "sequence"
    df = df.sort_values("timestamp").reset_index(drop=True)
    # Note: the loop-shaped polyline itself is exact Python floats, but the observed
    # lat/lon passed through a real protobuf round-trip (VehiclePosition.latitude/
    # longitude are proto `float`, i.e. float32) - pytest.approx absorbs that
    # negligible (~1e-8 relative) precision loss, distinct from the tie-break logic
    # under test.
    assert df.iloc[2]["distance_along_shape_m"] == pytest.approx(cumulative[3])

    # Contrast: today's unwindowed matching (trip_stop_anchors omitted) ties to the
    # early pass for that same ambiguous observation - the exact bug this milestone
    # closes for the live-position axis.
    df_legacy = match_snapshots([path], trip_shapes, shapes)
    assert df_legacy.attrs["position_signal"] == "none"
    df_legacy = df_legacy.sort_values("timestamp").reset_index(drop=True)
    assert df_legacy.iloc[2]["distance_along_shape_m"] == pytest.approx(cumulative[1])


def test_match_snapshots_partial_coverage_falls_back_per_observation(tmp_path):
    # Prague-like partial coverage (~68%): observations missing current_stop_sequence
    # must still fall back correctly to unrestricted matching, without breaking the
    # ones that do have the field, at the default (0.60) threshold.
    trip_shapes = {"tripL": "loopshape"}
    shapes = {"loopshape": _LOOP_LINE}
    anchors, cumulative = _loop_trip_anchors()
    trip_stop_anchors = {"tripL": anchors}

    entities = [
        _vehicle_entity("e0", "tripL", 0.0, 0.0, 1_700_000_000, current_stop_sequence=0),
        _vehicle_entity("e1", "tripL", 0.02, 0.0, 1_700_000_010, current_stop_sequence=1),
    ]
    # ~68% coverage (Prague-like): 8 observations with the field (the 2 above + 6 more),
    # 5 without (4 here + 1 ambiguous below) = 8/13 ~= 61.5%, above the 0.60 threshold.
    for i in range(6):
        entities.append(
            _vehicle_entity(f"e_seq{i}", "tripL", 0.0, 0.0, 1_700_000_020 + i, current_stop_sequence=0)
        )
    for i in range(4):
        entities.append(_vehicle_entity(f"e_nofield{i}", "tripL", 0.0, 0.0, 1_700_000_100 + i))
    # The ambiguous duplicated-point observation, missing the field entirely - must fall
    # back to the unrestricted (early-pass) result for this one observation only.
    entities.append(_vehicle_entity("e_ambig", "tripL", 0.01, 0.0, 1_700_000_200))

    feed = _make_feed_message(entities)
    path = _write_pb(tmp_path / "snapshot_20260101-000000.pb", feed)

    df = match_snapshots([path], trip_shapes, shapes, trip_stop_anchors=trip_stop_anchors)

    assert df.attrs["position_signal"] == "sequence"
    coverage = df.attrs["position_signal_coverage"]["sequence"]
    assert 0.60 <= coverage < 0.90

    ambig_row = df[df["timestamp"] == datetime.fromtimestamp(1_700_000_200, tz=timezone.utc)]
    assert len(ambig_row) == 1
    assert ambig_row.iloc[0]["distance_along_shape_m"] == pytest.approx(cumulative[1])


def test_match_snapshots_threshold_comparison_60_vs_90(tmp_path):
    # The exact comparison Michał asked for: the same ~68%-coverage synthetic day,
    # run once at the decided default (0.60, capability engages) and once at a
    # stricter 0.90 (capability does not engage) - demonstrating the practical
    # difference the threshold choice makes on the Prague-like edge case.
    trip_shapes = {"tripL": "loopshape"}
    shapes = {"loopshape": _LOOP_LINE}
    anchors, cumulative = _loop_trip_anchors()
    trip_stop_anchors = {"tripL": anchors}

    entities = [
        _vehicle_entity("e0", "tripL", 0.0, 0.0, 1_700_000_000, current_stop_sequence=0),
        _vehicle_entity("e1", "tripL", 0.02, 0.0, 1_700_000_010, current_stop_sequence=1),
    ]
    for i in range(6):
        entities.append(
            _vehicle_entity(f"e_seq{i}", "tripL", 0.0, 0.0, 1_700_000_020 + i, current_stop_sequence=0)
        )
    for i in range(4):
        entities.append(_vehicle_entity(f"e_nofield{i}", "tripL", 0.0, 0.0, 1_700_000_100 + i))
    entities.append(_vehicle_entity("e_ambig", "tripL", 0.01, 0.0, 1_700_000_200, current_stop_sequence=3))

    feed = _make_feed_message(entities)
    path = _write_pb(tmp_path / "snapshot_20260101-000000.pb", feed)

    df_60 = match_snapshots(
        [path], trip_shapes, shapes, trip_stop_anchors=trip_stop_anchors,
        position_signal_coverage_threshold=0.60,
    )
    df_90 = match_snapshots(
        [path], trip_shapes, shapes, trip_stop_anchors=trip_stop_anchors,
        position_signal_coverage_threshold=0.90,
    )

    assert df_60.attrs["position_signal"] == "sequence"
    assert df_90.attrs["position_signal"] == "none"

    ambig_ts = datetime.fromtimestamp(1_700_000_200, tz=timezone.utc)
    ambig_60 = df_60[df_60["timestamp"] == ambig_ts].iloc[0]
    ambig_90 = df_90[df_90["timestamp"] == ambig_ts].iloc[0]

    # At 0.60: capability engages, the observation's own current_stop_sequence=3
    # windows it correctly to the late pass. At 0.90: capability never engages for
    # this day at all, so the SAME observation - despite carrying the field - falls
    # back to today's unrestricted (early-pass) tie-break.
    assert ambig_60["distance_along_shape_m"] == pytest.approx(cumulative[3])
    assert ambig_90["distance_along_shape_m"] == pytest.approx(cumulative[1])
    assert ambig_60["distance_along_shape_m"] != ambig_90["distance_along_shape_m"]


def test_match_snapshots_zero_coverage_stays_full_fallback(tmp_path):
    # Gdańsk-like: neither field ever populated - the whole day must fall back to
    # today's unrestricted matching, no exceptions, no partial windowing, even though
    # trip_stop_anchors is given.
    trip_shapes = {"tripL": "loopshape"}
    shapes = {"loopshape": _LOOP_LINE}
    anchors, cumulative = _loop_trip_anchors()
    trip_stop_anchors = {"tripL": anchors}

    feed = _make_feed_message(
        [
            _vehicle_entity("e1", "tripL", 0.0, 0.0, 1_700_000_000),
            _vehicle_entity("e2", "tripL", 0.01, 0.0, 1_700_000_010),
        ]
    )
    path = _write_pb(tmp_path / "snapshot_20260101-000000.pb", feed)

    df = match_snapshots([path], trip_shapes, shapes, trip_stop_anchors=trip_stop_anchors)

    assert df.attrs["position_signal"] == "none"
    assert df.attrs["position_signal_coverage"] == {"sequence": 0.0, "stop_id": 0.0}
    row = df[df["timestamp"] == datetime.fromtimestamp(1_700_000_010, tz=timezone.utc)].iloc[0]
    assert row["distance_along_shape_m"] == pytest.approx(cumulative[1])


def test_match_snapshots_stop_id_recurrence_disambiguated_by_prior_confirmed_index(tmp_path):
    # PRD FA-12 "Ograniczenia" point 5: stop_id "sX" occurs twice in this trip's own
    # pattern (stop_sequence 1 and 3, the loop's early and late pass of the same
    # physical stop). No observation ever carries current_stop_sequence, so the day's
    # signal is stop_id. The first "sX" observation (no prior confirmed state) must
    # resolve to the EARLY occurrence; a later "sX" observation, after the trip has
    # already been confirmed past stop_sequence 1, must resolve to the LATE occurrence
    # - not an arbitrary first/last pick.
    trip_shapes = {"tripX": "loopshape"}
    shapes = {"loopshape": _LOOP_LINE}
    anchors, cumulative = _loop_trip_anchors()
    trip_stop_anchors = {"tripX": anchors}

    feed = _make_feed_message(
        [
            _vehicle_entity("e1", "tripX", 0.0, 0.0, 1_700_000_000, stop_id="s0"),
            _vehicle_entity("e2", "tripX", 0.01, 0.0, 1_700_000_010, stop_id="sX"),
            _vehicle_entity("e3", "tripX", 0.02, 0.0, 1_700_000_020, stop_id="s2"),
            _vehicle_entity("e4", "tripX", 0.01, 0.0, 1_700_000_030, stop_id="sX"),
        ]
    )
    path = _write_pb(tmp_path / "snapshot_20260101-000000.pb", feed)

    df = match_snapshots([path], trip_shapes, shapes, trip_stop_anchors=trip_stop_anchors)

    assert df.attrs["position_signal"] == "stop_id"
    df = df.sort_values("timestamp").reset_index(drop=True)
    first_sx = df.iloc[1]
    second_sx = df.iloc[3]
    assert first_sx["distance_along_shape_m"] == pytest.approx(cumulative[1])
    assert second_sx["distance_along_shape_m"] == pytest.approx(cumulative[3])


def test_match_snapshots_windowing_works_for_0_indexed_and_1_indexed_stop_sequence(tmp_path):
    # PRD FA-12 point 4: stop_sequence numbering base must be derived per feed, never
    # hardcoded - confirmed empirically: Poznań is 0-indexed, Prague is 1-indexed.
    # Two feeds differing ONLY in whether their stop_sequence starts at 0 or 1 must
    # both window the same ambiguous observation correctly.
    trip_shapes = {"tripL": "loopshape"}
    shapes = {"loopshape": _LOOP_LINE}
    cumulative = cumulative_distances(_LOOP_LINE)

    zero_indexed_anchors = {
        "tripL": [
            (0, "s0", cumulative[0]),
            (1, "s1", cumulative[1]),
            (2, "s2", cumulative[2]),
            (3, "s3", cumulative[3]),
        ]
    }
    one_indexed_anchors = {
        "tripL": [
            (1, "s0", cumulative[0]),
            (2, "s1", cumulative[1]),
            (3, "s2", cumulative[2]),
            (4, "s3", cumulative[3]),
        ]
    }

    feed_zero_indexed = _make_feed_message(
        [_vehicle_entity("e1", "tripL", 0.01, 0.0, 1_700_000_000, current_stop_sequence=3)]
    )
    feed_one_indexed = _make_feed_message(
        [_vehicle_entity("e1", "tripL", 0.01, 0.0, 1_700_000_000, current_stop_sequence=4)]
    )
    path_zero = _write_pb(tmp_path / "snapshot_20260101-000000.pb", feed_zero_indexed)
    path_one = _write_pb(tmp_path / "snapshot_20260101-000001.pb", feed_one_indexed)

    df_zero = match_snapshots([path_zero], trip_shapes, shapes, trip_stop_anchors=zero_indexed_anchors)
    df_one = match_snapshots([path_one], trip_shapes, shapes, trip_stop_anchors=one_indexed_anchors)

    assert df_zero.attrs["position_signal"] == "sequence"
    assert df_one.attrs["position_signal"] == "sequence"
    assert df_zero.iloc[0]["distance_along_shape_m"] == pytest.approx(cumulative[3])
    assert df_one.iloc[0]["distance_along_shape_m"] == pytest.approx(cumulative[3])


# ---------------------------------------------------------------------------
# observed_trip_ids (FA-12)
# ---------------------------------------------------------------------------


def test_observed_trip_ids_collects_distinct_non_empty_trip_ids(tmp_path):
    feed = gtfs_realtime_pb2.FeedMessage(
        header=gtfs_realtime_pb2.FeedHeader(gtfs_realtime_version="2.0"),
        entity=[
            _vehicle_entity("e1", "trip1", 0.0, 0.0, 1_700_000_000),
            _vehicle_entity("e2", "trip1", 0.01, 0.0, 1_700_000_010),
            _vehicle_entity("e3", "trip2", 0.0, 0.0, 1_700_000_020),
            _vehicle_entity("e4", "", 0.0, 0.0, 1_700_000_030),  # empty trip_id excluded
            gtfs_realtime_pb2.FeedEntity(
                id="e5",
                trip_update=gtfs_realtime_pb2.TripUpdate(
                    trip=gtfs_realtime_pb2.TripDescriptor(trip_id="trip3")
                ),
            ),  # no "vehicle" field - excluded
        ],
    )
    path = _write_pb(tmp_path / "snapshot_20260101-000000.pb", feed.SerializeToString())

    assert observed_trip_ids([path]) == {"trip1", "trip2"}


def test_observed_trip_ids_skips_corrupt_snapshot(tmp_path):
    good_feed = _make_feed_message([_vehicle_entity("e1", "trip1", 0.0, 0.0, 1_700_000_000)])
    good_path = _write_pb(tmp_path / "snapshot_20260101-000000.pb", good_feed)
    bad_path = _write_pb(tmp_path / "snapshot_20260101-000001.pb", b"\xff\xfenot-a-feed")

    assert observed_trip_ids([good_path, bad_path]) == {"trip1"}


# ---------------------------------------------------------------------------
# snapshot_feed_timestamp (FA-6 fix)
# ---------------------------------------------------------------------------


def test_snapshot_feed_timestamp_reads_header_timestamp(tmp_path):
    feed = gtfs_realtime_pb2.FeedMessage(
        header=gtfs_realtime_pb2.FeedHeader(gtfs_realtime_version="2.0", timestamp=1_700_000_000),
    )
    path = _write_pb(tmp_path / "snapshot_20260101-000000.pb", feed.SerializeToString())

    result = snapshot_feed_timestamp(path)

    assert result == datetime.fromtimestamp(1_700_000_000, tz=timezone.utc)


def test_snapshot_feed_timestamp_returns_none_when_unset(tmp_path):
    # header.timestamp left at its proto3 default (0) - some real feeds omit it.
    feed = gtfs_realtime_pb2.FeedMessage(
        header=gtfs_realtime_pb2.FeedHeader(gtfs_realtime_version="2.0"),
    )
    path = _write_pb(tmp_path / "snapshot_20260101-000000.pb", feed.SerializeToString())

    assert snapshot_feed_timestamp(path) is None


def test_snapshot_feed_timestamp_returns_none_for_corrupt_snapshot(tmp_path):
    path = _write_pb(tmp_path / "snapshot_20260101-000000.pb", b"\xff\xfenot-a-feed")

    assert snapshot_feed_timestamp(path) is None


# ---------------------------------------------------------------------------
# FA-15 — per-route reject diagnostics
# ---------------------------------------------------------------------------


def _make_dangling_shape_gtfs_zip(tmp_path: Path) -> Path:
    """A feed reproducing the Łódź 2026-07-24 route-603 defect in miniature.

    shapes.txt EXISTS and is fine for routeA, but routeB's trips reference a shape_id that has
    zero points in it. resolve_trip_shapes' stops-fallback only triggers when shapes.txt is
    missing entirely, so routeB's observations can never match - and, before FA-15, that was
    invisible: the whole-run unknown_shape rate stays low while one route is 100% dead.
    """
    path = tmp_path / "dangling.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "trips.txt",
            "trip_id,route_id,shape_id\n"
            "tripA,routeA,shape1\n"
            "tripB,routeB,shape_missing\n"
            "tripC,routeC,\n",
        )
        zf.writestr("stops.txt", "stop_id,stop_lat,stop_lon\ns1,0.0,0.0\n")
        zf.writestr("stop_times.txt", "trip_id,stop_id,stop_sequence\ntripA,s1,0\n")
        zf.writestr(
            "shapes.txt",
            "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\n"
            "shape1,0.0,0.0,0\n"
            "shape1,0.01,0.0,1\n"
            "shape1,0.02,0.0,2\n",
        )
    return path


def test_load_trip_route_index_keeps_every_trip(tmp_path):
    gtfs = _make_dangling_shape_gtfs_zip(tmp_path)

    trip_routes = load_trip_route_index(str(gtfs))

    assert trip_routes == {"tripA": "routeA", "tripB": "routeB", "tripC": "routeC"}
    # load_trip_shape_index drops the blank-shape_id trip; this index deliberately does not.
    assert "tripC" not in load_trip_shape_index(str(gtfs))


def test_match_snapshots_per_route_breakdown_exposes_fully_rejected_route(tmp_path):
    """Łódź-603 reproduction: one route 100% rejected, whole-run rate unremarkable."""
    gtfs = _make_dangling_shape_gtfs_zip(tmp_path)
    trip_shapes, shapes, _fallback = resolve_trip_shapes(str(gtfs))
    trip_routes = load_trip_route_index(str(gtfs))

    # 8 healthy routeA observations, 2 routeB observations that can never resolve a shape.
    entities = [
        _vehicle_entity(f"a{i}", "tripA", 0.005, 0.0, 1_700_000_000 + i) for i in range(8)
    ]
    entities += [
        _vehicle_entity(f"b{i}", "tripB", 0.005, 0.0, 1_700_000_000 + i) for i in range(2)
    ]
    path = _write_pb(tmp_path / "snapshot_20260101-000000.pb", _make_feed_message(entities))

    df = match_snapshots([path], trip_shapes, shapes, trip_routes=trip_routes)

    by_route = df.attrs["reject_counts_by_route"]
    assert by_route["routeA"] == {"accepted": 8, "unknown_shape": 0, "too_far_from_route": 0}
    assert by_route["routeB"] == {"accepted": 0, "unknown_shape": 2, "too_far_from_route": 0}
    # The whole-run figure alone would not draw anyone's attention - that is the point.
    assert df.attrs["reject_counts"]["unknown_shape"] == 2
    assert len(df) == 8
    # routeB's trips ARE known to the static feed, so nothing is unattributable here.
    assert df.attrs["unattributable_observations"] == 0


def test_match_snapshots_counts_unknown_trip_ids_as_unattributable(tmp_path):
    """Poznań "Bug 1" reproduction: the static feed is from another publication period."""
    gtfs = _make_dangling_shape_gtfs_zip(tmp_path)
    trip_shapes, shapes, _fallback = resolve_trip_shapes(str(gtfs))
    trip_routes = load_trip_route_index(str(gtfs))

    entities = [
        _vehicle_entity("ok", "tripA", 0.005, 0.0, 1_700_000_000),
        _vehicle_entity("x1", "trip_from_another_feed", 0.005, 0.0, 1_700_000_001),
        _vehicle_entity("x2", "trip_from_another_feed_2", 0.005, 0.0, 1_700_000_002),
    ]
    path = _write_pb(tmp_path / "snapshot_20260101-000000.pb", _make_feed_message(entities))

    df = match_snapshots([path], trip_shapes, shapes, trip_routes=trip_routes)

    assert df.attrs["unattributable_observations"] == 2
    # Critically, they are never silently filed under some route.
    assert set(df.attrs["reject_counts_by_route"]) == {"routeA"}
    assert df.attrs["reject_counts"]["unknown_shape"] == 2


def test_match_snapshots_attributes_too_far_from_route_per_route(tmp_path):
    gtfs = _make_dangling_shape_gtfs_zip(tmp_path)
    trip_shapes, shapes, _fallback = resolve_trip_shapes(str(gtfs))
    trip_routes = load_trip_route_index(str(gtfs))

    # 0.01 deg of longitude off the north-south line is ~1.1km - well past the 100m default.
    entities = [
        _vehicle_entity("near", "tripA", 0.005, 0.0, 1_700_000_000),
        _vehicle_entity("far", "tripA", 0.005, 0.01, 1_700_000_001),
    ]
    path = _write_pb(tmp_path / "snapshot_20260101-000000.pb", _make_feed_message(entities))

    df = match_snapshots([path], trip_shapes, shapes, trip_routes=trip_routes)

    assert df.attrs["reject_counts_by_route"]["routeA"] == {
        "accepted": 1,
        "unknown_shape": 0,
        "too_far_from_route": 1,
    }


def test_match_snapshots_without_trip_routes_is_byte_identical(tmp_path):
    """FA-15 is reporting-only: passing trip_routes must not move a single matched value."""
    gtfs = _make_dangling_shape_gtfs_zip(tmp_path)
    trip_shapes, shapes, _fallback = resolve_trip_shapes(str(gtfs))
    trip_routes = load_trip_route_index(str(gtfs))

    entities = [
        _vehicle_entity("a", "tripA", 0.005, 0.0, 1_700_000_000),
        _vehicle_entity("b", "tripB", 0.005, 0.0, 1_700_000_001),
        _vehicle_entity("c", "trip_unknown", 0.005, 0.0, 1_700_000_002),
    ]
    path = _write_pb(tmp_path / "snapshot_20260101-000000.pb", _make_feed_message(entities))

    without = match_snapshots([path], trip_shapes, shapes)
    with_routes = match_snapshots([path], trip_shapes, shapes, trip_routes=trip_routes)

    assert without.equals(with_routes)
    assert without.attrs["reject_counts"] == with_routes.attrs["reject_counts"]
    # Omitting it reproduces the pre-FA-15 "no data" state exactly.
    assert without.attrs["reject_counts_by_route"] == {}
    assert without.attrs["unattributable_observations"] == 0


def test_match_snapshots_per_route_tallies_reconcile_with_whole_run_totals(tmp_path):
    """Every observation lands in exactly one bucket - no double-count, no silent hole.

    The FA-15 diagnostic is only trustworthy if its parts add up to the whole-run totals it is
    printed next to; this is the match-side counterpart of test_build_gtfs.py's own
    corrected/gap reconciliation assertion.
    """
    gtfs = _make_dangling_shape_gtfs_zip(tmp_path)
    trip_shapes, shapes, _fallback = resolve_trip_shapes(str(gtfs))
    trip_routes = load_trip_route_index(str(gtfs))

    entities = (
        [_vehicle_entity(f"a{i}", "tripA", 0.005, 0.0, 1_700_000_000 + i) for i in range(5)]
        + [_vehicle_entity(f"b{i}", "tripB", 0.005, 0.0, 1_700_000_100 + i) for i in range(3)]
        + [_vehicle_entity(f"u{i}", f"unknown{i}", 0.005, 0.0, 1_700_000_200 + i) for i in range(4)]
        + [_vehicle_entity("far", "tripA", 0.005, 0.01, 1_700_000_300)]
        + [_vehicle_entity("none", "", 0.005, 0.0, 1_700_000_400)]
    )
    path = _write_pb(tmp_path / "snapshot_20260101-000000.pb", _make_feed_message(entities))

    df = match_snapshots([path], trip_shapes, shapes, trip_routes=trip_routes)

    rejects = df.attrs["reject_counts"]
    by_route = df.attrs["reject_counts_by_route"]
    route_total = sum(sum(c.values()) for c in by_route.values())

    # accepted + rejected, counted two independent ways, must agree.
    whole_run = (
        len(df)
        + rejects["no_trip_id"]
        + rejects["unknown_shape"]
        + rejects["too_far_from_route"]
    )
    # no_trip_id is rejected before any route lookup, so it belongs to neither per-route bucket.
    assert route_total + df.attrs["unattributable_observations"] + rejects["no_trip_id"] == whole_run
    # And the accepted rows are fully accounted for per route.
    assert sum(c["accepted"] for c in by_route.values()) == len(df)


def test_match_snapshots_empty_trip_routes_means_no_data_not_all_unattributable(tmp_path):
    """An empty dict must mean "no route data", per the documented empty-⇒-no-data convention.

    Reporting every observation as unattributable instead would be a perfect false Bug-1
    signature on a feed that simply has no trips.
    """
    trip_shapes, shapes = _shapes_and_trips()
    feed = _make_feed_message([_vehicle_entity("e1", "trip1", 0.005, 0.0, 1_700_000_000)])
    path = _write_pb(tmp_path / "snapshot_20260101-000000.pb", feed)

    df = match_snapshots([path], trip_shapes, shapes, trip_routes={})

    assert df.attrs["unattributable_observations"] == 0
    assert df.attrs["reject_counts_by_route"] == {}


def test_load_trip_route_index_short_row_gets_empty_route_not_none(tmp_path):
    """csv.DictReader pads a truncated row with None - which must not become a route_id."""
    path = tmp_path / "short_row.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("trips.txt", "trip_id,route_id,shape_id\ntripA,routeA,shape1\ntripB\n")

    trip_routes = load_trip_route_index(str(path))

    assert trip_routes["tripB"] == ""
    assert trip_routes["tripB"] is not None
