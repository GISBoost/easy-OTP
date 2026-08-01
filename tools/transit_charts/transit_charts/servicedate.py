"""GTFS time arithmetic: service dates, and turning stop_times seconds into real instants.

Two things here are easy to get wrong and expensive to notice later, so both are implemented
once and tested rather than open-coded at each call site.

**1. GTFS seconds are converted with stdlib WALL-CLOCK arithmetic, and that is a choice.**

The spec defines the origin as "noon minus 12 hours" of the service date. Taken strictly that
is an *absolute* instant, which on a spring-forward day sits an hour away from local midnight.
This module does not do that. It anchors on local midnight and adds the offset as wall time,
which is what stdlib aware-datetime arithmetic gives for free.

The two agree everywhere except `stop_times` values in 00:00-02:00 on a spring-forward day.
Measured for Europe/Warsaw, 2026-03-29:

    GTFS 01:30  wall-clock -> 01:30 CET   strict noon-12h -> 00:30 CET
    GTFS 08:00  wall-clock -> 08:00 CEST  strict noon-12h -> 08:00 CEST
    GTFS 25:30  wall-clock -> 01:30 CEST  strict noon-12h -> 01:30 CEST

Wall-clock is kept for two reasons. It reproduces the time the operator actually printed - a
01:30 departure exists that night, the skipped hour being 02:00-03:00 - and it agrees with the
rest of this pipeline: `family_a.rebuild_stop_times` accumulates plain seconds and never
consults a timezone, so a strict conversion here would make the charts disagree with the feed
they describe. **If a feed is ever found that means the strict reading, this is the place to
change, and the divergence above is the test to write.**

Where the real hazard lies is a different question, and it is not the stdlib: **pandas does
absolute arithmetic**. ``pd.Timestamp('2026-03-29', tz='Europe/Warsaw') + pd.Timedelta(hours=8)``
yields **09:00** local, an hour out. Every other module here is pandas-first, so the conversion
is deliberately kept in stdlib datetimes and vectorised nowhere. Both behaviours are pinned in
tests/test_servicedate.py.

**2. The service date is not the observation date.** A trip departing 23:50 has stop times
past 86400 and belongs to the *previous* calendar day's service. Rather than trusting a
convention, `infer_service_date` picks the candidate date that actually fits the observations
and reports how well it fits.

Note what that residual does and does not catch. It catches a **matched-table/static-feed
mismatch** - a recycled trip_id, or a static feed from a different publication period - where
no candidate date explains the observations. It does **not** catch Lisbon's stale timestamps,
because those are internally consistent: an observation three weeks old fits its own day's
schedule perfectly. Staleness is only visible relative to the rest of the table, which is
`quality.drop_stale_observations`' job, not this one.

No matplotlib here: this module is part of the extraction layer and must stay importable
wherever `family_a` runs.
"""
from __future__ import annotations

import statistics
import zoneinfo
from datetime import date, datetime, timedelta

# A trip whose best-fitting service date still leaves this much median absolute deviation is
# not a late vehicle, it is a mismatch - a stale timestamp, a recycled trip_id, or a static
# feed from a different publication period. Flagged, never silently dropped.
DEFAULT_MAX_PLAUSIBLE_RESIDUAL_S = 3 * 3600.0


def gtfs_seconds_to_datetime(service_date: date, seconds: float, tz: zoneinfo.ZoneInfo):
    """Instant of a GTFS `stop_times` value on *service_date*, tz-aware.

    Wall-clock arithmetic from local midnight, NOT the spec's strict absolute noon-minus-12h -
    see the module docstring for the measured difference and why this reading is kept. Values
    beyond 86400 (GTFS's >24:00:00 overnight convention) work by construction.
    """
    noon = datetime(service_date.year, service_date.month, service_date.day, 12, tzinfo=tz)
    return noon - timedelta(hours=12) + timedelta(seconds=float(seconds))


def candidate_service_dates(observed_local_date: date) -> list[date]:
    """Service dates a trip observed on *observed_local_date* could plausibly belong to.

    Only the day itself and the one before: a GTFS service day runs from its own 00:00 to at
    most 27-28 hours later in practice, so an observation can belong to today's service or to
    yesterday's overnight tail, never to tomorrow's.
    """
    return [observed_local_date, observed_local_date - timedelta(days=1)]


def infer_service_date(
    scheduled_seconds: list[float],
    observed_instants: list[datetime],
    tz: zoneinfo.ZoneInfo,
    max_residual_s: float = DEFAULT_MAX_PLAUSIBLE_RESIDUAL_S,
) -> tuple[date, float, bool]:
    """Pick the service date that best explains a trip's observed stop crossings.

    *scheduled_seconds* and *observed_instants* are parallel lists over the SAME stops - one
    entry per stop that actually produced a crossing. Returns
    ``(service_date, median_abs_residual_s, plausible)``.

    Method: for each candidate date, convert every scheduled second to an instant and take the
    median absolute difference from the observation. The candidate with the smaller median
    wins. Using the median rather than the mean matters - a trip that loses time badly on one
    segment must not drag the date choice by an hour.

    *plausible* is False when even the winning candidate leaves more than *max_residual_s* -
    the honest signal for "these two datasets do not describe the same vehicle run" (a recycled
    trip_id, or a static feed from another publication period). It is deliberately neither an
    exception nor a silent drop; the caller decides.

    It will NOT flag a stale observation that is internally consistent - see the module
    docstring. That is quality.drop_stale_observations' job.
    """
    if not observed_instants:
        raise ValueError("infer_service_date needs at least one observed crossing")

    local_dates = {t.astimezone(tz).date() for t in observed_instants}
    # Sorted, so a trip straddling local midnight resolves deterministically rather than
    # depending on set iteration order.
    candidates: list[date] = []
    for observed_date in sorted(local_dates):
        for candidate in candidate_service_dates(observed_date):
            if candidate not in candidates:
                candidates.append(candidate)

    best_date = candidates[0]
    best_residual = float("inf")
    for candidate in candidates:
        residuals = [
            abs((obs - gtfs_seconds_to_datetime(candidate, sec, tz)).total_seconds())
            for sec, obs in zip(scheduled_seconds, observed_instants)
        ]
        median_residual = statistics.median(residuals)
        if median_residual < best_residual:
            best_date, best_residual = candidate, median_residual

    return best_date, best_residual, best_residual <= max_residual_s
