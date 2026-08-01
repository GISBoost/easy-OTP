"""A2 - every trip of a route drawn individually, with no aggregation at all.

The argument for this chart: a median and a spread are summaries a reader has to trust, while
a hundred overlaid trajectories are the thing itself. Someone looking at it estimates the
typical journey and its variability without being told either, and can see immediately whether
the spread is a few consistently slow runs or genuine noise on every one.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from transit_charts import tidy
from transit_charts.render import style


def spaghetti(
    table: pd.DataFrame,
    *,
    out_prefix: Path,
    source,
    route: str,
    direction: str | None = None,
    min_trip_coverage: float = 0.6,
    show_schedule: bool = True,
) -> style.ChartResult:
    """Elapsed journey time against stop sequence, one faint line per trip run.

    Gaps are drawn as gaps. A trip missing its middle stops is *not* interpolated across,
    because a straight line through a hole is indistinguishable from an observed fast run and
    would quietly invent the very smoothness this chart exists to avoid.
    """
    subset = tidy.observed(table, routes=[route], min_trip_coverage=min_trip_coverage)
    direction = direction if direction is not None else tidy.busiest_direction(subset)
    subset = subset[subset.direction_id.astype(str) == str(direction)]
    if subset.empty:
        raise ValueError(f"no observations for route {route!r} direction {direction!r}")
    headsign = tidy.direction_label(subset)

    subset = subset.sort_values(["trip_id", "recording_date", "stop_sequence"])
    subset, anchor_stop, dropped = _anchor_on_common_stop(subset)
    if subset.empty:
        raise ValueError(f"no trip of route {route!r} direction {direction!r} reaches a common "
                         "anchor stop - try lowering --min-trip-coverage")

    fig, ax = style.new_figure(height=6.5)
    trip_count = 0
    for _key, run in subset.groupby(["trip_id", "recording_date"], dropna=False, sort=False):
        run = run.sort_values("stop_sequence")
        # Break the line across skipped stops without losing the points either side. The first
        # version set elapsed_min = NaN on the row AFTER each gap, which deleted that
        # observation rather than the edge leading to it - so a run observed at every other
        # stop (diff == 2 throughout, and perfectly legal under --min-trip-coverage 0.6) became
        # all-NaN and vanished from the figure while still being counted in trip_count and in
        # the caption. Reindexing onto the full stop range inserts NaN BETWEEN the points, which
        # is what matplotlib needs to lift the pen.
        full = range(int(run.stop_sequence.min()), int(run.stop_sequence.max()) + 1)
        line = run.set_index("stop_sequence").elapsed_min.reindex(full)
        ax.plot(line.index, line.to_numpy(dtype=float),
                color="#0072B2", alpha=0.12, linewidth=0.9,
                marker="." if len(run) < 4 else "none", markersize=2)
        trip_count += 1

    median = subset.groupby("stop_sequence").elapsed_min.median()
    ax.plot(median.index, median.values, color="#0072B2", linewidth=2.6, label="observed median")

    schedule = None
    if show_schedule:
        schedule = _scheduled_elapsed(subset)
        if schedule is not None:
            ax.plot(schedule.index, schedule.values, color="#D55E00", linewidth=2.0,
                    linestyle="--", label="scheduled")

    ax.set_xlabel("stop sequence")
    ax.set_ylabel(f"minutes since stop {anchor_stop}")
    ax.set_title(
        "A2 · every run of "
        + tidy.route_direction_title(route, direction, headsign)
        + f", {trip_count} trips"
    )
    ax.legend(loc="upper left", fontsize=9)

    out = median.rename("observed_median_min").reset_index()
    if schedule is not None:
        out = out.merge(schedule.rename("scheduled_min").reset_index(), on="stop_sequence",
                        how="left")
    counts = subset.groupby("stop_sequence").size().rename("n").reset_index()
    out = out.merge(counts, on="stop_sequence", how="left")

    notes = [
        style.window_note(subset),
        f"{trip_count} trip runs, one faint line each; gaps in a line are unobserved stops, "
        "never interpolated across",
        f"all runs anchored at stop {anchor_stop}, the most common first observed stop - "
        f"{dropped} run(s) that never reached it were dropped so the curves stay comparable",
        f"first stop excluded (FA-20); trips with <{min_trip_coverage:.0%} of stops observed "
        "excluded, so the visible spread is not just clipped runs",
    ]
    style.caption(ax, notes)
    return style.save(
        fig, out,
        style.chart_params(
            "A2", source, len(table),
            {"route": route, "direction": direction, "headsign": headsign,
             "min_trip_coverage": min_trip_coverage, "trips": trip_count,
             "anchor_stop_sequence": int(anchor_stop),
             "runs_dropped_missing_anchor": dropped},
            notes,
        ),
        out_prefix,
    )


def _anchor_on_common_stop(subset: pd.DataFrame) -> tuple[pd.DataFrame, int, int]:
    """Re-anchor every run on one shared stop, and drop the runs that never reach it.

    Without this the y axis is "minutes since each trip's OWN first observed stop", and a run
    clipped by the recording window starts its clock halfway along the route. Drawn together,
    such a run looks like a spectacularly fast journey rather than a partially observed one -
    a false outlier, and precisely the kind the eye is drawn to. Measured on Łódź route 11 it
    produced one line reaching stop 38 in 40 minutes against a real median of 57.

    The anchor is the most common first-observed stop, so the majority keeps its full extent.
    """
    key_cols = ["trip_id", "recording_date"]
    first_stops = subset.groupby(key_cols, dropna=False)["stop_sequence"].min()
    anchor_stop = int(first_stops.mode().iloc[0])

    at_anchor = (
        subset.loc[subset.stop_sequence == anchor_stop, key_cols + ["obs_time"]]
        .rename(columns={"obs_time": "anchor_time"})
        .drop_duplicates(subset=key_cols)
    )
    kept = subset.merge(at_anchor, on=key_cols, how="inner")
    kept["elapsed_min"] = (kept.obs_time - kept.anchor_time).dt.total_seconds() / 60.0
    dropped = int(len(first_stops) - len(at_anchor))
    return kept[kept.stop_sequence >= anchor_stop].copy(), anchor_stop, dropped


def _scheduled_elapsed(subset: pd.DataFrame) -> pd.Series | None:
    """Scheduled minutes from each trip's first *observed* stop, medianed over trips.

    Anchored on the first observed stop rather than the trip's true origin, so the dashed line
    is comparable with the observed ones on the same axes. Returns None when the schedule is
    unusable (no resolved service date), rather than drawing something meaningless.
    """
    if subset.sched_arr.isna().all():
        return None
    frame = subset.dropna(subset=["sched_arr"]).copy()
    first = frame.groupby(["trip_id", "recording_date"], dropna=False)["sched_arr"].transform("min")
    frame["sched_elapsed_min"] = (frame.sched_arr - first).dt.total_seconds() / 60.0
    return frame.groupby("stop_sequence").sched_elapsed_min.median()
