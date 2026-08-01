"""D14, D17, D15 - where time is lost, whether the timetable knew, and what to do about it.

These three are the ones that most need `seg_status == "ok"`, and they all go through
`tidy.usable_segments` to get it. Speed and running time are exactly what FA-13/FA-18/FA-20
exist to protect: without that filter a vehicle sitting out its layover renders as a 1.5 km/h
traffic jam, which is the most convincing lie this tool could tell.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

from transit_charts import tidy
from transit_charts.render import style



def _segment_labels(frame: pd.DataFrame) -> pd.Series:
    """`12 Piotrkowska -> Zielona`, ordered by stop_sequence so the axis follows the route."""
    return frame.stop_sequence.astype(str) + " " + frame.from_stop_name + " -> " + frame.stop_name


def speed_heatmap(
    table: pd.DataFrame,
    *,
    out_prefix: Path,
    source,
    route: str,
    direction: str | None = None,
    bucket_minutes: int = 120,
    min_n: int = 3,
) -> style.ChartResult:
    """D14 - median segment speed, segment by time band.

    Defaults are 2-hour bands and min_n=3 rather than hourly and 5, and that is not a loosened
    standard - it is an achievable one. A segment-hour on a route running every 15 minutes can
    hold at most ~4 observations, so a threshold of 5 suppressed 97% of the grid and produced a
    chart that read as "this route has no data".

    Bottleneck diagnosis in one picture: a dark row is a link that is always slow, a dark
    column is an hour when the whole route is, and a dark cell where the two cross is the one
    worth sending someone to look at.
    """
    subset = tidy.usable_segments(table, routes=[route])
    direction = direction if direction is not None else tidy.busiest_direction(subset)
    subset = subset[subset.direction_id.astype(str) == str(direction)]
    if subset.empty:
        raise ValueError(f"no usable segments for route {route!r} direction {direction!r}")
    headsign = tidy.direction_label(subset)

    subset["bucket"] = tidy.local_time_bucket(subset.obs_local, bucket_minutes)
    stats = (
        subset.groupby(["stop_sequence", "bucket"], sort=True)
        .agg(n=("seg_speed_kmh", "count"), speed_kmh=("seg_speed_kmh", "median"))
        .reset_index()
    )
    stats["below_min_n"] = stats.n < min_n
    stats.loc[stats.below_min_n, "speed_kmh"] = float("nan")
    labels = subset.drop_duplicates("stop_sequence").sort_values("stop_sequence")
    stats["segment"] = stats.stop_sequence.map(
        dict(zip(labels.stop_sequence, _segment_labels(labels)))
    )

    grid = stats.pivot(index="stop_sequence", columns="bucket", values="speed_kmh").sort_index()
    fig, ax = style.new_figure(width=12.5, height=max(5.0, 0.24 * len(grid)))
    mesh = ax.imshow(grid.to_numpy(dtype=float), aspect="auto", origin="lower", cmap="RdYlGn",
                     interpolation="nearest")
    bar = fig.colorbar(mesh, ax=ax, pad=0.02)
    bar.set_label("median segment speed (km/h)", labelpad=10)

    missing = np.argwhere(np.isnan(grid.to_numpy(dtype=float)))
    style.hatch_thin_cells(ax, [(int(x), int(y)) for y, x in missing])

    ax.set_xticks(range(len(grid.columns)))
    ax.set_xticklabels([f"{int(b) // 60:02d}:{int(b) % 60:02d}" for b in grid.columns],
                       rotation=45)
    ax.set_yticks(range(len(grid.index)))
    ax.set_yticklabels(grid.index)
    ax.set_xlabel("local time of day")
    ax.set_ylabel("segment (ending at stop sequence)")
    ax.set_title("D14 · segment speed, " + tidy.route_direction_title(route, direction, headsign))
    ax.grid(False)

    notes = [
        style.window_note(subset),
        f"median of observed segment speeds; hatched cells have fewer than {min_n} "
        "observations or none at all",
        "only segments that passed FA-13/FA-18/FA-20 are included - without that filter a "
        "terminus layover would render here as a 1.5 km/h jam",
    ]
    warning = style.thin_grid_warning("D14", stats.n, min_n)
    if warning:
        notes.append(warning)
        print(f"note: {warning}", file=sys.stderr)
    style.caption(ax, notes)
    return style.save(
        fig, stats, style.chart_params("D14", source, len(table),
                            {"route": route, "direction": direction, "headsign": headsign,
                             "bucket_minutes": bucket_minutes, "min_n": min_n}, notes),
        out_prefix,
    )


def schedule_padding(
    table: pd.DataFrame,
    *,
    out_prefix: Path,
    source,
    route: str,
    direction: str | None = None,
    bucket_minutes: int = 120,
    min_n: int = 3,
) -> style.ChartResult:
    """D17 - observed running time minus the time the timetable allows, per segment and hour.

    Positive (red) means the schedule is **too tight** there: vehicles cannot do it in the time
    given, and the delay they accumulate is designed in. Negative (blue) means **padding**: the
    timetable allows more than the segment needs, so vehicles arrive early and either wait or
    drift ahead of schedule.

    This is the chart that turns a delay measurement into something actionable, because the two
    findings have opposite remedies - one wants more running time, the other wants less.
    """
    subset = tidy.usable_segments(table, routes=[route])
    direction = direction if direction is not None else tidy.busiest_direction(subset)
    subset = subset[
        (subset.direction_id.astype(str) == str(direction)) & subset.sched_seg_time_s.notna()
    ]
    if subset.empty:
        raise ValueError(f"no usable segments with a scheduled duration for route {route!r}")
    headsign = tidy.direction_label(subset)

    subset["padding_s"] = subset.seg_time_s - subset.sched_seg_time_s
    subset["bucket"] = tidy.local_time_bucket(subset.obs_local, bucket_minutes)
    stats = (
        subset.groupby(["stop_sequence", "bucket"], sort=True)
        .agg(n=("padding_s", "count"), padding_s=("padding_s", "median"),
             sched_seg_time_s=("sched_seg_time_s", "median"))
        .reset_index()
    )
    stats["below_min_n"] = stats.n < min_n
    stats.loc[stats.below_min_n, "padding_s"] = float("nan")
    stats["padding_min"] = style.to_minutes(stats.padding_s)

    grid = stats.pivot(index="stop_sequence", columns="bucket", values="padding_min").sort_index()
    values = grid.to_numpy(dtype=float)
    limit = float(np.nanpercentile(np.abs(values), 98)) if np.isfinite(values).any() else 1.0

    fig, ax = style.new_figure(width=11.0, height=max(5.0, 0.24 * len(grid)))
    mesh = ax.imshow(values, aspect="auto", origin="lower", cmap="RdBu_r",
                     vmin=-limit, vmax=limit, interpolation="nearest")
    bar = fig.colorbar(mesh, ax=ax, pad=0.02)
    bar.set_label("observed minus scheduled (min)\nred = schedule too tight, blue = padded",
                  labelpad=10)
    style.hatch_thin_cells(
        ax, [(int(x), int(y)) for y, x in np.argwhere(np.isnan(values))]
    )

    ax.set_xticks(range(len(grid.columns)))
    ax.set_xticklabels([f"{int(b) // 60:02d}:{int(b) % 60:02d}" for b in grid.columns],
                       rotation=45)
    ax.set_yticks(range(len(grid.index)))
    ax.set_yticklabels(grid.index)
    ax.set_xlabel("local time of day")
    ax.set_ylabel("segment (ending at stop sequence)")
    ax.set_title("D17 · schedule slack, " + tidy.route_direction_title(route, direction, headsign))
    ax.grid(False)

    notes = [
        style.window_note(subset),
        f"median of (observed - scheduled) running time per segment; colour scale clipped at "
        f"+/-{limit:.1f} min (p98) so a single outlier cannot flatten the rest",
        "scheduled travel is arrival minus the PREVIOUS stop's departure, so dwell is not "
        "double-counted - the same convention rebuild_stop_times uses",
    ]
    warning = style.thin_grid_warning("D17", stats.n, min_n)
    if warning:
        notes.append(warning)
        print(f"note: {warning}", file=sys.stderr)
    style.caption(ax, notes)
    return style.save(
        fig, stats, style.chart_params("D17", source, len(table),
                            {"route": route, "direction": direction, "headsign": headsign,
                             "bucket_minutes": bucket_minutes, "min_n": min_n}, notes),
        out_prefix,
    )


def systematic_vs_stochastic(
    tables: list[pd.DataFrame],
    *,
    out_prefix: Path,
    sources: list[Path],
    routes: list[str] | None = None,
    direction: str | None = None,
    min_days: int = 3,
    min_n: int = 12,
    annotate: int = 6,
) -> style.ChartResult:
    """D15 - split each segment's lost time into the predictable part and the variable part.

    The distinction is the whole point, because the two have opposite remedies:

    - **systematic** (x) - the median gap between observed and scheduled running time, taken
      across days. A segment that is reliably 40 s slower than its timetable does not need
      infrastructure, it needs the timetable corrected.
    - **stochastic** (y) - the interquartile range of that same gap. A segment whose loss
      swings by minutes from run to run cannot be timetabled away; that is a bus lane, signal
      priority, or a junction problem.

    Quadrants are drawn and named, so the chart carries its own recommendation rather than
    leaving the reader to infer one. Needs several days: with one day, "across days" is not a
    measurement, so the function refuses rather than quietly reporting a single day's noise.

    The *annotate* most extreme segments are named on the plot. Only the extremes: a scatter
    with a label on every point is unreadable, and the points anyone acts on are the ones far
    from the crowd - which is also the definition used to pick them (see `_outlier_order`).
    """
    frame = pd.concat(tables, ignore_index=True)
    subset = tidy.usable_segments(frame, routes=routes, direction=direction)
    subset = subset[subset.sched_seg_time_s.notna()]
    if subset.empty:
        raise ValueError("no usable segments with a scheduled duration")

    days = subset.service_date.nunique()
    if days < min_days:
        raise ValueError(
            f"D15 separates a persistent offset from run-to-run variability, which needs "
            f"several days; got {days}. Extract more city-days and pass them all with "
            f"repeated --table."
        )

    subset["padding_s"] = subset.seg_time_s - subset.sched_seg_time_s
    # city is part of the key: without it, route "11" in Łódź and route "11" in Rome would
    # be averaged into one point, which looks like a segment and is two.
    key = ["city", "route_short_name", "direction_id", "stop_sequence"]
    stats = (
        subset.groupby(key, sort=True)
        .agg(
            n=("padding_s", "count"),
            days=("service_date", "nunique"),
            systematic_s=("padding_s", "median"),
            p25=("padding_s", lambda s: s.quantile(0.25)),
            p75=("padding_s", lambda s: s.quantile(0.75)),
            sched_seg_time_s=("sched_seg_time_s", "median"),
            # Carried so an annotated point can name a place rather than a number. The key is
            # (city, route, direction, stop_sequence), so both names are constant within a group.
            from_stop_name=("from_stop_name", "first"),
            stop_name=("stop_name", "first"),
        )
        .reset_index()
    )
    stats["stochastic_s"] = stats.p75 - stats.p25
    stats = stats[(stats.n >= min_n) & (stats.days >= min_days)]
    if stats.empty:
        raise ValueError(f"no segment reached min_n={min_n} across {min_days}+ days")

    stats["systematic_min"] = style.to_minutes(stats.systematic_s)
    stats["stochastic_min"] = style.to_minutes(stats.stochastic_s)

    x_split, y_split = 0.0, float(stats.stochastic_min.median())
    fig, ax = style.new_figure(width=10.0, height=7.5)
    ax.scatter(stats.systematic_min, stats.stochastic_min, s=18, alpha=0.65,
               color="#0072B2", edgecolor="none")
    ax.axvline(x_split, color="black", linewidth=0.9, alpha=0.6)
    ax.axhline(y_split, color="black", linewidth=0.9, alpha=0.6, linestyle="--")

    # Widen before placing the corner labels: at the default limits the two right-hand ones
    # ran off the canvas, which is the one place a chart must not economise - a quadrant
    # diagram whose quadrant names are cut off explains nothing.
    ax.margins(x=0.12, y=0.10)
    x_low, x_high = ax.get_xlim()
    y_low, y_high = ax.get_ylim()
    # Corner labels carry two different kinds of statement and must not read as one sentence:
    # what the quadrant IS (an observation, from the data) and what it NEEDS (a recommendation,
    # from us). Weight and colour separate them, and both are inset from the corner so neither
    # touches the frame.
    pad_x = (x_high - x_low) * 0.02
    pad_y = (y_high - y_low) * 0.03
    quadrants = [
        (x_high - pad_x, y_high - pad_y, "slow AND erratic", "bus lane + retime", "right", "top"),
        (x_low + pad_x, y_high - pad_y, "fast but erratic", "infrastructure, not timetable",
         "left", "top"),
        (x_high - pad_x, y_low + pad_y, "reliably slow", "retime the schedule", "right", "bottom"),
        (x_low + pad_x, y_low + pad_y, "healthy", "leave alone", "left", "bottom"),
    ]
    line_gap = (y_high - y_low) * 0.045
    for x, y, observation, recommendation, ha, va in quadrants:
        top_y = y if va == "top" else y + line_gap
        ax.text(x, top_y, observation, fontsize=8.5, color="#333333", ha=ha, va=va,
                fontweight="bold")
        ax.text(x, top_y - line_gap, recommendation, fontsize=8, color="#8A8A8A", ha=ha, va=va,
                fontstyle="italic")

    stats["annotated"] = False
    labelled = _annotate_outliers(ax, stats, annotate, (x_low, x_high), (y_low, y_high))
    stats.loc[labelled, "annotated"] = True

    ax.set_xlabel("systematic: median observed minus scheduled (min)")
    ax.set_ylabel("stochastic: interquartile range of the same (min)")
    scope = ", ".join(sorted(routes)) if routes else "all extracted routes"
    scope = f"route {scope}" if routes else scope
    ax.set_title(f"D15 · what each segment needs, {scope} ({days} days)")

    notes = [
        f"{len(stats)} segments over {days} service days; each needs >={min_n} observations",
        (
            f"the {len(labelled)} segments furthest from the middle of the cloud are named as "
            "route, direction and the stop the segment ends at (robust distance, both axes "
            "scaled by their IQR); the sidecar CSV flags them in `annotated` and names every "
            "other point in full"
            if len(labelled)
            else "point labels off (--annotate 0); the sidecar CSV names every segment"
        ),
        "horizontal split at the median segment, so 'erratic' is relative to this network, "
        "not to an absolute standard",
        "only segments passing FA-13/FA-18/FA-20; a cancelled trip is invisible to both axes",
    ]
    style.caption(ax, notes)
    return style.save(
        fig, stats, style.chart_params("D15", sources, len(frame),
                            {"routes": routes, "direction": direction, "days": days,
                             "min_days": min_days, "min_n": min_n, "annotate": annotate,
                             "stochastic_split_min": y_split}, notes),
        out_prefix,
    )


_LABEL_MAX_CHARS = 34


def _segment_annotation(row) -> str:
    """`11 d1 23: Kosciuszki-Mickiewicza` - route, direction, and where the segment ends.

    The direction is in there because it has to be: this chart keys on
    (city, route, direction, stop_sequence), so two points can share a stop_sequence and a
    label without it names both of them.

    Only the segment's END stop, matching the "segment ending at stop sequence" convention D14
    and D17 use on their axes. Naming both ends is the honest form and it does not fit - at the
    width a scatter label can have, `A -> B` truncates away exactly the half that identifies the
    place. The sidecar CSV carries both names in full.
    """
    name = (
        f"{row.route_short_name} d{row.direction_id} {int(row.stop_sequence)}: {row.stop_name}"
    )
    return name if len(name) <= _LABEL_MAX_CHARS else name[: _LABEL_MAX_CHARS - 1] + "…"


def _outlier_order(x: pd.Series, y: pd.Series) -> list:
    """Index labels ordered by how far each point sits from the middle of the cloud.

    Distance is measured after scaling each axis by its own IQR rather than its standard
    deviation. The whole point of this chart is that a few segments are wildly worse than the
    rest, and those segments are in the standard deviation - so using it would let the outliers
    define the scale that is meant to find them. The median and IQR do not move.
    """
    def scale(series: pd.Series) -> pd.Series:
        deviation = series - float(series.median())
        spread = float(series.quantile(0.75) - series.quantile(0.25))
        if not spread > 0:
            # A zero IQR is not a flat axis, it is the case this chart cares about most: every
            # segment steady except one. Falling back to zero here would delete that segment
            # from the ranking, so the largest deviation becomes the unit instead.
            spread = float(deviation.abs().max())
        if not spread > 0:  # genuinely identical everywhere; the axis carries no information
            return pd.Series(0.0, index=series.index)
        return deviation / spread

    distance = (scale(x) ** 2 + scale(y) ** 2) ** 0.5
    return list(distance.sort_values(ascending=False).index)


def _annotate_outliers(ax, stats: pd.DataFrame, annotate: int, xlim, ylim) -> list:
    """Name the *annotate* most extreme points, skipping any that would overprint another.

    Labels are placed **towards** the middle of the cloud, not away from it. Outward was the
    obvious choice and the wrong one: the extreme points sit in the corners, which is exactly
    where the quadrant captions live, so outward labels landed on top of them or ran off the
    canvas.
    """
    if annotate <= 0 or stats.empty:
        return []

    x_low, x_high = xlim
    y_low, y_high = ylim
    gap_x, gap_y = (x_high - x_low) * 0.05, (y_high - y_low) * 0.05
    pad_x, pad_y = (x_high - x_low) * 0.012, (y_high - y_low) * 0.015
    centre_x = float(stats.systematic_min.median())
    centre_y = float(stats.stochastic_min.median())

    placed: list[tuple[float, float]] = []
    labelled: list = []
    for index in _outlier_order(stats.systematic_min, stats.stochastic_min):
        if len(labelled) >= annotate:
            break
        row = stats.loc[index]
        x, y = float(row.systematic_min), float(row.stochastic_min)
        if any(abs(x - px) < gap_x and abs(y - py) < gap_y for px, py in placed):
            continue
        # Both offsets point back at the cloud, so the text always runs inwards from the point.
        label_left = x > centre_x
        label_below = y > centre_y
        ax.annotate(
            _segment_annotation(row),
            xy=(x, y),
            xytext=(x - pad_x if label_left else x + pad_x,
                    y - pad_y if label_below else y + pad_y),
            fontsize=7.0, color="#333333",
            ha="right" if label_left else "left",
            va="top" if label_below else "bottom",
        )
        placed.append((x, y))
        labelled.append(index)

    if labelled:
        marked = stats.loc[labelled]
        ax.scatter(marked.systematic_min, marked.stochastic_min, s=34, facecolor="none",
                   edgecolor="#0072B2", linewidth=1.1, zorder=3)
    return labelled
