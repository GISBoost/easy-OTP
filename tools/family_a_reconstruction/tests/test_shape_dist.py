"""Unit tests for family_a.shape_dist (FA-10).

No QGIS, no network - pure stdlib + pytest.
Run: pytest tests/test_shape_dist.py -v
"""

from family_a.build_gtfs import StaticIndex
from family_a.shape_dist import evaluate_shape_trust, evaluate_trip_trust

# Same straight north-south line used in test_matcher.py / test_interpolate.py:
# ~0.01 deg lat apart (~1112m per segment at the equator), so haversine length
# for the full 2-segment line is ~2224m (already relied on, within tolerance,
# by the "fully filled and unit consistent" tests below - not a new assumption).
_STRAIGHT_LINE = [(0.0, 0.0), (0.01, 0.0), (0.02, 0.0)]


def _make_static_index(
    trip_stops: dict[str, list[tuple]],
    stop_time_dist_traveled: dict[tuple[str, int], float | None],
) -> StaticIndex:
    return StaticIndex(
        trip_route={tid: ("R1", "0") for tid in trip_stops},
        trip_stops=trip_stops,
        stop_map={},
        all_trip_ids=set(trip_stops.keys()),
        trip_service_id={tid: "" for tid in trip_stops},
        stop_time_dist_traveled=stop_time_dist_traveled,
    )


# ---------------------------------------------------------------------------
# evaluate_shape_trust - metres (the default/most common case)
# ---------------------------------------------------------------------------


def test_evaluate_shape_trust_fully_filled_and_unit_consistent():
    shapes = {"shape1": _STRAIGHT_LINE}
    shape_dist_raw = {"shape1": [0.0, 1112.0, 2224.0]}

    trustworthy, scale_factor = evaluate_shape_trust(shapes, shape_dist_raw)

    assert trustworthy == {"shape1": [0.0, 1112.0, 2224.0]}
    assert scale_factor == {"shape1": 1.0}


def test_evaluate_shape_trust_entirely_blank_column_is_excluded_without_a_crash(caplog):
    """The Łódź/Vilnius trap: shape_dist_traveled column present in the header,
    every row's value blank. Must not crash and must not silently treat the
    blanks as 0.0 - excluded, no per-shape warning (only the aggregate summary).
    """
    shapes = {"shape1": _STRAIGHT_LINE}
    shape_dist_raw = {"shape1": [None, None, None]}

    with caplog.at_level("INFO"):
        trustworthy, scale_factor = evaluate_shape_trust(shapes, shape_dist_raw)

    assert trustworthy == {}
    assert scale_factor == {}
    assert not any("shape1" in record.message for record in caplog.records if record.levelname == "WARNING")
    assert any("0/1 shapes trustworthy" in record.message for record in caplog.records)


def test_evaluate_shape_trust_partial_fill_is_excluded_and_warned(caplog):
    shapes = {"shape1": _STRAIGHT_LINE}
    shape_dist_raw = {"shape1": [0.0, None, 2224.0]}

    with caplog.at_level("WARNING"):
        trustworthy, scale_factor = evaluate_shape_trust(shapes, shape_dist_raw)

    assert trustworthy == {}
    assert scale_factor == {}
    assert any("shape1" in record.message and "partially-filled" in record.message for record in caplog.records)


def test_evaluate_shape_trust_column_absent_entirely_returns_empty_silently(caplog):
    """A feed lacking shape_dist_traveled entirely (Poznań/Szczecin/Gdańsk) -
    matcher.load_shape_dist_traveled returns {} - must produce ({}, {}) back with no
    logging at all, so those feeds see zero behaviour change including on logs.
    """
    shapes = {"shape1": _STRAIGHT_LINE}
    with caplog.at_level("INFO"):
        trustworthy, scale_factor = evaluate_shape_trust(shapes, {})
    assert trustworthy == {}
    assert scale_factor == {}
    assert caplog.records == []


