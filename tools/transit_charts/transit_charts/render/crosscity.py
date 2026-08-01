"""E20 - the terminus-layover artifact profile, side by side across cities.

Twelve cities recorded the same way is unusual, and this chart is what that buys: the shape of
the delay profile over a trip's first few stops, compared across feeds. FA-17 was calibrated on
one city (Gdańsk) and generalised a rule from it that turned out not to hold; FA-20 replaced
that rule after measuring nine. This chart is that measurement, rebuilt from the shipped
pipeline rather than from one-off scripts, so it can be re-run whenever a city is added.

**This is the one chart that deliberately keeps each trip's first stop.** Everywhere else the
first stop is dropped, because the vehicle's layover on its origin terminus lands there. Here
the size of that contamination IS the subject, so `include_first_stop=True` is not a loosened
filter - it is the measurement.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from transit_charts import tidy
from transit_charts.render import style

# Where "the artifact" stops and "the trip" begins. The first few increments are reported
# individually because that is where they differ; past this the profile is flat enough that a
# single steady-state number describes it.
EARLY_STOPS = (1, 2, 3, 4)
STEADY_RANGE = (5, 20)


def group_by_city(tables: list[pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Group loaded tidy tables by the `city` column they carry.

    Reads the data, not the filename. Deriving the label from `path.stem.split("_")[0]` meant
    two tables of the same city silently overwrote each other in a dict comprehension - one
    city-day vanishing with no message - and a file named `2026-07-21_lodz.csv.gz` produced a
    city called "2026-07-21". Several tables of one city are concatenated, which is what a
    multi-day per-city profile needs.
    """
    grouped: dict[str, list[pd.DataFrame]] = {}
    for frame in tables:
        if frame.empty:
            continue
        for city, part in frame.groupby("city", dropna=False, sort=True):
            grouped.setdefault(str(city), []).append(part)
    return {
        city: parts[0] if len(parts) == 1 else pd.concat(parts, ignore_index=True)
        for city, parts in grouped.items()
    }


