"""B5, B6, B7 - regularity, the family that never touches the schedule's travel times.

Worth saying once, because it is the reason to trust these three above the others: headway is
measured between vehicles, so nothing here depends on scheduled travel time, on the first-pair
layover artifact (FA-20), or on the matched table lining up with the right static feed version.
The whole class of defects that FA-16…FA-20 were about cannot reach this page.

For a route running every eight minutes, this is also the family that matches what passengers
actually do: nobody consults a timetable for it, they turn up.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from transit_charts import tidy
from transit_charts.render import html as html_mod
from transit_charts.render import style

# Regularity benchmarks drawn as reference lines on B5. Below 0.25 is widely regarded as
# excellent; the US bus average sits near 0.42.
CV_EXCELLENT = 0.25
CV_TYPICAL = 0.42



def cv_heatmap(
    table: pd.DataFrame,
    *,
    out_prefix: Path,
    source,
    route: str,
    direction: str | None = None,
    bucket_minutes: int = 60,
    min_n: int = 3,
) -> style.ChartResult:
    """B5 - headway coefficient of variation, stop by hour.

    Reading it: a horizontal band of high CV is an hour when the whole route went ragged; a
    vertical band is one stop where it always does, which usually means bunching sets in just
    upstream of it.
    """
    subset = tidy.usable_headways(table, routes=[route])
    direction = direction if direction is not None else tidy.busiest_direction(subset)
    subset = subset[subset.direction_id.astype(str) == str(direction)]
    if subset.empty:
        raise ValueError(f"no usable headways for route {route!r} direction {direction!r}")
    headsign = tidy.direction_label(subset)

    subset["bucket"] = tidy.local_time_bucket(subset.obs_local, bucket_minutes)
    stats = tidy.headway_cv(subset, ["stop_sequence", "bucket"], min_n=min_n)

    grid = stats.pivot(index="stop_sequence", columns="bucket", values="cv").sort_index()
    thin = stats.pivot(index="stop_sequence", columns="bucket", values="below_min_n")

    fig, ax = style.new_figure(width=12.0, height=max(5.0, 0.22 * len(grid)))
    mesh = ax.imshow(
        grid.to_numpy(dtype=float), aspect="auto", origin="lower", cmap="RdYlGn_r",
        vmin=0.0, vmax=1.0, interpolation="nearest",
    )
    bar = fig.colorbar(mesh, ax=ax, pad=0.02)
    bar.set_label("headway CV (0 = perfectly even)", labelpad=12)
    # Benchmarks drawn INSIDE the bar: put beside it they collided with the axis label, and a
    # colourbar whose own label is unreadable is worse than one without annotations.
    for level, text in ((CV_EXCELLENT, "excellent"), (CV_TYPICAL, "US bus avg")):
        bar.ax.axhline(level, color="black", linewidth=1.0)
        bar.ax.text(0.5, level + 0.012, text, fontsize=6.5, ha="center", va="bottom",
                    transform=bar.ax.get_yaxis_transform())

    style.hatch_thin_cells(
        ax,
        [(x, y) for y, row in enumerate(thin.to_numpy()) for x, flag in enumerate(row)
         if bool(flag) or pd.isna(grid.to_numpy()[y][x])],
    )
    ax.set_xticks(range(len(grid.columns)))
    ax.set_xticklabels([f"{int(b) // 60:02d}:{int(b) % 60:02d}" for b in grid.columns],
                       rotation=45)
    ax.set_yticks(range(len(grid.index)))
    ax.set_yticklabels(grid.index)
    ax.set_xlabel("local time of day")
    ax.set_ylabel("stop sequence")
    ax.set_title(
        "B5 · headway regularity, " + tidy.route_direction_title(route, direction, headsign)
    )
    ax.grid(False)

    notes = [
        style.window_note(subset),
        f"CV = sd/mean of observed headway; hatched cells have fewer than {min_n} headways, "
        "where a standard deviation is not a measurement",
        "headways spanning a feed outage excluded; regularity never uses travel time, so the "
        "FA-20 layover artifact cannot reach this chart",
    ]
    style.caption(ax, notes)
    return style.save(
        fig, stats, style.chart_params("B5", source, len(table),
                            {"route": route, "direction": direction, "headsign": headsign,
                             "bucket_minutes": bucket_minutes, "min_n": min_n}, notes),
        out_prefix,
    )


def excess_wait(
    table: pd.DataFrame,
    *,
    out_prefix: Path,
    source,
    routes: list[str] | None = None,
    bucket_minutes: int = 60,
    min_n: int = 5,
    winsorise_quantile: float | None = 0.99,
    interactive: bool = False,
) -> style.ChartResult:
    """B6 - actual vs scheduled wait, with the excess as the visible gap between them.

    Plotting AWT and SWT rather than EWT alone is the point: an excess of two minutes means
    something different on a four-minute service than on a twenty-minute one, and the reader
    can see which they are looking at.
    """
    subset = tidy.usable_headways(table, routes=routes)
    if subset.empty:
        raise ValueError("no usable headways after filtering")
    subset["bucket"] = tidy.local_time_bucket(subset.obs_local, bucket_minutes)

    stats = tidy.wait_times(
        subset, ["route_short_name", "bucket"],
        winsorise_quantile=winsorise_quantile, min_n=min_n,
    )
    if stats.empty:
        raise ValueError(f"no group reached min_n={min_n} headways")

    for column in ("awt_s", "swt_s", "ewt_s", "awt_untrimmed_s", "ewt_untrimmed_s",
                   "mean_headway_s"):
        stats[column.replace("_s", "_min")] = style.to_minutes(stats[column])

    keys = sorted(stats.route_short_name.unique())
    fig, axes = _facet(keys, height_each=2.6)
    colours = style.colour_for(keys)
    for ax, key in zip(axes, keys):
        part = stats[stats.route_short_name == key].sort_values("bucket")
        part = style.on_full_bucket_grid(part, "bucket", bucket_minutes)
        awt = part.awt_min
        swt = part.swt_min
        ax.fill_between(part.bucket, swt, awt, where=awt >= swt, color=colours[key], alpha=0.30,
                        linewidth=0, label="excess wait")
        ax.plot(part.bucket, awt, color=colours[key], linewidth=1.9, label="actual wait")
        ax.plot(part.bucket, swt, color="#555555", linewidth=1.4, linestyle="--",
                label="scheduled wait")
        ax.plot(part.bucket, part.awt_untrimmed_min, color=colours[key],
                linewidth=0.9, linestyle=":", label="actual wait, untrimmed")
        ax.set_ylabel(f"route {key}\nwait (min)")
        ax.grid(True, **style.GRID_KW)

    style.time_of_day_axis(axes[-1])
    axes[-1].set_xlabel("local time of day")
    fig.suptitle(f"B6 · wait a turn-up passenger experiences ({bucket_minutes}-min buckets)")
    style.facet_legend(fig, axes[0].get_legend_handles_labels()[0], ncols=4)

    trimmed_note = (
        f"solid vs dotted = winsorised at p{winsorise_quantile:.0%} vs raw; a large gap between "
        "them IS the finding, not noise to be smoothed"
        if winsorise_quantile else "no winsorising applied"
    )
    notes = [
        style.window_note(subset),
        "wait = E[H^2]/(2 E[H]), not half the mean headway - irregular service puts more "
        f"passengers into the long gaps. {trimmed_note}",
        "BOTH curves only see trips that were observed, so a cancelled trip is invisible to "
        "each: this measures irregularity, not lost capacity",
    ]
    style.caption(axes[-1], notes)
    return style.save(
        fig, stats, style.chart_params("B6", source, len(table),
                            {"routes": keys, "bucket_minutes": bucket_minutes, "min_n": min_n,
                             "winsorise_quantile": winsorise_quantile}, notes),
        out_prefix, rect=(0, 0.06, 1, 0.92),
        html_spec=html_mod.HtmlSpec(
            x="bucket", x_label="local time of day", y_label="wait (min)",
            series=[("awt_min", "actual wait", "#0072B2"),
                    ("swt_min", "scheduled wait", "#555555"),
                    ("awt_untrimmed_min", "actual, untrimmed", "#D55E00")],
            group="route_short_name", x_is_time_of_day=True,
        ) if interactive else None,
    )


def headway_ridgeline(
    table: pd.DataFrame,
    *,
    out_prefix: Path,
    source,
    route: str,
    direction: str | None = None,
    bucket_minutes: int = 60,
    max_headway_min: float = 30.0,
    bins: int = 30,
    min_n: int = 20,
) -> style.ChartResult:
    """B7 - the headway distribution hour by hour, stacked as a ridgeline.

    This is bunching made visible without defining bunching: a tidy unimodal ridge at the
    scheduled interval splitting into two humps - one near zero, one at roughly twice the
    interval - is a pair of vehicles that have caught each other up.

    Densities come from normalised histograms rather than a KDE, deliberately: a KDE needs
    scipy, which is not a dependency of this tool, and its bandwidth choice would smooth away
    the very bimodality the chart exists to show.
    """
    subset = tidy.usable_headways(table, routes=[route])
    direction = direction if direction is not None else tidy.busiest_direction(subset)
    subset = subset[subset.direction_id.astype(str) == str(direction)]
    if subset.empty:
        raise ValueError(f"no usable headways for route {route!r} direction {direction!r}")
    headsign = tidy.direction_label(subset)

    subset["bucket"] = tidy.local_time_bucket(subset.obs_local, bucket_minutes)
    subset["headway_min"] = style.to_minutes(subset.headway_s.astype(float))

    edges = np.linspace(0.0, max_headway_min, bins + 1)
    centres = (edges[:-1] + edges[1:]) / 2
    buckets = sorted(subset.bucket.unique())

    fig, ax = style.new_figure(height=max(5.0, 0.55 * len(buckets)))
    colours = style.colour_for([str(b) for b in buckets])
    rows, offset_step = [], 1.0
    for index, bucket in enumerate(buckets):
        window = subset[subset.bucket == bucket]
        values = window.headway_min.dropna()
        n = len(values)
        # The number that actually governs how much this ridge can be trusted. Each vehicle
        # pair is re-observed at every stop of the route, so `n` overstates the evidence by
        # roughly the number of stops - on a 15-minute service that is ~4 real events per hour
        # dressed up as 125. Printed beside n rather than buried in the caption, because the
        # large number is the one the eye believes.
        independent = window.trip_id.nunique()
        base = index * offset_step
        label = f"{int(bucket) // 60:02d}:{int(bucket) % 60:02d}"
        if n < min_n:
            ax.text(max_headway_min * 0.02, base + 0.12, f"{label}  n={n} (too few)",
                    fontsize=7.5, color=style.THIN_COLOUR, va="bottom")
            rows.append({"bucket": bucket, "n": n, "independent_vehicles": independent,
                         "below_min_n": True})
            continue
        density, _ = np.histogram(values.clip(upper=max_headway_min), bins=edges, density=True)
        scaled = density / density.max() * 0.9 if density.max() > 0 else density
        ax.fill_between(centres, base, base + scaled, color=colours[str(bucket)], alpha=0.55,
                        linewidth=0)
        ax.plot(centres, base + scaled, color=colours[str(bucket)], linewidth=1.0)
        ax.text(max_headway_min * 0.02, base + 0.12,
                f"{label}  n={n} from {independent} vehicles", fontsize=7.5, va="bottom")
        median = float(values.median())
        ax.plot([median, median], [base, base + 0.9], color="black", linewidth=0.8, alpha=0.5)
        for edge_low, edge_high, share in zip(edges[:-1], edges[1:], density):
            rows.append({"bucket": bucket, "n": n, "independent_vehicles": independent,
                         "below_min_n": False,
                         "headway_min_low": edge_low, "headway_min_high": edge_high,
                         "density": share})

    ax.set_yticks([])
    ax.set_xlabel("headway (minutes)")
    ax.set_ylabel("local time of day (one ridge per bucket)")
    ax.set_xlim(0, max_headway_min)
    ax.set_title(
        "B7 · headway distribution by hour, "
        + tidy.route_direction_title(route, direction, headsign)
    )
    ax.grid(True, axis="x", **style.GRID_KW)

    stops_pooled = subset.stop_sequence.nunique()
    notes = [
        style.window_note(subset),
        f"normalised histograms ({bins} bins, clipped at {max_headway_min:.0f} min), each ridge "
        "scaled to its own peak; the vertical tick is that hour's median",
        f"pooled over {stops_pooled} stops, so n counts vehicle-pair-at-stop: each ridge rests "
        "on the handful of vehicles named beside it, re-observed all along the route. Read the "
        "SHAPE - a ridge splitting in two is a pair that caught each other up - never the n",
    ]
    style.caption(ax, notes)
    return style.save(
        fig, pd.DataFrame(rows),
        style.chart_params("B7", source, len(table),
                {"route": route, "direction": direction, "headsign": headsign,
                 "bucket_minutes": bucket_minutes,
                 "max_headway_min": max_headway_min, "bins": bins, "min_n": min_n,
                 "stops_pooled": int(stops_pooled)}, notes),
        out_prefix,
    )


def _facet(keys: list[str], height_each: float):
    fig, axes = style.plt.subplots(
        len(keys), 1, figsize=(12.0, max(3.0, height_each * len(keys))), sharex=True
    )
    return fig, [axes] if len(keys) == 1 else list(axes)