def test_evaluate_shape_trust_multiple_shapes_mixed_outcomes():
    shapes = {"shape1": _STRAIGHT_LINE, "shape2": _STRAIGHT_LINE}
    shape_dist_raw = {
        "shape1": [0.0, 1112.0, 2224.0],  # trustworthy
        "shape2": [None, None, None],  # Łódź/Vilnius trap
    }

    trustworthy, scale_factor = evaluate_shape_trust(shapes, shape_dist_raw)

    assert trustworthy == {"shape1": [0.0, 1112.0, 2224.0]}
    assert scale_factor == {"shape1": 1.0}


# ---------------------------------------------------------------------------
# evaluate_shape_trust - unit-scale detection (FA-10 real-data follow-up)
#
# Real-data verification against Prague's live feed (PID) found shape_dist_traveled
# published feed-wide in kilometres, not metres (confirmed across all 7298 shapes,
# consistently, in both shapes.txt and stop_times.txt). evaluate_shape_trust must
# detect this (and the other common conventions) rather than rejecting everything
# that isn't already metres.
# ---------------------------------------------------------------------------


def test_evaluate_shape_trust_detects_kilometres_and_converts_to_metres():
    shapes = {"shape1": _STRAIGHT_LINE}
    # Same route as the metres case above, expressed in km (Prague/PID's real convention).
    shape_dist_raw = {"shape1": [0.0, 1.112, 2.224]}

    trustworthy, scale_factor = evaluate_shape_trust(shapes, shape_dist_raw)

    assert trustworthy == {"shape1": [0.0, 1112.0, 2224.0]}
    assert scale_factor == {"shape1": 1000.0}


def test_evaluate_shape_trust_detects_miles_and_converts_to_metres():
    shapes = {"shape1": _STRAIGHT_LINE}
    # ~2224m expressed in miles (2224 / 1609.344 ~= 1.382).
    shape_dist_raw = {"shape1": [0.0, 0.691, 1.382]}

    trustworthy, scale_factor = evaluate_shape_trust(shapes, shape_dist_raw)

    assert scale_factor == {"shape1": 1609.344}
    assert trustworthy["shape1"][-1] == 1.382 * 1609.344


def test_evaluate_shape_trust_detects_feet_and_converts_to_metres():
    shapes = {"shape1": _STRAIGHT_LINE}
    # ~2224m expressed in feet (2224 / 0.3048 ~= 7296).
    shape_dist_raw = {"shape1": [0.0, 3648.0, 7296.0]}

    trustworthy, scale_factor = evaluate_shape_trust(shapes, shape_dist_raw)

    assert scale_factor == {"shape1": 0.3048}
    assert trustworthy["shape1"][-1] == 7296.0 * 0.3048


def test_evaluate_shape_trust_missing_polyline_is_excluded_and_warned(caplog):
    """shape_dist_raw has a shape_id with no matching entry in shapes (or a
    point-count mismatch) - not expected in practice (both come from the same
    shapes.txt pass), but must still be logged, not silently skipped.
    """
    shape_dist_raw = {"shape1": [0.0, 1112.0, 2224.0]}

    with caplog.at_level("WARNING"):
        trustworthy, scale_factor = evaluate_shape_trust({}, shape_dist_raw)

    assert trustworthy == {}
    assert scale_factor == {}
    assert any(
        "shape1" in record.message and "no matching polyline" in record.message
        for record in caplog.records
    )


def test_evaluate_shape_trust_matches_no_known_unit_is_excluded_and_warned(caplog):
    """Genuinely inconsistent data (not a clean multiple of any known unit
    convention) must still be rejected, not force-matched to the closest candidate.
    """
    shapes = {"shape1": _STRAIGHT_LINE}
    shape_dist_raw = {"shape1": [0.0, 250.0, 500.0]}  # fails metres/km/miles/feet alike

    with caplog.at_level("WARNING"):
        trustworthy, scale_factor = evaluate_shape_trust(shapes, shape_dist_raw)

    assert trustworthy == {}
    assert scale_factor == {}
    assert any(
        "shape1" in record.message and "no known unit convention" in record.message
        for record in caplog.records
    )


