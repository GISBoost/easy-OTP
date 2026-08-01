"""The shared table. These tests pin the behaviours every chart silently depends on."""
from __future__ import annotations

import pandas as pd
import pytest

from transit_charts import quality, tidy

TZ = "Europe/Warsaw"


def _crossings(rows):
    """Build a collect_stop_crossings-shaped frame.

    rows: (trip_id, stop_sequence, stop_id, sched_arr_s, obs_iso_or_None, seg_time_s, seg_dist_m,
           seg_status, route_id, direction_id)
    """
    columns = [
        "trip_id", "stop_sequence", "stop_id", "sched_arr_s", "obs_time",
        "seg_time_s", "seg_dist_m", "seg_status", "route_id", "direction_id",
    ]
    frame = pd.DataFrame(rows, columns=columns)
    frame["recording_date"] = "2026-07-21"
    frame["sched_dep_s"] = frame["sched_arr_s"]
    frame["shape_dist_m"] = 0.0
    frame["is_first_stop"] = frame.stop_sequence == 1
    frame["obs_time"] = pd.to_datetime(frame["obs_time"], utc=True)
    return frame


def _build(frame, outages=None, trip_headsigns=None):
    report = quality.QualityReport()
    table = tidy.build(
        frame,
        city="testville",
        short_name_by_route={"R1": "11"},
        group_by_route={"R1": "11"},
        stop_names={"A": "Alfa", "B": "Beta", "C": "Gamma"},
        trip_headsigns=trip_headsigns,
        agency_tz=TZ,
        outages=outages or [],
        report=report,
    )
    return table, report


def test_delay_is_measured_against_the_scheduled_arrival():
    # 10:00 local = 08:00 UTC in July; observed 90 s late.
    frame = _crossings([
        ("t1", 1, "A", 10 * 3600, "2026-07-21T08:01:30Z", None, None, "no_previous_stop", "R1", "0"),
        ("t1", 2, "B", 10 * 3600 + 600, "2026-07-21T08:11:30Z", 600.0, 1000.0, "first_pair", "R1", "0"),
    ])

    table, _report = _build(frame)

    assert list(table.delay_s) == pytest.approx([90.0, 90.0])
    assert list(table.route_short_name) == ["11", "11"]
    assert list(table.stop_name) == ["Alfa", "Beta"]


def test_missing_crossings_keep_their_row_and_lower_trip_coverage():
    """The window-edge guard: a trip observed only halfway through must be identifiable as
    such, or it silently biases every aggregate keyed on stop_sequence."""
    frame = _crossings([
        ("t1", 1, "A", 10 * 3600, "2026-07-21T08:00:00Z", None, None, "no_previous_stop", "R1", "0"),
        ("t1", 2, "B", 10 * 3600 + 600, None, None, None, "gap", "R1", "0"),
        ("t1", 3, "C", 10 * 3600 + 1200, None, None, None, "gap", "R1", "0"),
    ])

    table, report = _build(frame)

    assert len(table) == 3
    assert table.trip_coverage.round(3).tolist() == [pytest.approx(1 / 3, abs=1e-3)] * 3
    assert report.stops_total == 3
    assert report.stops_crossed == 1
    assert pd.isna(table.loc[table.stop_id == "B", "delay_s"].iloc[0])


def test_headway_is_null_for_the_first_vehicle_never_zero():
    """A zero there would read as perfect bunching and poison every aggregate downstream."""
    frame = _crossings([
        ("t1", 2, "B", 10 * 3600, "2026-07-21T08:00:00Z", 60.0, 500.0, "ok", "R1", "0"),
        ("t2", 2, "B", 10 * 3600, "2026-07-21T08:08:00Z", 60.0, 500.0, "ok", "R1", "0"),
        ("t3", 2, "B", 10 * 3600, "2026-07-21T08:14:00Z", 60.0, 500.0, "ok", "R1", "0"),
    ])

    table, _report = _build(frame)

    by_trip = dict(zip(table.trip_id, table.headway_s))
    assert pd.isna(by_trip["t1"])
    assert by_trip["t2"] == pytest.approx(480.0)
    assert by_trip["t3"] == pytest.approx(360.0)


def test_headway_is_keyed_per_direction_so_opposite_runs_do_not_interleave():
    frame = _crossings([
        ("t1", 2, "B", 10 * 3600, "2026-07-21T08:00:00Z", 60.0, 500.0, "ok", "R1", "0"),
        ("t2", 2, "B", 10 * 3600, "2026-07-21T08:02:00Z", 60.0, 500.0, "ok", "R1", "1"),
        ("t3", 2, "B", 10 * 3600, "2026-07-21T08:08:00Z", 60.0, 500.0, "ok", "R1", "0"),
    ])

    table, _report = _build(frame)

    by_trip = dict(zip(table.trip_id, table.headway_s))
    assert pd.isna(by_trip["t2"])                      # first of its own direction
    assert by_trip["t3"] == pytest.approx(480.0)       # t1 -> t3, not t2 -> t3


def test_headway_on_a_loop_route_does_not_interleave_the_two_passes():
    """Same stop_id twice in one trip: keyed on stop_sequence too, so the second pass is a
    separate service point rather than a bogus few-minute headway."""
    frame = _crossings([
        ("t1", 2, "B", 10 * 3600, "2026-07-21T08:00:00Z", 60.0, 500.0, "ok", "R1", "0"),
        ("t1", 8, "B", 10 * 3600 + 1800, "2026-07-21T08:30:00Z", 60.0, 500.0, "ok", "R1", "0"),
        ("t2", 2, "B", 10 * 3600, "2026-07-21T08:10:00Z", 60.0, 500.0, "ok", "R1", "0"),
    ])

    table, report = _build(frame)

    second_pass = table[(table.trip_id == "t1") & (table.stop_sequence == 8)]
    assert pd.isna(second_pass.headway_s.iloc[0])
    later = table[(table.trip_id == "t2") & (table.stop_sequence == 2)]
    assert later.headway_s.iloc[0] == pytest.approx(600.0)
    assert report.repeated_stop_trips == 1


