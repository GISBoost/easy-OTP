"""Unit tests for family_a.matcher (FA-2).

No QGIS, no network — pure stdlib + pandas + pytest.
Run: pytest tests/test_matcher.py -v
"""

import math
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from google.transit import gtfs_realtime_pb2

from family_a.matcher import (
    load_fallback_shapes_from_stops,
    load_shape_dist_traveled,
    load_shapes,
    load_stop_locations,
    load_trip_shape_index,
    match_snapshots,
    project_point_to_polyline,
    resolve_trip_shapes,
    snapshot_feed_timestamp,
)

# A straight north-south line, ~0.01 deg lat apart (~1.1km per segment) at the equator.
_STRAIGHT_LINE = [(0.0, 0.0), (0.01, 0.0), (0.02, 0.0)]


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
    entity_id: str, trip_id: str, lat: float, lon: float, timestamp: int
) -> gtfs_realtime_pb2.FeedEntity:
    return gtfs_realtime_pb2.FeedEntity(
        id=entity_id,
        vehicle=gtfs_realtime_pb2.VehiclePosition(
            trip=gtfs_realtime_pb2.TripDescriptor(trip_id=trip_id),
            position=gtfs_realtime_pb2.Position(latitude=lat, longitude=lon),
            timestamp=timestamp,
        ),
    )


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