# ---------------------------------------------------------------------------
# evaluate_trip_trust
# ---------------------------------------------------------------------------


def test_evaluate_trip_trust_fully_filled_trip_on_trustworthy_shape():
    idx = _make_static_index(
        trip_stops={"t1": [(1, "A", 0, 0), (2, "B", 100, 100)]},
        stop_time_dist_traveled={("t1", 1): 0.0, ("t1", 2): 1112.0},
    )
    trip_shapes = {"t1": "shape1"}
    trustworthy_shape_cumulative = {"shape1": [0.0, 1112.0, 2224.0]}
    shape_scale_factor = {"shape1": 1.0}

    result = evaluate_trip_trust(idx, trip_shapes, trustworthy_shape_cumulative, shape_scale_factor)

    assert result == {("t1", 1): 0.0, ("t1", 2): 1112.0}


def test_evaluate_trip_trust_applies_detected_scale_to_stop_time_values():
    """The trip's own raw stop_times.txt values are in km (like shapes.txt) -
    evaluate_trip_trust must apply the same scale evaluate_shape_trust detected,
    not return the raw km values as if they were already metres.
    """
    idx = _make_static_index(
        trip_stops={"t1": [(1, "A", 0, 0), (2, "B", 100, 100)]},
        stop_time_dist_traveled={("t1", 1): 0.0, ("t1", 2): 1.112},  # km, raw
    )
    trip_shapes = {"t1": "shape1"}
    trustworthy_shape_cumulative = {"shape1": [0.0, 1112.0, 2224.0]}  # already metres
    shape_scale_factor = {"shape1": 1000.0}  # km -> m

    result = evaluate_trip_trust(idx, trip_shapes, trustworthy_shape_cumulative, shape_scale_factor)

    assert result == {("t1", 1): 0.0, ("t1", 2): 1112.0}


def test_evaluate_trip_trust_partially_filled_trip_is_excluded_and_warned(caplog):
    idx = _make_static_index(
        trip_stops={"t1": [(1, "A", 0, 0), (2, "B", 100, 100)]},
        stop_time_dist_traveled={("t1", 1): 0.0, ("t1", 2): None},
    )
    trip_shapes = {"t1": "shape1"}
    trustworthy_shape_cumulative = {"shape1": [0.0, 1112.0, 2224.0]}
    shape_scale_factor = {"shape1": 1.0}

    with caplog.at_level("WARNING"):
        result = evaluate_trip_trust(idx, trip_shapes, trustworthy_shape_cumulative, shape_scale_factor)

    assert result == {}
    assert any("t1" in record.message and "partially-filled" in record.message for record in caplog.records)


def test_evaluate_trip_trust_trip_on_non_trustworthy_shape_is_excluded():
    idx = _make_static_index(
        trip_stops={"t1": [(1, "A", 0, 0), (2, "B", 100, 100)]},
        stop_time_dist_traveled={("t1", 1): 0.0, ("t1", 2): 1112.0},
    )
    trip_shapes = {"t1": "shape2"}  # not in trustworthy_shape_cumulative
    trustworthy_shape_cumulative = {"shape1": [0.0, 1112.0, 2224.0]}
    shape_scale_factor = {"shape1": 1.0}

    result = evaluate_trip_trust(idx, trip_shapes, trustworthy_shape_cumulative, shape_scale_factor)

    assert result == {}


def test_evaluate_trip_trust_no_trustworthy_shapes_at_all_short_circuits_silently(caplog):
    """trustworthy_shape_cumulative == {} (every feed confirmed so far except
    Prague) must produce {} with no per-trip work and no logging.
    """
    idx = _make_static_index(
        trip_stops={"t1": [(1, "A", 0, 0), (2, "B", 100, 100)]},
        stop_time_dist_traveled={("t1", 1): 0.0, ("t1", 2): 1112.0},
    )
    trip_shapes = {"t1": "shape1"}

    with caplog.at_level("INFO"):
        result = evaluate_trip_trust(idx, trip_shapes, {}, {})

    assert result == {}
    assert caplog.records == []


