"""Data-quality guards. Each test names the real archive that motivated the guard."""
from __future__ import annotations

import pandas as pd
import pytest

from transit_charts import quality


def _matched(rows):
    frame = pd.DataFrame(rows, columns=["trip_id", "timestamp", "distance_along_shape_m"])
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    return frame


def test_stale_observations_are_dropped_and_reported_by_date():
    """Lisbon 2026-07-28: exactly 944 observations on each of three foreign dates.

    Dropping them silently would be just as wrong as keeping them - the count and the dates
    are what let someone ask why.
    """
    rows = [("t1", f"2026-07-28T10:0{i}:00Z", 0.0) for i in range(6)]
    rows += [("t1", "2026-07-08T10:00:00Z", 0.0), ("t1", "2026-07-21T10:00:00Z", 0.0)]
    report = quality.QualityReport()

    kept = quality.drop_stale_observations(_matched(rows), report)

    assert len(kept) == 6
    assert report.observations_in == 8
    assert report.observations_kept == 6
    assert report.stale_observations == 2
    assert report.stale_dates == {"2026-07-08": 1, "2026-07-21": 1}
    assert report.dominant_recording_date == "2026-07-28"
    assert "2026-07-08 x1" in report.render()


def test_an_overnight_tail_is_not_stale():
    """A trip running past local midnight lands on the next calendar date legitimately, so the
    one-day slack has to be real slack and not a rounding accident."""
    rows = [("t1", f"2026-07-28T2{i}:00:00Z", 0.0) for i in range(4)]
    rows += [("t1", "2026-07-29T00:30:00Z", 0.0)]
    report = quality.QualityReport()

    kept = quality.drop_stale_observations(_matched(rows), report)

    assert len(kept) == 5
    assert report.stale_observations == 0


def test_outage_detection_finds_the_silence_and_reports_its_length():
    rows = [("t1", "2026-07-21T10:00:00Z", 0.0), ("t1", "2026-07-21T10:01:00Z", 0.0)]
    rows += [("t1", "2026-07-21T10:40:00Z", 0.0)]  # 39-minute silence

    outages = quality.find_outages(_matched(rows), gap_s=300.0)

    assert len(outages) == 1
    assert outages[0][2] == pytest.approx(39 * 60)


def test_normal_polling_is_not_an_outage():
    rows = [("t1", f"2026-07-21T10:0{i}:00Z", 0.0) for i in range(5)]
    assert quality.find_outages(_matched(rows), gap_s=300.0) == []


def test_headway_spanning_an_outage_is_flagged():
    """The point of detecting outages at all: a headway measured across one is an artefact of
    the recording, and publishing it as a service gap would misrepresent the operator."""
    outages = [(pd.Timestamp("2026-07-21T10:05:00Z"), pd.Timestamp("2026-07-21T10:45:00Z"), 2400.0)]
    start = pd.Series(pd.to_datetime(["2026-07-21T10:00:00Z", "2026-07-21T11:00:00Z"], utc=True))
    end = pd.Series(pd.to_datetime(["2026-07-21T10:50:00Z", "2026-07-21T11:08:00Z"], utc=True))

    flags = quality.spans_outage(start, end, outages)

    assert list(flags) == [True, False]


def test_non_monotonic_trip_is_flagged_but_gps_noise_is_not():
    """FA-2 documents small backward jumps on ~9% of real trips; a 900 m reversal is a
    different animal, most likely two vehicles sharing one trip_id."""
    noisy = _matched([
        ("noise", "2026-07-21T10:00:00Z", 1000.0),
        ("noise", "2026-07-21T10:01:00Z", 980.0),      # 20 m back - ordinary
        ("swap", "2026-07-21T10:00:00Z", 1000.0),
        ("swap", "2026-07-21T10:01:00Z", 100.0),       # 900 m back - not ordinary
    ])

    assert quality.flag_non_monotonic_trips(noisy, tolerance_m=100.0) == {"swap"}


def test_report_always_states_the_optimistic_bias():
    """Coverage is never just a number here: a vehicle that vanishes from the feed WHILE STUCK
    contributes nothing, so the reader has to be told which way the omissions push."""
    report = quality.QualityReport(stops_total=100, stops_crossed=60)

    rendered = report.render()

    assert "60/100 (60.0%)" in rendered
    assert "biased optimistic" in rendered


def test_empty_input_does_not_raise():
    report = quality.QualityReport()
    empty = _matched([])
    assert quality.drop_stale_observations(empty, report).empty
    assert quality.find_outages(empty) == []
    assert quality.flag_non_monotonic_trips(empty) == set()
