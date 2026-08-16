"""City-wide fluctuation of stop-level headway - the time axis companion to the per-stop map.

The map (built via QGIS, see docs/handoffs/gtfs-rt-visualisation-catalogue_handoff.md I37)
shows one cumulative number per hex over the whole 06:00-22:00 window. This shows how that
same pooled-across-all-lines headway moves hour by hour, city-wide, in one panel - not
per-route (H28-H30 already cover that) and not per-hex (would be too many small multiples to
read at once, Michal's call 2026-08-16).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from transit_charts import stop_headway
from transit_charts.render import style


def fluctuation(
    frame: pd.DataFrame,
    outages: list[tuple[pd.Timestamp, pd.Timestamp, float]],
    *,
    out_prefix: Path,
    source,
    bucket_minutes: int = 60,
    min_n: int = 20,
) -> style.ChartResult:
    """H31 - median stop-level headway through the day, pooled over every line and every stop.

    One panel, city-wide: the median (p25-p75 band) of the pooled headway (any vehicle, any
    line, same stop_id) computed within each hour bucket. A widening band at a flat median is a
    network that stays on schedule "on average" while individual waits become less predictable -
    exactly the reading B6/C10 give per line, here given for the whole city at once.
    """
    stats = stop_headway.citywide_hourly(frame, outages, bucket_minutes=bucket_minutes, min_n=min_n)
    if stats.empty:
        raise ValueError("no bucket reached min_n pooled headways")
    for column in ("p10", "p25", "p50", "p75", "p90"):
        if column in stats.columns:
            stats[column.replace("p", "headway_p") + "_min"] = style.to_minutes(stats[column])

    stats = style.on_full_bucket_grid(stats.sort_values("bucket"), "bucket", bucket_minutes)

    fig, ax = style.new_figure(height=5.0)
    ax.fill_between(
        stats.bucket, stats.headway_p25_min, stats.headway_p75_min,
        color="#0072B2", alpha=0.30, linewidth=0, label="p25-p75",
    )
    ax.plot(stats.bucket, stats.headway_p50_min, color="#0072B2", linewidth=1.9, label="median")
    style.mark_thin_buckets(ax, stats.bucket, stats.get("below_min_n", pd.Series(dtype=bool)),
                             label=f"n < {min_n}")

    style.time_of_day_axis(ax)
    ax.set_xlabel("local time of day")
    ax.set_ylabel("pooled stop headway (min)")
    ax.set_title("H31 · city-wide stop-level headway through the day, all lines pooled")
    ax.legend(loc="upper right", fontsize=8.5, framealpha=0.0)

    notes = [
        style.window_note(frame),
        "headway pooled across EVERY line and direction sharing a stop_id - the wait a "
        "passenger who does not check which line comes next actually experiences, not a "
        "per-route quantity (compare H28-H30)",
        f"requires a whole-feed extraction (extract run without --route); a route-filtered "
        f"table would understate every stop's real frequency. Buckets below min_n={min_n} "
        "marked, not interpolated over",
    ]
    style.caption(ax, notes)
    return style.save(
        fig, stats,
        style.chart_params("H31", source, len(frame),
                            {"bucket_minutes": bucket_minutes, "min_n": min_n}, notes),
        out_prefix,
    )