# ---------------------------------------------------------------------------
# Full-pipeline synthetic fixtures: trustworthy feed bypasses geometric
# projection entirely for stop anchoring.
# ---------------------------------------------------------------------------


def test_trustworthy_feed_end_to_end_never_calls_geometric_projection(monkeypatch):
    """A Prague-like feed (shape_dist_traveled fully filled, unit-consistent in
    both shapes.txt and stop_times.txt) must never invoke project_point_to_polyline
    for stop anchoring, once shape_dist.py's checks and segment_stats.py's
    trusted_stop_dist wiring are driven end to end.
    """
    from family_a.interpolate import stop_distance_along_shape
    from family_a.segment_stats import _cached_stop_distance

    def _boom(*args, **kwargs):
        raise AssertionError("project_point_to_polyline must not be called for a trustworthy stop")

    monkeypatch.setattr("family_a.interpolate.project_point_to_polyline", _boom)

    shapes = {"shape1": _STRAIGHT_LINE}
    shape_dist_raw = {"shape1": [0.0, 1112.0, 2224.0]}
    idx = _make_static_index(
        trip_stops={"t1": [(1, "A", 0, 0), (2, "B", 100, 100), (3, "C", 200, 200)]},
        stop_time_dist_traveled={("t1", 1): 0.0, ("t1", 2): 1112.0, ("t1", 3): 2224.0},
    )
    trip_shapes = {"t1": "shape1"}

    trustworthy_shapes, scale_factor = evaluate_shape_trust(shapes, shape_dist_raw)
    trusted_stop_dist = evaluate_trip_trust(idx, trip_shapes, trustworthy_shapes, scale_factor)

    cache: dict = {}
    for seq, stop_id, *_rest in idx.trip_stops["t1"]:
        trusted = trusted_stop_dist.get(("t1", seq))
        result = _cached_stop_distance(cache, "shape1", stop_id, {}, shapes["shape1"], None, trusted)
        assert result == shape_dist_raw["shape1"][seq - 1]

    # stop_distance_along_shape itself, called directly (as the PRD's acceptance
    # criterion phrases it), must also skip project_point_to_polyline.
    assert stop_distance_along_shape(0.0, 0.0, _STRAIGHT_LINE, trusted_dist_m=0.0) == 0.0


def test_prague_like_kilometres_feed_end_to_end_never_calls_geometric_projection(monkeypatch):
    """Regression test for the real-data finding: a feed publishing
    shape_dist_traveled in kilometres (not metres) in both files must still reach
    the trusted-direct-value path end to end, not silently fall back because the
    unit check only recognised metres.
    """
    from family_a.interpolate import stop_distance_along_shape
    from family_a.segment_stats import _cached_stop_distance

    def _boom(*args, **kwargs):
        raise AssertionError("project_point_to_polyline must not be called for a trustworthy stop")

    monkeypatch.setattr("family_a.interpolate.project_point_to_polyline", _boom)

    shapes = {"shape1": _STRAIGHT_LINE}
    shape_dist_raw = {"shape1": [0.0, 1.112, 2.224]}  # km, like Prague/PID
    idx = _make_static_index(
        trip_stops={"t1": [(1, "A", 0, 0), (2, "B", 100, 100), (3, "C", 200, 200)]},
        stop_time_dist_traveled={("t1", 1): 0.0, ("t1", 2): 1.112, ("t1", 3): 2.224},  # km, raw
    )
    trip_shapes = {"t1": "shape1"}

    trustworthy_shapes, scale_factor = evaluate_shape_trust(shapes, shape_dist_raw)
    trusted_stop_dist = evaluate_trip_trust(idx, trip_shapes, trustworthy_shapes, scale_factor)

    expected_metres = [0.0, 1112.0, 2224.0]
    cache: dict = {}
    for seq, stop_id, *_rest in idx.trip_stops["t1"]:
        trusted = trusted_stop_dist.get(("t1", seq))
        assert trusted is not None
        result = _cached_stop_distance(cache, "shape1", stop_id, {}, shapes["shape1"], None, trusted)
        assert result == expected_metres[seq - 1]
