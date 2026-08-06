"""A2 - every trip of a route drawn individually, with no aggregation at all.

The argument for this chart: a median and a spread are summaries a reader has to trust, while
a hundred overlaid trajectories are the thing itself. Someone looking at it estimates the
typical journey and its variability without being told either, and can see immediately whether
the spread is a few consistently slow runs or genuine noise on every one.

Split into `_prepare_a2` (pure pandas, no matplotlib) and `spaghetti` (drawing) so a second
renderer such as render/social.py can reuse the exact same prepared numbers.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from transit_charts import tidy
from transit_charts.render import style


@dataclass(frozen=True)
class A2Run:
    """One trip run's plotted line, plus the marker rule the drawing loop needs."""

    line: pd.Series   # index = the run's full contiguous stop_sequence range, values = elapsed
                       # minutes, NaN where a stop was not observed (so the pen lifts, not draws)
    thin: bool         # True when the run has fewer than 4 observed stops - draws a dot marker


@dataclass(frozen=True)
class A2Data:
    out: pd.DataFrame            # -> sidecar CSV: stop_sequence, observed_median_min,
                                  #    scheduled_min?, n
    subset: pd.DataFrame         # anchored, filtered
    notes: list[str]
    runs: list[A2Run]
    median: pd.Series             # index=stop_sequence
    schedule: pd.Series | None    # index=stop_sequence
    route: str
    direction: str
    headsign: str
    min_trip_coverage: float
    show_schedule: bool
    anchor_stop: int
    dropped: int
    trip_count: int               # == len(runs)


def _prepare_a2(
    table: pd.DataFrame,
    route: str,
    direction: str | None,
    min_trip_coverage: float,
    show_schedule: bool,
) -> A2Data:
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

    runs = []
    for _key, run in subset.groupby(["trip_id", "recording_date"], dropna=False, sort=False):
        run = run.sort_values("stop_sequence")
        # Break the line across skipped stops without losing the points either side. Reindexing
        # onto the full stop range inserts NaN BETWEEN the points, which is what matplotlib needs
        # to lift the pen - see the module-level history note this replaced in an older version
        # that set elapsed_min = NaN on the row AFTER each gap, deleting an observation instead.
        full = range(int(run.stop_sequence.min()), int(run.stop_sequence.max()) + 1)
        line = run.set_index("stop_sequence").elapsed_min.reindex(full)
        runs.append(A2Run(line=line, thin=len(run) < 4))

    median = subset.groupby("stop_sequence").elapsed_min.median()

    schedule = None
    if show_schedule:
        schedule = _scheduled_elapsed(subset)

    out = median.rename("observed_median_min").reset_index()
    if schedule is not None:
        out = out.merge(schedule.rename("scheduled_min").reset_index(), on="stop_sequence",
                        how="left")
    counts = subset.groupby("stop_sequence").size().rename("n").reset_index()
    out = out.merge(counts, on="stop_sequence", how="left")

    trip_count = len(runs)
    notes = [
        style.window_note(subset),
        f"{trip_count} trip runs, one faint line each; gaps in a line are unobserved stops, "
        "never interpolated across",
        f"all runs anchored at stop {anchor_stop}, the most common first observed stop - "
        f"{dropped} run(s) that never reached it were dropped so the curves stay comparable",
        f"first stop excluded (FA-20); trips with <{min_trip_coverage:.0%} of stops observed "
        "excluded, so the visible spread is not just clipped runs",
    ]
    return A2Data(
        out=out, subset=subset, notes=notes, runs=runs, median=median, schedule=schedule,
        route=route, direction=direction, headsign=headsign, min_trip_coverage=min_trip_coverage,
        show_schedule=show_schedule, anchor_stop=anchor_stop, dropped=dropped,
        trip_count=trip_count,
    )


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
    """Elapsed journey time against stop sequence, one faint line per trip run."""
    data = _prepare_a2(table, route, direction, min_trip_coverage, show_schedule)

    fig, ax = style.new_figure(height=6.5)
    for run in data.runs:
        line = run.line
        ax.plot(line.index, line.to_numpy(dtype=float),
                color="#0072B2", alpha=0.12, linewidth=0.9,
                marker="." if run.thin else "none", markersize=2)

    ax.plot(data.median.index, data.median.values, color="#0072B2", linewidth=2.6,
            label="observed median")

    if data.schedule is not None:
        ax.plot(data.schedule.index, data.schedule.values, color="#D55E00", linewidth=2.0,
                linestyle="--", label="scheduled")

    ax.set_xlabel("stop sequence")
    ax.set_ylabel(f"minutes since stop {data.anchor_stop}")
    ax.set_title(
        "A2 · every run of "
        + tidy.route_direction_title(data.route, data.direction, data.headsign)
        + f", {data.trip_count} trips"
    )
    ax.legend(loc="upper left", fontsize=9)

    style.caption(ax, data.notes)
    return style.save(
        fig, data.out,
        style.chart_params(
            "A2", source, len(table),
            {"route": data.route, "direction": data.direction, "headsign": data.headsign,
             "min_trip_coverage": min_trip_coverage, "trips": data.trip_count,
             "anchor_stop_sequence": int(data.anchor_stop),
             "runs_dropped_missing_anchor": data.dropped},
            data.notes,
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
