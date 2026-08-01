"""Regularity maths. The formulas here are the ones most easily got subtly wrong."""
from __future__ import annotations

import pandas as pd
import pytest

from transit_charts import tidy


def _frame(observed, scheduled=None, **extra):
    scheduled = scheduled if scheduled is not None else observed
    base = {
        "route_short_name": "11", "route_group": "11", "direction_id": "0",
        "headway_spans_outage": False,
    }
    base.update(extra)
    return pd.DataFrame({
        "headway_s": pd.Series(observed, dtype="Float64"),
        "sched_headway_s": pd.Series(scheduled, dtype="Float64"),
        **{k: [v] * len(observed) for k, v in base.items()},
    })


def test_perfectly_even_service_waits_exactly_half_the_headway():
    """The sanity anchor: with no variance, E[H^2]/(2 E[H]) collapses to E[H]/2."""
    out = tidy.wait_times(_frame([600.0] * 10), ["route_short_name"], min_n=3)

    assert out.awt_s.iloc[0] == pytest.approx(300.0)
    assert out.swt_s.iloc[0] == pytest.approx(300.0)
    assert out.ewt_s.iloc[0] == pytest.approx(0.0)


def test_irregular_service_costs_more_than_half_the_mean_headway():
    """Why the formula is not mean/2.

    Alternating 2 and 18 minutes averages 10, so a naive 'half the headway' says 5 minutes.
    But 90% of turn-up passengers arrive into the 18-minute gap, and the real mean wait is
    E[H^2]/(2 E[H]) = (120^2 + 1080^2)/2 / (2*600) = 8.2 minutes.
    """
    out = tidy.wait_times(_frame([120.0, 1080.0] * 6), ["route_short_name"], min_n=3)

    assert out.mean_headway_s.iloc[0] == pytest.approx(600.0)
    assert out.awt_s.iloc[0] == pytest.approx(492.0, abs=1.0)
    assert out.awt_s.iloc[0] > out.mean_headway_s.iloc[0] / 2


def test_excess_is_measured_against_the_schedule_not_against_the_observed_mean():
    """A route planned irregularly must not be charged for the irregularity it was given."""
    out = tidy.wait_times(
        _frame([120.0, 1080.0] * 6, scheduled=[120.0, 1080.0] * 6),
        ["route_short_name"], min_n=3,
    )

    assert out.ewt_s.iloc[0] == pytest.approx(0.0)   # delivered exactly what was planned


def test_untrimmed_value_is_always_returned_so_trimming_cannot_hide_a_collapse():
    """One 90-minute hole in otherwise tidy 10-minute service.

    Winsorising keeps the chart readable, but if it silently swallowed the hole the chart
    would say the service was fine on the one day it was not. The gap between the two numbers
    IS the finding, so both travel together.
    """
    headways = [600.0] * 20 + [5400.0]
    # n=21, E[H] = 17400/21 = 828.57, E[H^2] = 36360000/21 = 1731429,
    # so AWT = 1731429 / (2 x 828.57) = 1044.8 s - the one hole nearly quadruples the wait.
    # Winsorising at p90 caps every value at 600, giving a flat 300 s.
    out = tidy.wait_times(_frame(headways), ["route_short_name"],
                          winsorise_quantile=0.90, min_n=3)

    assert out.awt_s.iloc[0] == pytest.approx(300.0)
    assert out.awt_untrimmed_s.iloc[0] == pytest.approx(1044.8, abs=1.0)
    assert out.awt_untrimmed_s.iloc[0] > out.awt_s.iloc[0] * 3


def test_no_winsorising_leaves_the_two_identical():
    out = tidy.wait_times(_frame([600.0] * 5 + [5400.0]), ["route_short_name"],
                          winsorise_quantile=None, min_n=3)

    assert out.awt_s.iloc[0] == pytest.approx(out.awt_untrimmed_s.iloc[0])


