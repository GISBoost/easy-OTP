"""The D family - the charts that most depend on family_a's filters actually being applied."""
from __future__ import annotations

import json

import pandas as pd
import pytest

from transit_charts import tidy
from transit_charts.render import speed


def _row(**kw):
    base = {
        "city": "testville", "service_date": "2026-07-21", "recording_date": "2026-07-21",
        "trip_id": "t1", "route_id": "R1", "route_short_name": "11", "route_group": "11",
        "direction_id": "0", "stop_sequence": 2, "stop_id": "S2", "stop_name": "Beta",
        "from_stop_id": "S1", "from_stop_name": "Alfa", "shape_dist_m": 1000.0,
        "sched_arr": pd.Timestamp("2026-07-21T08:04:00Z"),
        "sched_dep": pd.Timestamp("2026-07-21T08:04:00Z"),
        "obs_time": pd.Timestamp("2026-07-21T08:05:00Z"),
        "obs_local": pd.Timestamp("2026-07-21T08:05:00Z").tz_convert("Europe/Warsaw"),
        "delay_s": 60.0, "seg_time_s": 240.0, "sched_seg_time_s": 240.0, "seg_dist_m": 500.0,
        "seg_speed_kmh": 7.5, "seg_status": "ok", "is_first_stop": False,
        "headway_s": 600.0, "sched_headway_s": 600.0, "headway_spans_outage": False,
        "trip_coverage": 1.0, "service_date_offset_days": 0, "service_date_plausible": True,
    }
    base.update(kw)
    return base


def _table(rows):
    return pd.DataFrame(rows).reindex(columns=tidy.TIDY_COLUMNS)


def test_usable_segments_drops_every_rejected_status():
    """The single most important line in the D family.

    Speed and running time are exactly what FA-13/FA-18/FA-20 protect. A stationary segment
    left in would render as a 1.5 km/h traffic jam and look entirely plausible.
    """
    table = _table([
        _row(seg_status="ok"),
        _row(seg_status="stationary", seg_speed_kmh=1.5),
        _row(seg_status="first_pair"),
        _row(seg_status="implausible"),
        _row(seg_status="gap", seg_time_s=None),
        _row(seg_status="no_previous_stop", seg_time_s=None),
    ])

    assert list(tidy.usable_segments(table).seg_status) == ["ok"]


def test_scheduled_segment_time_uses_previous_departure_not_arrival():
    """Dwell must not be counted twice: the schedule allows arrival-here minus
    departure-from-there, which is the convention rebuild_stop_times accumulates with."""
    from transit_charts import quality

    crossings = pd.DataFrame({
        "trip_id": ["t1", "t1"], "recording_date": ["2026-07-21"] * 2,
        "route_id": ["R1"] * 2, "direction_id": ["0"] * 2,
        "stop_sequence": [1, 2], "stop_id": ["S1", "S2"],
        "shape_dist_m": [0.0, 500.0],
        # Stop 1: arrive 08:00, wait 60 s, depart 08:01. Stop 2: arrive 08:05.
        # Scheduled travel is 4 minutes, not 5.
        "sched_arr_s": [8 * 3600, 8 * 3600 + 300], "sched_dep_s": [8 * 3600 + 60, 8 * 3600 + 300],
        "obs_time": pd.to_datetime(["2026-07-21T06:00:00Z", "2026-07-21T06:05:00Z"], utc=True),
        "is_first_stop": [True, False], "seg_time_s": [None, 300.0],
        "seg_dist_m": [None, 500.0], "seg_status": ["no_previous_stop", "ok"],
    })

    built = tidy.build(
        crossings, city="t",
        short_name_by_route={"R1": "11"}, group_by_route={"R1": "11"},
        stop_names={"S1": "Alfa", "S2": "Beta"}, agency_tz="Europe/Warsaw",
        outages=[], report=quality.QualityReport(),
    )

    second = built[built.stop_sequence == 2].iloc[0]
    assert second.sched_seg_time_s == pytest.approx(240.0)
    assert second.from_stop_id == "S1"
    assert second.from_stop_name == "Alfa"


def test_negative_scheduled_duration_becomes_nan_not_a_fast_bus():
    """An inconsistent static feed must not read as a segment covered in negative time, which
    would drag a padding median the wrong way."""
    from transit_charts import quality

    crossings = pd.DataFrame({
        "trip_id": ["t1", "t1"], "recording_date": ["2026-07-21"] * 2,
        "route_id": ["R1"] * 2, "direction_id": ["0"] * 2,
        "stop_sequence": [1, 2], "stop_id": ["S1", "S2"], "shape_dist_m": [0.0, 500.0],
        "sched_arr_s": [8 * 3600, 8 * 3600 - 120],   # stop 2 scheduled BEFORE stop 1
        "sched_dep_s": [8 * 3600, 8 * 3600 - 120],
        "obs_time": pd.to_datetime(["2026-07-21T06:00:00Z", "2026-07-21T06:05:00Z"], utc=True),
        "is_first_stop": [True, False], "seg_time_s": [None, 300.0],
        "seg_dist_m": [None, 500.0], "seg_status": ["no_previous_stop", "ok"],
    })

    built = tidy.build(
        crossings, city="t",
        short_name_by_route={"R1": "11"}, group_by_route={"R1": "11"},
        stop_names={}, agency_tz="Europe/Warsaw", outages=[],
        report=quality.QualityReport(),
    )

    assert pd.isna(built[built.stop_sequence == 2].sched_seg_time_s.iloc[0])


