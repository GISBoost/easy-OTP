"""Decide whether a static feed's shape_dist_traveled is trustworthy (FA-10).

Family A's stop anchoring (interpolate.stop_distance_along_shape) has been
purely geometric until now - a deliberate "Approved MVP simplification" per
that function's own docstring, because a feed's shape_dist_traveled column
isn't guaranteed to be present, filled, or unit-consistent with the shape's
own geometry. This module supplies the two checks that reversal needs:

1. Shape-level trust (evaluate_shape_trust): does shapes.txt provide a fully
   filled, unit-consistent shape_dist_traveled for this shape_id? Needs only
   shapes.txt data, so it's usable by both 'match' (live vehicle-position
   axis) and 'build' (stop anchoring).
2. Trip-level trust (evaluate_trip_trust): does stop_times.txt provide a
   fully filled shape_dist_traveled for every row of this trip, AND is its
   shape itself trustworthy? Needs stop_times.txt data (StaticIndex), so
   it's 'build'-only.

Cross-city investigation (docs/handoffs/family-a-matching-accuracy_handoff.md)
found Prague is the only confirmed city with this column present and 100%
filled in both files. Łódź and Vilnius have the column present in the header
but every value blank - a naive "does the column exist" check would wrongly
trust it. Both thresholds below are FA-10 defaults, not settled PRD values:
see docs/prd/PR_easy-OTP_family-a-matching-accuracy.md §7 "Open questions",
item #1 (the PRD states no default fill-rate threshold at all) and item #2
(the PRD's own working proposal is +/-20%, explicitly marked as pending
confirmation) - flag any change to these to Michał.

Real-data verification against Prague's live feed (PID) found the ±20% unit
check alone was not enough: PID publishes shape_dist_traveled in KILOMETRES,
consistently across all 7298 shapes and both files (ratio to the haversine
length ~0.001 for every single shape) - not a per-shape anomaly, a feed-wide
convention the PRD's "feet vs metres" example didn't anticipate. Rejecting
everything that isn't already metres left Prague, this milestone's only
confirmed target city, with zero shapes trusted - the fallback path behaved
correctly (proven bit-identical against pre-FA-10 output) but the milestone
accomplished nothing for the one feed it exists for. evaluate_shape_trust
below therefore tests a small set of common shape_dist_traveled unit
conventions (metres/kilometres/miles/feet) against the tolerance, rather than
assuming metres is the only possibility - confirmed with Michał, not a
guess.

Standalone tool code: never imports easy_otp/, never imports osgeo/QGIS, and
is never imported by the plugin.

No QGIS / GDAL imports. Run tests: pytest tests/test_shape_dist.py -v
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from family_a.matcher import haversine_m

if TYPE_CHECKING:
    from family_a.build_gtfs import StaticIndex

logger = logging.getLogger(__name__)

# PRD §7 "Open questions" #1: fraction of a trip's stop_times.txt rows that must have
# a non-blank shape_dist_traveled to trust that trip's stop anchoring. The PRD leaves
# this threshold fully open, with no default proposed at all. This milestone requires
# 100% - flag any change to Michał, per FA-10's own instructions.
FILL_RATE_THRESHOLD = 1.0

# PRD §7 "Open questions" #2: max relative discrepancy between a candidate
# metre-scaled shape_dist_traveled and the shape's haversine polyline length before
# treating that candidate as inconsistent. The PRD's own working proposal is +/-20%,
# explicitly marked as pending confirmation - used here as-is.
UNIT_TOLERANCE = 0.20

# Candidate "multiply raw shape_dist_traveled by this to get metres" conventions to
# test against UNIT_TOLERANCE, tried in this order (metres first, since it's the most
# common case - order otherwise doesn't matter in practice: at UNIT_TOLERANCE=0.20 the
# four candidate bands don't overlap, but the km and miles bands are only ~7% apart at
# their nearest edges, not "orders of magnitude" - a future increase to UNIT_TOLERANCE
# should re-check for overlap before assuming these stay unambiguous). Confirmed with
# Michał 2026-07-23 after real-data verification showed Prague's PID feed uses
# kilometres feed-wide, not metres - km was added as a direct result; miles/feet added
# at the same time since the marginal cost of testing them is negligible and the
# PRD's own unit-inconsistency example was feet vs metres.
_CANDIDATE_UNIT_SCALES: dict[str, float] = {
    "metres": 1.0,
    "kilometres": 1000.0,
    "miles": 1609.344,
    "feet": 0.3048,
}


def _polyline_length_m(polyline: list[tuple[float, float]]) -> float:
    """Total haversine length of a polyline (sum of consecutive-vertex distances)."""
    return sum(
        haversine_m(*polyline[i], *polyline[i + 1]) for i in range(len(polyline) - 1)
    )


def _detect_unit_scale(max_dist: float, haversine_len: float) -> tuple[str, float] | None:
    """Return (unit_name, scale) for the first candidate matching within UNIT_TOLERANCE.

    scale is "multiply raw value by this to get metres". Returns None if no candidate
    in _CANDIDATE_UNIT_SCALES brings max_dist within tolerance of haversine_len.
    """
    for unit_name, scale in _CANDIDATE_UNIT_SCALES.items():
        scaled_max = max_dist * scale
        discrepancy = abs(scaled_max - haversine_len) / haversine_len
        if discrepancy <= UNIT_TOLERANCE:
            return unit_name, scale
    return None


def evaluate_shape_trust(
    shapes: dict[str, list[tuple[float, float]]],
    shape_dist_raw: dict[str, list[float | None]],
) -> tuple[dict[str, list[float]], dict[str, float]]:
    """Return (shape_cumulative_dist, shape_scale_factor).

    shape_cumulative_dist: shape_id -> trustworthy cumulative shape_dist_traveled
    values, already converted to metres. shape_scale_factor: shape_id -> the "multiply
    raw value by this to get metres" factor detected for that shape - callers (e.g.
    evaluate_trip_trust) that also need to convert stop_times.txt's raw per-row values
    for the same shape must apply this same factor, since a feed's shape_dist_traveled
    unit convention is a property of the whole feed/shape, not independently chosen per
    file.

    A shape qualifies when EVERY shapes.txt row for it has a non-blank
    shape_dist_traveled (rejects the Łódź/Vilnius trap: column present, every value
    blank) AND max(values), scaled by one of _CANDIDATE_UNIT_SCALES, is within
    UNIT_TOLERANCE of the shape's own haversine polyline length (rejects a genuine
    unit mismatch not matching any known convention).

    shape_dist_raw is matcher.load_shape_dist_traveled's output - {} (no shapes ever
    had the column at all) produces ({}, {}) back here with no logging, so a feed
    lacking the column entirely (Poznań/Szczecin/Gdańsk) sees zero behaviour change,
    including on logging.
    """
    trustworthy: dict[str, list[float]] = {}
    scale_factor: dict[str, float] = {}
    partial_fill_count = 0

    for shape_id, values in shape_dist_raw.items():
        if not values:
            continue
        blank_count = sum(1 for v in values if v is None)
        if blank_count == len(values):
            # Entirely blank for this shape - the expected shape of the
            # Łódź/Vilnius trap. Not logged per-shape (would spam a whole
            # feed's log); the aggregate summary line below covers it.
            continue
        if blank_count > 0:
            partial_fill_count += 1
            logger.warning(
                "shape_dist.py: shape_id=%s has a partially-filled shape_dist_traveled "
                "in shapes.txt (%d/%d rows blank) - falling back to geometric "
                "projection for this shape.",
                shape_id, blank_count, len(values),
            )
            continue

        polyline = shapes.get(shape_id)
        if not polyline or len(polyline) != len(values):
            # Not expected in practice (shapes and shape_dist_raw come from the same
            # shapes.txt pass), but logged like every other rejection branch here
            # rather than silently skipped, in case that assumption ever breaks.
            logger.warning(
                "shape_dist.py: shape_id=%s has shape_dist_traveled data but no "
                "matching polyline (or a point-count mismatch) in shapes - falling "
                "back to geometric projection for this shape.",
                shape_id,
            )
            continue

        haversine_len = _polyline_length_m(polyline)
        max_dist = max(values)
        if haversine_len <= 0:
            continue

        detected = _detect_unit_scale(max_dist, haversine_len)
        if detected is None:
            logger.warning(
                "shape_dist.py: shape_id=%s shape_dist_traveled max=%.1f vs haversine "
                "polyline length=%.1f matches no known unit convention (tried %s, "
                "tolerance %.0f%%) - treating as unit-inconsistent, falling back to "
                "geometric projection.",
                shape_id, max_dist, haversine_len,
                list(_CANDIDATE_UNIT_SCALES), UNIT_TOLERANCE * 100,
            )
            continue

        unit_name, scale = detected
        trustworthy[shape_id] = [v * scale for v in values]
        scale_factor[shape_id] = scale
        if unit_name != "metres":
            logger.info(
                "shape_dist.py: shape_id=%s shape_dist_traveled detected as %s "
                "(scale=%.4f) - converted to metres.",
                shape_id, unit_name, scale,
            )

    if shape_dist_raw:
        logger.info(
            "shape_dist.py: %d/%d shapes trustworthy for shape_dist_traveled "
            "(%d partially filled).",
            len(trustworthy), len(shape_dist_raw), partial_fill_count,
        )

    return trustworthy, scale_factor


def evaluate_trip_trust(
    static_index: "StaticIndex",
    trip_shapes: dict[str, str],
    trustworthy_shape_cumulative: dict[str, list[float]],
    shape_scale_factor: dict[str, float],
) -> dict[tuple[str, int], float]:
    """Return (trip_id, stop_sequence) -> trusted shape_dist_traveled (metres) for stop anchoring.

    Only includes rows for trips where:
    - the trip's resolved shape_id already passed evaluate_shape_trust, AND
    - every one of the trip's own stop_times.txt rows has a non-blank
      shape_dist_traveled (FILL_RATE_THRESHOLD).

    static_index.stop_time_dist_traveled holds RAW (un-converted) values parsed
    straight from stop_times.txt - each trip's values are multiplied by
    shape_scale_factor[shape_id] (the same factor evaluate_shape_trust detected for
    that shape from shapes.txt) before being returned, since a feed's unit convention
    is shared by both files for a given shape, not independently chosen per file (this
    matches what Prague's real feed showed: shapes.txt and stop_times.txt both in km).

    trustworthy_shape_cumulative == {} (no shape ever trustworthy - every non-Prague
    feed confirmed so far) short-circuits every trip to "not trustworthy" with no
    per-trip work and no logging - zero behaviour change for feeds without a usable
    shape_dist_traveled at all.
    """
    trusted: dict[tuple[str, int], float] = {}
    if not trustworthy_shape_cumulative:
        return trusted

    partial_fill_count = 0
    for trip_id, stops in static_index.trip_stops.items():
        shape_id = trip_shapes.get(trip_id)
        if shape_id not in trustworthy_shape_cumulative:
            continue

        seqs = [seq for seq, *_ in stops]
        if not seqs:
            continue
        values = [static_index.stop_time_dist_traveled.get((trip_id, seq)) for seq in seqs]
        filled = sum(1 for v in values if v is not None)
        fill_rate = filled / len(values)

        if fill_rate < FILL_RATE_THRESHOLD:
            if filled:
                partial_fill_count += 1
                logger.warning(
                    "shape_dist.py: trip_id=%s has a partially-filled shape_dist_traveled "
                    "in stop_times.txt (%d/%d rows blank) - falling back to geometric "
                    "projection for this trip's stop anchoring.",
                    trip_id, len(values) - filled, len(values),
                )
            continue

        scale = shape_scale_factor[shape_id]
        for seq, val in zip(seqs, values):
            assert val is not None  # fill_rate check above guarantees this
            trusted[(trip_id, seq)] = val * scale

    logger.info(
        "shape_dist.py: %d/%d trips trustworthy for shape_dist_traveled stop anchoring "
        "(%d partially filled).",
        len({tid for tid, _ in trusted}), len(static_index.trip_stops), partial_fill_count,
    )

    return trusted