def test_headway_spanning_an_outage_is_flagged():
    outages = [(pd.Timestamp("2026-07-21T08:05:00Z"), pd.Timestamp("2026-07-21T08:45:00Z"), 2400.0)]
    frame = _crossings([
        ("t1", 2, "B", 10 * 3600, "2026-07-21T08:00:00Z", 60.0, 500.0, "ok", "R1", "0"),
        ("t2", 2, "B", 10 * 3600, "2026-07-21T08:50:00Z", 60.0, 500.0, "ok", "R1", "0"),
    ])

    table, _report = _build(frame, outages)

    flags = dict(zip(table.trip_id, table.headway_spans_outage))
    assert flags["t2"] is True or flags["t2"] == True  # noqa: E712 - numpy bool
    assert not flags["t1"]


def test_speed_is_nan_not_infinite_for_a_zero_length_segment():
    frame = _crossings([
        ("t1", 2, "B", 10 * 3600, "2026-07-21T08:00:00Z", 0.0, 500.0, "implausible", "R1", "0"),
    ])

    table, _report = _build(frame)

    assert pd.isna(table.seg_speed_kmh.iloc[0])


def test_seg_status_is_carried_through_untouched():
    """The chart layer decides what it can tolerate; tidy must not pre-filter for it."""
    frame = _crossings([
        ("t1", 1, "A", 10 * 3600, "2026-07-21T08:00:00Z", None, None, "no_previous_stop", "R1", "0"),
        ("t1", 2, "B", 10 * 3600 + 600, "2026-07-21T08:10:00Z", 600.0, 1000.0, "first_pair", "R1", "0"),
        ("t1", 3, "C", 10 * 3600 + 1200, "2026-07-21T08:20:00Z", 600.0, 300.0, "stationary", "R1", "0"),
    ])

    table, _report = _build(frame)

    assert list(table.seg_status) == ["no_previous_stop", "first_pair", "stationary"]
    assert len(table) == 3


def test_summarise_reports_n_and_marks_thin_groups_without_dropping_them():
    """A hole in a chart reads as zero; an explicit 'insufficient data' does not."""
    frame = pd.DataFrame({
        "bucket": ["a"] * 5 + ["b"] * 2,
        "delay_s": [10.0, 20, 30, 40, 50, 100, 200],
    })

    out = tidy.summarise(frame, ["bucket"], "delay_s", min_n=3)

    thin = out[out.bucket == "b"].iloc[0]
    thick = out[out.bucket == "a"].iloc[0]
    assert thin.n == 2 and thin.below_min_n and pd.isna(thin.p50)
    assert thick.n == 5 and not thick.below_min_n and thick.p50 == pytest.approx(30.0)
    assert "mean" not in out.columns          # medians only, on purpose


def test_local_time_bucket_uses_local_wall_clock_not_utc():
    """08:00 UTC is 10:00 in Warsaw; a chart axis built on the UTC hour would be two hours out."""
    local = pd.Series(pd.to_datetime(["2026-07-21T08:00:00Z"], utc=True)).dt.tz_convert(TZ)

    assert list(tidy.local_time_bucket(local, 60)) == [600]  # minutes since local midnight


def test_direction_label_prefers_the_headsign_passengers_see():
    frame = _crossings([
        ("t1", 1, "A", 10 * 3600, "2026-07-21T08:00:00Z", None, None, "no_previous_stop", "R1", "1"),
        ("t1", 2, "B", 10 * 3600 + 600, "2026-07-21T08:10:00Z", 600.0, 1000.0, "ok", "R1", "1"),
    ])

    table, _report = _build(frame, trip_headsigns={"t1": "Chocianowice IKEA"})

    assert tidy.direction_label(table) == "Chocianowice IKEA"
    assert tidy.route_direction_title("11", "1", "Chocianowice IKEA") == (
        "route 11 -> Chocianowice IKEA (direction 1)"
    )


def test_direction_label_falls_back_to_the_last_stop_when_the_feed_has_no_headsign():
    """trip_headsign is optional in GTFS and blank in several of the feeds here. Naming the
    direction after the terminus is nearly always the same fact, and always better than '1'."""
    frame = _crossings([
        ("t1", 1, "A", 10 * 3600, "2026-07-21T08:00:00Z", None, None, "no_previous_stop", "R1", "1"),
        ("t1", 2, "B", 10 * 3600 + 600, "2026-07-21T08:10:00Z", 600.0, 1000.0, "ok", "R1", "1"),
        ("t1", 3, "C", 10 * 3600 + 1200, "2026-07-21T08:20:00Z", 600.0, 1000.0, "ok", "R1", "1"),
    ])

    table, _report = _build(frame)

    assert tidy.direction_label(table) == "Gamma"


def test_route_direction_title_omits_the_arrow_when_nothing_names_the_direction():
    assert tidy.route_direction_title("11", "0", "") == "route 11 (direction 0)"


def test_empty_crossings_produce_the_full_schema():
    table, _report = _build(pd.DataFrame(columns=["trip_id"]))

    assert table.empty
    assert list(table.columns) == tidy.TIDY_COLUMNS