def _multi_day(days=3, segments=4):
    rows = []
    for day in range(days):
        date = f"2026-07-2{day + 1}"
        for run in range(6):
            for seq in range(2, 2 + segments):
                observed = 240.0 + 12.0 * seq + (40.0 if seq == 3 else 0) * (run % 3)
                stamp = pd.Timestamp(f"{date}T08:0{run}:00Z")
                rows.append(_row(
                    service_date=date, recording_date=date, trip_id=f"t{day}_{run}",
                    stop_sequence=seq, stop_id=f"S{seq}", from_stop_id=f"S{seq - 1}",
                    seg_time_s=observed, sched_seg_time_s=240.0,
                    obs_time=stamp, obs_local=stamp.tz_convert("Europe/Warsaw"),
                ))
    return _table(rows)


def test_d15_refuses_a_single_day_instead_of_reporting_noise(tmp_path):
    """'Across days' is not a measurement on one day, so the chart declines to draw."""
    one_day = _multi_day(days=1)

    with pytest.raises(ValueError, match="several days"):
        speed.systematic_vs_stochastic(
            [one_day], out_prefix=tmp_path / "d15", sources=[tmp_path / "a.csv"],
            routes=["11"], min_n=1,
        )


def test_d15_separates_the_steady_offset_from_the_swing(tmp_path):
    """Segment 3 is built to be erratic (its loss varies run to run) while the others are
    steadily slow. The chart must put them in different quadrants, which is its whole point."""
    source = tmp_path / "a.csv"
    _multi_day().to_csv(source, index=False)

    result = speed.systematic_vs_stochastic(
        [_multi_day()], out_prefix=tmp_path / "d15", sources=[source], routes=["11"], min_n=3,
    )

    data = pd.read_csv(result.csv)
    erratic = data[data.stop_sequence == 3].iloc[0]
    steady = data[data.stop_sequence == 2].iloc[0]
    assert erratic.stochastic_s > steady.stochastic_s * 5
    assert steady.stochastic_s == pytest.approx(0.0, abs=1.0)
    meta = json.loads(result.json.read_text(encoding="utf-8"))
    assert meta["options"]["days"] == 3
    # city is part of the grouping key, so route "11" in two cities stays two segments rather
    # than being averaged into one point.
    assert "city" in data.columns


def test_d15_labels_only_the_extremes_and_records_which(tmp_path):
    """Every point labelled is a plot nobody can read; the ones worth naming are the ones far
    from the crowd, and the sidecar has to say which those were."""
    source = tmp_path / "a.csv"
    table = _multi_day(segments=8)
    table.to_csv(source, index=False)

    result = speed.systematic_vs_stochastic(
        [table], out_prefix=tmp_path / "d15", sources=[source], routes=["11"], min_n=3,
        annotate=3,
    )

    data = pd.read_csv(result.csv)
    assert data.annotated.sum() <= 3
    # Segment 3 is the only erratic one in the fixture, so its stochastic axis has an IQR of
    # zero across the others. It must still rank as an outlier - the case where one segment
    # alone swings is exactly the one this chart exists to surface.
    assert bool(data.loc[data.stop_sequence == 3, "annotated"].iloc[0])
    assert {"from_stop_name", "stop_name"} <= set(data.columns)


def test_d15_annotate_zero_labels_nothing_but_still_draws(tmp_path):
    source = tmp_path / "a.csv"
    table = _multi_day()
    table.to_csv(source, index=False)

    result = speed.systematic_vs_stochastic(
        [table], out_prefix=tmp_path / "d15", sources=[source], routes=["11"], min_n=3,
        annotate=0,
    )

    assert result.png.stat().st_size > 0
    assert not pd.read_csv(result.csv).annotated.any()


def test_d15_annotate_beyond_the_number_of_points_is_not_an_error(tmp_path):
    source = tmp_path / "a.csv"
    table = _multi_day(segments=2)
    table.to_csv(source, index=False)

    result = speed.systematic_vs_stochastic(
        [table], out_prefix=tmp_path / "d15", sources=[source], routes=["11"], min_n=3,
        annotate=50,
    )

    assert pd.read_csv(result.csv).annotated.sum() <= 2


def test_d14_and_d17_write_their_numbers(tmp_path):
    source = tmp_path / "a.csv"
    table = _multi_day(days=1)
    table.to_csv(source, index=False)

    speeds = speed.speed_heatmap(table, out_prefix=tmp_path / "d14", source=source,
                                 route="11", min_n=1)
    padding = speed.schedule_padding(table, out_prefix=tmp_path / "d17", source=source,
                                     route="11", min_n=1)

    assert pd.read_csv(speeds.csv).speed_kmh.notna().any()
    slack = pd.read_csv(padding.csv)
    # Observed exceeds scheduled everywhere in this fixture, so padding is positive throughout.
    assert (slack.padding_s.dropna() > 0).all()
    assert "padding_min" in slack.columns
