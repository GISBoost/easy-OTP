"""E20 and the HTML backend."""
from __future__ import annotations

import json

import pandas as pd
import pytest

from transit_charts import tidy
from transit_charts.render import crosscity, html, punctuality


def _city_table(city, first_stop_delay_s, later_delay_s, stops=25, runs=20):
    """A city whose stop-1 delay is set independently of the rest - the artifact, synthesised."""
    rows = []
    for run in range(runs):
        start = pd.Timestamp("2026-07-21T08:00:00Z") + pd.Timedelta(minutes=10 * run)
        for seq in range(1, stops + 1):
            delay = first_stop_delay_s if seq == 1 else later_delay_s * seq
            obs = start + pd.Timedelta(minutes=3 * (seq - 1), seconds=delay)
            rows.append({
                "city": city, "service_date": "2026-07-21", "recording_date": "2026-07-21",
                "trip_id": f"{city}_{run}", "route_id": "R", "route_short_name": "1",
                "route_group": "1", "direction_id": "0", "stop_sequence": seq,
                "stop_id": f"S{seq}", "stop_name": f"S{seq}", "from_stop_id": f"S{seq - 1}",
                "from_stop_name": f"S{seq - 1}", "shape_dist_m": 400.0 * seq,
                "sched_arr": start + pd.Timedelta(minutes=3 * (seq - 1)),
                "sched_dep": start + pd.Timedelta(minutes=3 * (seq - 1)),
                "obs_time": obs, "obs_local": obs.tz_convert("Europe/Warsaw"),
                "delay_s": float(delay), "seg_time_s": 180.0, "sched_seg_time_s": 180.0,
                "seg_dist_m": 400.0, "seg_speed_kmh": 8.0, "seg_status": "ok",
                "is_first_stop": seq == 1, "headway_s": 600.0, "sched_headway_s": 600.0,
                "headway_spans_outage": False, "trip_coverage": 1.0,
                "service_date_offset_days": 0, "service_date_plausible": True,
            })
    return pd.DataFrame(rows).reindex(columns=tidy.TIDY_COLUMNS)


def test_e20_separates_a_city_with_the_artifact_from_one_without(tmp_path):
    """The FA-20 argument in one assertion: the first increment towers over the second in the
    affected city and does not in the clean one."""
    tables = {
        "artifacty": _city_table("artifacty", first_stop_delay_s=-400.0, later_delay_s=2.0),
        "clean": _city_table("clean", first_stop_delay_s=2.0, later_delay_s=2.0),
    }
    source = tmp_path / "a.csv"
    source.write_text("x", encoding="utf-8")

    result = crosscity.artifact_profile(
        tables, out_prefix=tmp_path / "e20", sources=[source], min_n=10
    )

    data = pd.read_csv(result.csv).set_index("city")
    assert data.loc["artifacty", "inc_1_2_s"] > 400
    assert abs(data.loc["clean", "inc_1_2_s"]) < 10
    # And the SECOND increment is unremarkable in both - that contrast is the signature.
    assert abs(data.loc["artifacty", "inc_2_3_s"]) < 10


def test_e20_keeps_the_first_stop_unlike_every_other_chart(tmp_path):
    """If the first stop were filtered here as it is elsewhere, the chart would measure
    nothing and silently report zeros."""
    tables = {"c": _city_table("c", first_stop_delay_s=-400.0, later_delay_s=2.0)}
    source = tmp_path / "a.csv"
    source.write_text("x", encoding="utf-8")

    result = crosscity.artifact_profile(
        tables, out_prefix=tmp_path / "e20", sources=[source], min_n=10
    )

    assert pd.read_csv(result.csv).inc_1_2_s.iloc[0] > 400


def test_e20_reports_nan_rather_than_substituting_a_missing_stop(tmp_path):
    """Generalising across cities from unequal stop sets is exactly the error FA-17 made."""
    thin = _city_table("thin", -400.0, 2.0, runs=3)      # far below min_n
    thick = _city_table("thick", -400.0, 2.0, runs=40)
    source = tmp_path / "a.csv"
    source.write_text("x", encoding="utf-8")

    result = crosscity.artifact_profile(
        {"thin": thin, "thick": thick}, out_prefix=tmp_path / "e20",
        sources=[source], min_n=20,
    )

    data = pd.read_csv(result.csv)
    assert set(data.city) == {"thick"}          # thin city omitted, not filled in


def test_group_by_city_reads_the_data_not_the_filename(tmp_path):
    """E20 used to key cities off `path.stem.split("_")[0]`.

    Two tables of one city then overwrote each other in a dict comprehension - one city-day
    vanishing silently - and a file called `2026-07-21_lodz.csv.gz` produced a city named
    "2026-07-21". Reading the `city` column instead makes both impossible, and several days of
    one city concatenate rather than replacing one another.
    """
    monday = _city_table("lodz", 2.0, 2.0, runs=4)
    tuesday = _city_table("lodz", 2.0, 2.0, runs=4)
    rome = _city_table("rome", -400.0, 2.0, runs=4)

    grouped = crosscity.group_by_city([monday, tuesday, rome])

    assert set(grouped) == {"lodz", "rome"}
    assert len(grouped["lodz"]) == len(monday) + len(tuesday)   # concatenated, not overwritten


def test_html_page_is_self_contained_and_carries_the_same_numbers(tmp_path):
    """It has to work from a USB stick: no CDN, no external image, no network."""
    frame = _city_table("c", 2.0, 2.0)
    source = tmp_path / "tidy.csv"
    frame.to_csv(source, index=False)

    result = punctuality.percentile_fan(
        frame, out_prefix=tmp_path / "c10", source=source,
        bucket_minutes=60, min_n=2, interactive=True,
    )

    assert result.html is not None
    page = result.html.read_text(encoding="utf-8")
    assert "http://" not in page.replace("http://www.w3.org/2000/svg", "")
    assert "https://" not in page
    assert "data:image/png;base64," in page
    # The embedded rows must be the sidecar's rows, not a recomputation.
    embedded = json.loads(page.split("window.__CHART__=")[1].split(";</script>")[0])
    assert len(embedded["rows"]) == len(pd.read_csv(result.csv))


def test_html_is_not_written_unless_asked(tmp_path):
    frame = _city_table("c", 2.0, 2.0)
    source = tmp_path / "tidy.csv"
    frame.to_csv(source, index=False)

    result = punctuality.percentile_fan(
        frame, out_prefix=tmp_path / "c10", source=source, bucket_minutes=60, min_n=2
    )

    assert result.html is None
    assert not (tmp_path / "c10.html").exists()
