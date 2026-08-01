"""GTFS time arithmetic - the two things that are wrong in most ad-hoc scripts."""
from __future__ import annotations

import zoneinfo
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from transit_charts.servicedate import (
    candidate_service_dates,
    gtfs_seconds_to_datetime,
    infer_service_date,
)

WARSAW = zoneinfo.ZoneInfo("Europe/Warsaw")
TOKYO = zoneinfo.ZoneInfo("Asia/Tokyo")


def test_ordinary_day_matches_naive_midnight_arithmetic():
    """On a day with no DST transition the noon-12h rule must agree with the obvious version,
    otherwise the correction would be introducing an error rather than removing one."""
    got = gtfs_seconds_to_datetime(date(2026, 7, 21), 8 * 3600 + 30 * 60, WARSAW)
    assert got == datetime(2026, 7, 21, 8, 30, tzinfo=WARSAW)


def test_times_past_midnight_use_the_gtfs_over_24h_convention():
    """25:30:00 on the 21st is 01:30 on the 22nd, still belonging to the 21st's service day."""
    got = gtfs_seconds_to_datetime(date(2026, 7, 21), 25 * 3600 + 30 * 60, WARSAW)
    assert got == datetime(2026, 7, 22, 1, 30, tzinfo=WARSAW)


def test_spring_forward_keeps_scheduled_times_where_the_operator_put_them():
    """Europe/Warsaw springs forward at 02:00 on 2026-03-29; an 08:00 bus is still at 08:00."""
    scheduled = gtfs_seconds_to_datetime(date(2026, 3, 29), 8 * 3600, WARSAW)

    assert scheduled.hour == 8
    assert scheduled.minute == 0
    # The stdlib's own wall-clock arithmetic agrees - this implementation is not compensating
    # for a stdlib bug, and saying so keeps the next reader from "simplifying" it back.
    assert scheduled == datetime(2026, 3, 29, tzinfo=WARSAW) + timedelta(hours=8)


def test_pandas_timedelta_arithmetic_is_the_thing_this_module_avoids():
    """Where the DST danger actually lives.

    pandas does ABSOLUTE arithmetic on a tz-aware Timestamp, so midnight + 8h lands on 09:00
    local across a spring-forward - an entire city reading exactly 3600 s late, on one day a
    year, which is extremely tempting to explain away as data. Every other module here is
    pandas-first, so this test exists to justify keeping the conversion in stdlib datetimes.
    """
    pandas_version = pd.Timestamp("2026-03-29", tz="Europe/Warsaw") + pd.Timedelta(hours=8)
    correct = gtfs_seconds_to_datetime(date(2026, 3, 29), 8 * 3600, WARSAW)

    assert pandas_version.hour == 9
    assert correct.hour == 8
    assert pandas_version.to_pydatetime() != correct


def test_autumn_back_also_holds():
    """2026-10-25 falls back at 03:00; an 08:00 departure is still 08:00."""
    assert gtfs_seconds_to_datetime(date(2026, 10, 25), 8 * 3600, WARSAW).hour == 8


def test_candidates_are_today_and_yesterday_only():
    assert candidate_service_dates(date(2026, 7, 21)) == [date(2026, 7, 21), date(2026, 7, 20)]


def _utc(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


def test_infer_picks_the_observation_day_for_an_ordinary_trip():
    # Scheduled 10:00 and 10:10 local (= 08:00/08:10 UTC in July), observed a minute late.
    service_date, residual, plausible = infer_service_date(
        [10 * 3600, 10 * 3600 + 600],
        [_utc(2026, 7, 21, 8, 1), _utc(2026, 7, 21, 8, 11)],
        WARSAW,
    )
    assert service_date == date(2026, 7, 21)
    assert residual == pytest.approx(60.0)
    assert plausible


def test_infer_picks_the_previous_day_for_an_after_midnight_trip():
    """A trip scheduled 24:10 belongs to the previous service day - the case that makes
    'service date = observation date' quietly wrong.

    24:10 on the 21st is 00:10 local on the 22nd, i.e. 22:10 UTC on the 21st in July (UTC+2).
    """
    service_date, residual, plausible = infer_service_date(
        [24 * 3600 + 600],
        [_utc(2026, 7, 21, 22, 10)],
        WARSAW,
    )
    assert service_date == date(2026, 7, 21)
    assert residual == pytest.approx(0.0)
    assert plausible


def test_infer_flags_a_trip_no_candidate_date_can_explain():
    """What the residual actually catches: a matched/static mismatch.

    Observed at 10:00 local but scheduled at 03:00 means neither today's nor yesterday's
    service explains this vehicle - a recycled trip_id, or a static feed from a different
    publication period. It must come back flagged rather than as a plausible date with a
    seven-hour delay, which would silently poison every chart.

    Note this does NOT catch Lisbon's stale timestamps: those fit their own day's schedule
    perfectly and are only detectable against the rest of the table (see test_quality.py).
    """
    _service_date, residual, plausible = infer_service_date(
        [3 * 3600],
        [_utc(2026, 7, 21, 8, 0)],  # 10:00 local
        WARSAW,
    )
    assert not plausible
    assert residual > 3 * 3600


def test_infer_uses_the_median_so_one_bad_stop_cannot_move_the_date():
    """A trip that loses 40 minutes on one segment is still today's trip."""
    service_date, _residual, plausible = infer_service_date(
        [10 * 3600, 10 * 3600 + 600, 10 * 3600 + 1200],
        [
            _utc(2026, 7, 21, 8, 0),
            _utc(2026, 7, 21, 8, 10),
            _utc(2026, 7, 21, 8, 50),   # the outlier
        ],
        WARSAW,
    )
    assert service_date == date(2026, 7, 21)
    assert plausible


def test_infer_is_correct_in_a_far_eastern_timezone():
    """Guards against a UTC-vs-local slip that a Europe-only test set would never notice."""
    service_date, residual, _plausible = infer_service_date(
        [9 * 3600],
        [_utc(2026, 7, 21, 0, 0)],  # 09:00 in Tokyo
        TOKYO,
    )
    assert service_date == date(2026, 7, 21)
    assert residual == pytest.approx(0.0)


def test_infer_rejects_an_empty_observation_list():
    with pytest.raises(ValueError):
        infer_service_date([], [], WARSAW)