def artifact_profile(
    tables: dict[str, pd.DataFrame],
    *,
    out_prefix: Path,
    sources: list[Path],
    min_n: int = 200,
) -> style.ChartResult:
    """Per-city delay increment between consecutive early stops, plus the steady state.

    *tables* maps a city label to its tidy table. Each city contributes one row of increments:
    stop 1→2, 2→3, 3→4, and the median increment over stops 5–20.

    Reading it: a city whose 1→2 bar towers over its own 2→3 bar has the layover artifact; one
    whose bars are all the same height does not. Łódź and Poznań were measured at +0.1 s and
    +0.2 s on that first increment while Boston was at +62.8 s - and both are `sequence`-signal
    feeds, which is exactly why FA-17's signal condition had to go.
    """
    rows = []
    for city in sorted(tables):
        observed = tidy.observed(
            tables[city], include_first_stop=True, min_trip_coverage=None
        )
        if observed.empty:
            continue
        profile = (
            observed.groupby("stop_sequence")
            .agg(n=("delay_s", "count"), delay_s=("delay_s", "median"))
            .sort_index()
        )
        usable = profile[profile.n >= min_n]
        if len(usable) < STEADY_RANGE[0] + 1:
            continue

        record = {"city": city, "stops_used": int(len(usable)), "n_total": int(profile.n.sum())}
        for low, high in zip(EARLY_STOPS, EARLY_STOPS[1:]):
            record[f"inc_{low}_{high}_s"] = _increment(usable, low, high)
        steady = [
            _increment(usable, seq, seq + 1)
            for seq in range(STEADY_RANGE[0], STEADY_RANGE[1])
        ]
        steady = [value for value in steady if value == value]  # drop NaN
        record["steady_s"] = float(pd.Series(steady).median()) if steady else float("nan")
        rows.append(record)

    if not rows:
        raise ValueError(
            f"no city had a stop profile with at least {min_n} observations per stop - "
            "extract without a --route filter so every route contributes"
        )

    stats = pd.DataFrame(rows).sort_values("inc_1_2_s", ascending=False).reset_index(drop=True)
    series = [
        ("inc_1_2_s", "stop 1 to 2", "#D55E00"),
        ("inc_2_3_s", "stop 2 to 3", "#E69F00"),
        ("inc_3_4_s", "stop 3 to 4", "#56B4E9"),
        ("steady_s", f"steady state ({STEADY_RANGE[0]}-{STEADY_RANGE[1]})", "#009E73"),
    ]

    # Two panels sharing the city axis, NOT one. Rome's first increment is +515 s while every
    # later one is within +/-17 s, so on a single axis the other three series collapse into a
    # flat line at zero and the chart shows only what was already obvious. Deleting the 1-to-2
    # series would fix the readability by removing the measurement, which is worse - so it gets
    # its own scale instead, and the panels stay stacked so a city's bars line up vertically.
    fig, (ax_first, ax_rest) = style.plt.subplots(
        2, 1, figsize=(max(9.0, 1.5 * len(stats)), 7.5), sharex=True,
        gridspec_kw={"height_ratios": [1, 1.4]},
    )
    positions = range(len(stats))

    ax_first.bar(positions, stats[series[0][0]], width=0.55, color=series[0][2],
                 label=series[0][1])
    ax_first.axhline(0, color="black", linewidth=0.9, alpha=0.6)
    ax_first.set_ylabel("stop 1 to 2 (s)")
    ax_first.grid(True, **style.GRID_KW)
    ax_first.set_axisbelow(True)
    ax_first.set_title("E20 · terminus-layover artifact profile, by city")
    for index, value in enumerate(stats[series[0][0]]):
        if value == value:
            ax_first.text(index, value, f" {value:+.0f} ", ha="center", fontsize=8,
                          va="bottom" if value >= 0 else "top")

    width = 0.26
    for index, (column, label, colour) in enumerate(series[1:]):
        offsets = [i + (index - 1) * width for i in positions]
        ax_rest.bar(offsets, stats[column], width=width, label=label, color=colour)
    ax_rest.axhline(0, color="black", linewidth=0.9, alpha=0.6)
    ax_rest.set_ylabel("later increments (s)")
    ax_rest.grid(True, **style.GRID_KW)
    ax_rest.set_axisbelow(True)
    ax_rest.set_xticks(list(positions))
    ax_rest.set_xticklabels(stats.city)
    ax_rest.legend(fontsize=8.5, ncols=3, loc="upper right")

    notes = [
        f"{len(stats)} cities, whole-feed; each stop needs >={min_n} observations. "
        "TWO SCALES - the first increment is up to 50x the others, so compare within a panel.",
        "the only chart here that keeps each trip's first stop: that increment IS the artifact "
        "FA-20 removes, so measuring it means not filtering it",
        "a tall top bar beside small bottom bars is the signature; Lodz has neither",
    ]
    style.caption(ax_rest, notes)
    return style.save(
        fig, stats,
        style.chart_params(
            "E20", sources, int(sum(len(t) for t in tables.values())),
            {"cities": list(stats.city), "min_n": min_n,
             "early_stops": list(EARLY_STOPS), "steady_range": list(STEADY_RANGE)},
            notes,
        ),
        out_prefix, rect=(0, 0.13, 1, 1),
    )


def _increment(profile: pd.DataFrame, low: int, high: int) -> float:
    """Median delay at *high* minus at *low*, or NaN when either stop is missing.

    NaN rather than a substitute: a city whose stop 3 fell below the observation floor has no
    2-to-3 increment, and inventing one from stop 4 would silently compare different things
    across cities - which is precisely the error FA-17 made by generalising from one city.
    """
    if low not in profile.index or high not in profile.index:
        return float("nan")
    return float(profile.loc[high, "delay_s"] - profile.loc[low, "delay_s"])
