"""City-wide fluctuation of stop-level headway - the time axis companion to the per-stop map.

The map (built via QGIS, see docs/handoffs/gtfs-rt-visualisation-catalogue_handoff.md I37)
shows one cumulative number per hex over the whole 06:00-22:00 window - each stop's own median
headway over the whole day, coloured in place. This shows how the pooled-across-the-whole-city
headway moves hour by hour instead - a different aggregation on purpose, see
`stop_headway`'s module docstring for why the two disagree - not per-route (H28-H30 already
cover that) and not per-hex (would be too many small multiples to read at once, Michal's call
2026-08-16).
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
        "pooled across every stop, not a per-stop median averaged across stops - a busy "
        "corridor stop's many crossings correctly outweigh a quiet stop's few. The I37 map "
        "answers a different, spatial question (each STOP's own median) and its cumulative "
        "figure will not match this chart's; see stop_headway module docstring",
    ]
    style.caption(ax, notes)
    return style.save(
        fig, stats,
        style.chart_params("H31", source, len(frame),
                            {"bucket_minutes": bucket_minutes, "min_n": min_n}, notes),
        out_prefix,
    )


def citywide_comparison(
    tables: dict[str, pd.DataFrame],
    *,
    out_prefix: Path,
    sources: list,
    bucket_minutes: int = 60,
    min_n: int = 20,
) -> style.ChartResult:
    """J39 - H31 overlaid across cities: one line per city, no band.

    Median only, not mean: `tidy.summarise` deliberately never computes a mean (heavy-tailed
    headways, see its docstring). p25-p75 is dropped, not just kept per-city: N cities' bands
    would occlude each other on one panel, which H31 (one city, one band) does not have to
    solve.

    *tables* comes from `crosscity.group_by_city` - one already-concatenated tidy table per
    city. Outages cannot be reconstructed from an already-extracted table (that requires the
    raw matched positions `stop-headway` reads directly), so this always pools every gap; a
    feed with a genuine recording outage would need re-running `stop-headway` for that city.
    """
    frames = []
    for city in sorted(tables):
        stats = stop_headway.citywide_hourly(
            tables[city], outages=[], bucket_minutes=bucket_minutes, min_n=min_n,
        )
        if stats.empty:
            continue
        stats = stats.copy()
        stats["city"] = city
        frames.append(stats)
    if not frames:
        raise ValueError("no city reached min_n pooled headways in any bucket")
    combined = pd.concat(frames, ignore_index=True)
    combined["headway_p50_min"] = style.to_minutes(combined.p50)

    colours = style.colour_for(list(tables))
    fig, ax = style.new_figure(height=5.5)
    for city, part in combined.groupby("city", sort=True):
        part = style.on_full_bucket_grid(part.sort_values("bucket"), "bucket", bucket_minutes)
        ax.plot(part.bucket, part.headway_p50_min, color=colours[city], linewidth=1.9,
                 label=city)
        style.mark_thin_buckets(ax, part.bucket, part.get("below_min_n", pd.Series(dtype=bool)))

    style.time_of_day_axis(ax)
    ax.set_xlabel("local time of day")
    ax.set_ylabel("pooled stop headway, median (min)")
    ax.set_title("J39 · city-wide stop-level headway through the day, compared across cities")
    ax.legend(loc="upper right", fontsize=8.5, framealpha=0.0, ncols=min(len(tables), 4))

    notes = [
        f"{len(tables)} cities: {', '.join(sorted(tables))}",
        "median only, no p25-p75 band - H31 per city; several bands on one panel would occlude "
        "each other. Pooled across every stop, not per-stop-then-averaged; see H31's caption "
        "and the stop_headway module docstring for why this disagrees with the I37 map's own "
        "cumulative figure.",
        f"buckets below min_n={min_n} marked (grey triangle at the axis floor), not "
        "interpolated over; each city keeps its own recording window, so a city that starts "
        "later simply has a shorter line, not a lower value",
        "outages pooled across every gap - re-run stop-headway per city if a feed had a "
        "genuine recording outage",
    ]
    style.caption(ax, notes)
    return style.save(
        fig, combined,
        style.chart_params(
            "J39", sources, sum(len(t) for t in tables.values()),
            {"bucket_minutes": bucket_minutes, "min_n": min_n, "cities": sorted(tables)}, notes,
        ),
        out_prefix,
    )
