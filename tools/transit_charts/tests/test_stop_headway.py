"""Pooled-across-lines stop headway. The one thing worth getting wrong here is pooling by the
wrong key - route-level headway (tidy.headway_s) and this are different quantities on purpose.
"""
from __future__ import annotations

import pandas as pd
import pytest

from transit_charts import stop_headway


def _frame(rows):
    """rows: list of (stop_id, route_id, minute_offset, stop_name)."""
    base = pd.Timestamp("2026-07-23 10:00:00", tz="UTC")
    return pd.DataFrame({
        "stop_id": [r[0] for r in rows],
        "route_id": [r[1] for r in rows],
        "obs_time": [base + pd.Timedelta(minutes=r[2]) for r in rows],
        "obs_local": [base + pd.Timedelta(minutes=r[2]) for r in rows],
        "stop_name": [r[3] for r in rows],
    })


def test_headway_pools_across_routes_at_the_same_stop():
    """Two different routes at one stop must still produce a headway between them.

    tidy.headway_s would see these as two independent series (different route_id) and never
    pair them - the whole point of this module is that a passenger does not care.
    """
    frame = _frame([
        ("S1", "10", 0, "Centrum"),
        ("S1", "55", 5, "Centrum"),   # different route, same physical stop
        ("S1", "10", 9, "Centrum"),
    ])
    crossings = stop_headway._pooled_crossings(frame, outages=[])

    assert len(crossings) == 2  # first arrival has no predecessor
    assert crossings.headway_s.tolist() == pytest.approx([300.0, 240.0])


def test_first_arrival_at_a_stop_has_no_headway_row():
    """Never a 0-minute headway for a vehicle with no predecessor (same convention as tidy)."""
    frame = _frame([("S1", "10", 0, "Centrum")])
    crossings = stop_headway._pooled_crossings(frame, outages=[])
    assert crossings.empty


def test_headway_spanning_an_outage_is_dropped():
    frame = _frame([("S1", "10", 0, "Centrum"), ("S1", "10", 40, "Centrum")])
    base = pd.Timestamp("2026-07-23 10:00:00", tz="UTC")
    outages = [(base + pd.Timedelta(minutes=10), base + pd.Timedelta(minutes=30), 1200.0)]
    crossings = stop_headway._pooled_crossings(frame, outages=outages)
    assert crossings.empty


def test_per_stop_summary_flags_thin_stops_instead_of_dropping_them():
    """A stop below min_n keeps its row (n visible) but gets no median - same convention as
    tidy.summarise, so a thin stop reads as 'not enough data', not as 'zero'."""
    frame = _frame([
        ("BUSY", "10", 0, "Busy"), ("BUSY", "11", 3, "Busy"),
        ("BUSY", "12", 6, "Busy"), ("BUSY", "10", 9, "Busy"),
        ("THIN", "10", 0, "Thin"), ("THIN", "11", 5, "Thin"),
    ])
    stats, missing = stop_headway.per_stop_summary(frame, outages=[], stop_locations={
        "BUSY": (51.0, 19.0), "THIN": (51.1, 19.1),
    }, min_n=3)

    busy = stats.set_index("stop_id").loc["BUSY"]
    thin = stats.set_index("stop_id").loc["THIN"]
    assert busy.n == 3 and pytest.approx(busy.median_headway_min) == 3.0
    assert thin.n == 1 and thin.below_min_n and pd.isna(thin.median_headway_min)
    assert missing == 0


def test_per_stop_summary_reports_stops_with_no_coordinate():
    frame = _frame([("S1", "10", 0, "X"), ("S1", "11", 5, "X")])
    stats, missing = stop_headway.per_stop_summary(frame, outages=[], stop_locations={}, min_n=1)
    assert missing == 1
    assert stats.lat.isna().all()


def test_citywide_hourly_buckets_by_the_later_arrival():
    frame = _frame([
        ("S1", "10", 0, "X"), ("S1", "11", 5, "X"),     # both in the 10:00 bucket
        ("S2", "10", 61, "Y"), ("S2", "11", 65, "Y"),   # both in the 11:00 bucket
    ])
    stats = stop_headway.citywide_hourly(frame, outages=[], bucket_minutes=60, min_n=1)
    buckets = dict(zip(stats.bucket, stats.n))
    assert buckets == {600: 1, 660: 1}
