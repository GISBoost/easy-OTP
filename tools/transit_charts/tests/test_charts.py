"""Rendering smoke tests.

These do not check what a figure looks like - that is what eyes are for. They check the
contract every figure owes its reader: the three files exist, the sidecar CSV holds the numbers
that were plotted, and the JSON records enough to reproduce it. A chart whose numbers cannot be
re-read is decoration, not evidence.
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from transit_charts import tidy
from transit_charts.render import punctuality, trajectory


@pytest.fixture
def table(tmp_path):
    """A small but structurally realistic tidy table: 2 routes, 12 runs, 6 stops each."""
    rows = []
    base = pd.Timestamp("2026-07-21T08:00:00Z")
    for route, n_runs in (("11", 8), ("10B", 4)):
        for run in range(n_runs):
            start = base + pd.Timedelta(minutes=20 * run)
            for stop in range(1, 7):
                delay = 30.0 * stop + 20 * (run % 3)      # delay grows along the route
                obs = start + pd.Timedelta(minutes=4 * (stop - 1), seconds=delay)
                rows.append({
                    "city": "testville",
                    "service_date": "2026-07-21",
                    "recording_date": "2026-07-21",
                    "trip_id": f"{route}_{run}",
                    "route_id": route,
                    "route_short_name": route,
                    "route_group": route,
                    "direction_id": "0",
                    "stop_sequence": stop,
                    "stop_id": f"S{stop}",
                    "stop_name": f"Stop {stop}",
                    "shape_dist_m": 500.0 * stop,
                    "sched_arr": start + pd.Timedelta(minutes=4 * (stop - 1)),
                    "sched_dep": start + pd.Timedelta(minutes=4 * (stop - 1)),
                    "obs_time": obs,
                    "obs_local": obs.tz_convert("Europe/Warsaw"),
                    "delay_s": delay,
                    "seg_time_s": 240.0,
                    "seg_dist_m": 500.0,
                    "seg_speed_kmh": 7.5,
                    "seg_status": "no_previous_stop" if stop == 1 else "ok",
                    "is_first_stop": stop == 1,
                    "headway_s": None if run == 0 else 1200.0,
                    "headway_spans_outage": False,
                    "trip_coverage": 1.0,
                    "service_date_offset_days": 0,
                    "service_date_plausible": True,
                })
    frame = pd.DataFrame(rows).reindex(columns=tidy.TIDY_COLUMNS)
    source = tmp_path / "tidy.csv"
    frame.to_csv(source, index=False)
    return frame, source


def _artefacts(result):
    assert result.png.exists() and result.png.stat().st_size > 0
    assert result.csv.exists()
    assert result.json.exists()
    return pd.read_csv(result.csv), json.loads(result.json.read_text(encoding="utf-8"))


def test_c9_writes_one_sidecar_row_per_stop_and_excludes_the_first(table, tmp_path):
    frame, source = table

    result = punctuality.dot_and_whisker(
        frame, out_prefix=tmp_path / "c9", source=source, route="11", min_n=2
    )

    data, meta = _artefacts(result)
    assert set(data.stop_sequence) == {2, 3, 4, 5, 6}   # stop 1 dropped (FA-20)
    assert {"n", "p10_min", "p50_min", "p90_min", "below_min_n"} <= set(data.columns)
    # Minutes, not seconds: the sidecar must carry what the axis shows (delay grows
    # 30 s per stop in the fixture, so stop 6 is 3 min).
    assert data.loc[data.stop_sequence == 6, "p50_min"].iloc[0] == pytest.approx(3.0, abs=0.4)
    assert meta["chart"] == "C9"
    assert meta["options"]["route"] == "11"
    assert meta["source_sha256"]


def test_c10_covers_every_requested_route(table, tmp_path):
    frame, source = table

    result = punctuality.percentile_fan(
        frame, out_prefix=tmp_path / "c10", source=source, bucket_minutes=30, min_n=2
    )

    data, meta = _artefacts(result)
    assert set(data.route_short_name) == {"10B", "11"}
    assert {"p50_min", "p90_min"} <= set(data.columns)
    assert meta["options"]["bucket_minutes"] == 30


def test_c11_shares_sum_to_one_and_carry_n(table, tmp_path):
    frame, source = table

    result = punctuality.punctuality_bands(
        frame, out_prefix=tmp_path / "c11", source=source, bucket_minutes=60, min_n=2
    )

    data, meta = _artefacts(result)
    bands = [name for name, _lo, _hi, _c in punctuality.DEFAULT_BANDS]
    assert data[bands].sum(axis=1).round(6).eq(1.0).all()
    assert "n" in data.columns and (data.n > 0).all()
    # Faceted per route, and the sidecar says which route each share belongs to. The first
    # version pooled every route into one area and named none of them - a share with no
    # stated population, which is exactly what the first reader asked about.
    assert set(data.route_short_name) == {"10B", "11"}
    assert set(meta["options"]["routes"]) == {"10B", "11"}


def test_c11_combine_pools_observations_rather_than_averaging_routes(table, tmp_path):
    """The pooled panel must weigh a route by how much service it ran. Averaging the two
    routes' shares would let 10B's 4 runs count as much as 11's 8, which is a different
    quantity and not a network figure."""
    frame, source = table

    result = punctuality.punctuality_bands(
        frame, out_prefix=tmp_path / "c11", source=source, bucket_minutes=60, min_n=2,
        combine=True,
    )

    data, meta = _artefacts(result)
    assert meta["options"]["combine"] is True
    pooled = data[data.route_short_name == punctuality.POOLED_KEY]
    per_route = data[data.route_short_name != punctuality.POOLED_KEY]
    assert not pooled.empty
    assert pooled.n.sum() == per_route.n.sum()
    bands = [name for name, _lo, _hi, _c in punctuality.DEFAULT_BANDS]
    assert pooled[bands].sum(axis=1).round(6).eq(1.0).all()


def test_c11_combine_is_dropped_when_only_one_route_is_charted(table, tmp_path):
    """Pooling one route with itself is the same panel twice, captioned 'all 1 routes'."""
    frame, source = table

    result = punctuality.punctuality_bands(
        frame, out_prefix=tmp_path / "c11", source=source, routes=["11"], bucket_minutes=60,
        min_n=2, combine=True,
    )

    data, meta = _artefacts(result)
    assert punctuality.POOLED_KEY not in set(data.route_short_name)
    assert meta["options"]["combine"] is False


def test_c11_without_combine_has_no_pooled_row(table, tmp_path):
    frame, source = table

    result = punctuality.punctuality_bands(
        frame, out_prefix=tmp_path / "c11", source=source, bucket_minutes=60, min_n=2
    )

    data, _meta = _artefacts(result)
    assert punctuality.POOLED_KEY not in set(data.route_short_name)


def test_a2_anchors_every_run_on_the_same_stop(table, tmp_path):
    """The false-outlier guard: a run clipped by the window must not start its clock midway."""
    frame, source = table
    clipped = frame.trip_id == "11_3"
    frame = frame[~(clipped & (frame.stop_sequence < 4))]

    result = trajectory.spaghetti(
        frame, out_prefix=tmp_path / "a2", source=source, route="11", min_trip_coverage=0.0
    )

    _data, meta = _artefacts(result)
    assert meta["options"]["anchor_stop_sequence"] == 2
    assert meta["options"]["runs_dropped_missing_anchor"] == 1
    assert meta["options"]["trips"] == 7


def test_a2_keeps_a_run_observed_at_every_other_stop(table, tmp_path):
    """The first line-breaking implementation deleted the point past each gap rather than the
    edge leading to it. A run seen at every other stop has diff == 2 throughout, so every one
    of its points was blanked and the whole trajectory vanished from the figure - while still
    being counted in trip_count and announced in the caption as one of N drawn lines."""
    frame, source = table
    thinned = frame.trip_id == "11_5"
    frame = frame[~(thinned & frame.stop_sequence.isin([3, 5]))]

    result = trajectory.spaghetti(
        frame, out_prefix=tmp_path / "a2", source=source, route="11", min_trip_coverage=0.0
    )

    _data, meta = _artefacts(result)
    assert meta["options"]["trips"] == 8          # the thinned run is still one of them
    # Its remaining stops must survive into the median, i.e. contribute rather than vanish.
    numbers = pd.read_csv(result.csv)
    assert (numbers.n > 0).all()


def test_charts_refuse_an_empty_selection_rather_than_drawing_nothing(table, tmp_path):
    frame, source = table

    with pytest.raises(ValueError):
        punctuality.dot_and_whisker(
            frame, out_prefix=tmp_path / "x", source=source, route="does-not-exist"
        )


def test_thin_buckets_are_marked_not_silently_dropped(table, tmp_path):
    """A hole in a chart reads as zero; below_min_n has to survive into the sidecar so the
    reader can tell the difference."""
    frame, source = table

    result = punctuality.dot_and_whisker(
        frame, out_prefix=tmp_path / "c9thin", source=source, route="11", min_n=1000
    )

    data, _meta = _artefacts(result)
    assert data.below_min_n.all()
    assert data.p50_min.isna().all()
    assert (data.n > 0).all()          # the counts are still there to be read