def test_groups_below_min_n_are_reported_as_thin_not_dropped():
    """Dropping them left a hole in B6, and a hole in a chart reads as zero. Same rule as
    summarise(): keep the row, keep n, blank the statistics, flag it."""
    out = tidy.wait_times(_frame([600.0, 600.0]), ["route_short_name"], min_n=5)

    assert len(out) == 1
    row = out.iloc[0]
    assert row.n == 2 and row.below_min_n
    assert pd.isna(row.awt_s) and pd.isna(row.ewt_s)


def test_scheduled_headway_is_paired_on_the_same_vehicles_as_the_observed_one():
    """The defect that inflated B6 by about a minute of wait nobody experienced.

    Scheduled headway used to be computed over every row carrying a schedule, including stops
    with no crossing. When a vehicle is missed the observed side merges two intervals into one
    while the schedule side does not, so observed headway ran systematically longer than
    scheduled - measured on Łódź route 11 as 16.16 min against 15.00 - and AWT is quadratic in
    headway, so that fed straight through to the excess.
    """
    from transit_charts import quality
    from transit_charts import tidy as tidy_mod

    # Three vehicles 10 minutes apart on paper; the middle one is never crossed.
    crossings = pd.DataFrame({
        "trip_id": ["a", "b", "c"], "recording_date": ["2026-07-21"] * 3,
        "route_id": ["R"] * 3, "direction_id": ["0"] * 3,
        "stop_sequence": [2] * 3, "stop_id": ["S2"] * 3, "shape_dist_m": [500.0] * 3,
        "sched_arr_s": [36000, 36600, 37200], "sched_dep_s": [36000, 36600, 37200],
        "obs_time": pd.to_datetime(
            ["2026-07-21T08:00:00Z", None, "2026-07-21T08:20:00Z"], utc=True
        ),
        "is_first_stop": [False] * 3, "seg_time_s": [120.0] * 3,
        "seg_dist_m": [500.0] * 3, "seg_status": ["ok"] * 3,
    })
    built = tidy_mod.build(
        crossings, city="t", short_name_by_route={"R": "11"}, group_by_route={"R": "11"},
        stop_names={}, agency_tz="Europe/Warsaw", outages=[], report=quality.QualityReport(),
    )

    row = built[built.trip_id == "c"].iloc[0]
    assert row.headway_s == pytest.approx(1200.0)      # a -> c, the missed vehicle merged in
    assert row.sched_headway_s == pytest.approx(1200.0)  # SAME pair of vehicles, not 600
    assert row.headway_skips_vehicles == 1             # and the skip is counted, not hidden


def test_cv_is_undefined_below_three_observations():
    """A standard deviation from two numbers is arithmetic, not a measurement."""
    frame = pd.concat([
        _frame([600.0, 700.0], route_short_name="thin"),
        _frame([600.0, 700.0, 800.0, 900.0], route_short_name="thick"),
    ], ignore_index=True)

    out = tidy.headway_cv(frame, ["route_short_name"], min_n=3)

    thin = out[out.route_short_name == "thin"].iloc[0]
    thick = out[out.route_short_name == "thick"].iloc[0]
    assert pd.isna(thin.cv) and thin.below_min_n and thin.n == 2
    assert thick.cv == pytest.approx(0.169, abs=0.01) and not thick.below_min_n


def test_usable_headways_drops_outage_spanning_intervals_by_default():
    """A headway measured across a feed silence describes the recording, not the operator."""
    frame = _frame([600.0, 2400.0])
    frame.loc[1, "headway_spans_outage"] = True

    default = tidy.usable_headways(frame)
    forced = tidy.usable_headways(frame, include_outage_spanning=True)

    assert list(default.headway_s) == [600.0]
    assert len(forced) == 2


def test_usable_headways_keeps_the_first_stop():
    """Regularity never touches travel time, so the FA-20 layover artifact cannot reach it -
    excluding stop 1 here would throw away good data for no reason."""
    frame = _frame([600.0, 600.0])
    frame["is_first_stop"] = [True, False]

    assert len(tidy.usable_headways(frame)) == 2
