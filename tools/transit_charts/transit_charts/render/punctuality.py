"""C9, C10, C11 - punctuality, always as a distribution and never as a bare mean.

The shared premise: a median delay of +60 s means something very different when the spread is
±20 s than when it is ±300 s, and the passenger experiences the spread. Every chart here shows
it, and every one carries its own `n`.

Each chart is split into a `_prepare_*` function (pure pandas, no matplotlib - this is what a
second renderer such as render/social.py reuses to guarantee the same sidecar CSV) and a public
drawing function that turns the prepared `*Data` into a figure.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from transit_charts import tidy
from transit_charts.render import html as html_mod
from transit_charts.render import style

# Default punctuality classes, in seconds of delay. Roughly the bands most European operators
# report against; configurable because "on time" is a policy choice, not a fact.
DEFAULT_BANDS = (
    ("early", float("-inf"), -60.0, "#56B4E9"),
    ("on time", -60.0, 180.0, "#009E73"),
    ("late", 180.0, 600.0, "#E69F00"),
    ("very late", 600.0, float("inf"), "#D55E00"),
)

# Sidecar key for C11's pooled panel. A literal rather than a blank or a route name, so a
# reader filtering the CSV by route cannot pick it up by accident and count it twice.
POOLED_KEY = "ALL"


@dataclass(frozen=True)
class C9Data:
    """Everything `dot_and_whisker` needs to draw, computed with zero matplotlib calls."""

    stats: pd.DataFrame          # -> sidecar CSV: stop_sequence, n, p10..p90_min, below_min_n,
                                  #    stop_name
    subset: pd.DataFrame         # filtered to route + resolved direction; for style.window_note
    notes: list[str]
    route: str
    direction: str                # resolved, never None past this point
    headsign: str
    min_n: int
    min_trip_coverage: float


@dataclass(frozen=True)
class C10Panel:
    """One route's data for `percentile_fan`, split into what each drawing call needs."""

    key: str
    part: pd.DataFrame    # per-route stats, sorted by bucket, NOT grid-filled - feeds
                           # mark_thin_buckets exactly as the un-refactored code did
    solid: pd.DataFrame   # grid-filled onto style.on_full_bucket_grid with below-min-n blanked -
                           # feeds fill_between/plot


@dataclass(frozen=True)
class C10Data:
    stats: pd.DataFrame          # -> sidecar CSV: route_short_name, bucket, p*_min, n, below_min_n
    subset: pd.DataFrame
    notes: list[str]
    keys: list[str]               # sorted route_short_name list (== options["routes"] today)
    bucket_minutes: int
    min_n: int
    panels: list[C10Panel]


@dataclass(frozen=True)
class C11Panel:
    """One panel's data for `punctuality_bands` - draw does zero pandas work from these."""

    key: str
    shares: pd.DataFrame   # index=bucket, columns=band_names, values are shares in [0, 1]
    totals: pd.Series      # index=bucket, n per bucket
    block: pd.DataFrame    # this panel's rows of the sidecar CSV (route_short_name, bucket,
                            # band columns, n, below_min_n)


@dataclass(frozen=True)
class C11Data:
    out: pd.DataFrame            # -> sidecar CSV, concatenation of every panel's block, in CSV
                                  #    row order (pooled panel FIRST when combine - see
                                  #    render/social.py for why draw order must not follow this)
    subset: pd.DataFrame
    notes: list[str]
    keys: list[str]
    band_names: list[str]
    bands: tuple
    combine: bool                 # post-adjusted (combine and len(keys) > 1)
    bucket_minutes: int
    min_n: int
    panels: list[C11Panel]         # same order as `out`'s row blocks


def _prepare_c9(
    table: pd.DataFrame,
    route: str,
    direction: str | None,
    min_n: int,
    min_trip_coverage: float,
) -> C9Data:
    """C9 - delay distribution at each stop along one route direction.

    One route, one direction, by construction: `stop_sequence` 5 is a different physical place
    in each direction and on each route, so overlaying them would average unrelated places into
    a convincing-looking line. Takes a single route and picks the busiest direction unless told
    otherwise, reporting which.
    """
    subset = tidy.observed(table, routes=[route], min_trip_coverage=min_trip_coverage)
    direction = direction if direction is not None else tidy.busiest_direction(subset)
    subset = subset[subset.direction_id.astype(str) == str(direction)]
    if subset.empty:
        raise ValueError(f"no observations for route {route!r} direction {direction!r}")
    headsign = tidy.direction_label(subset)

    stats = _to_minutes(tidy.summarise(subset, ["stop_sequence"], "delay_s", min_n=min_n))
    names = (
        subset.drop_duplicates("stop_sequence").set_index("stop_sequence").stop_name.to_dict()
    )
    stats["stop_name"] = stats.stop_sequence.map(names)

    notes = [
        style.window_note(subset),
        f"n per stop {int(stats.n.min())}-{int(stats.n.max())}; bar is p25-p75, and the "
        "sidecar CSV carries n plus p10/p90 for the tail",
        f"first stop excluded (FA-20 terminus layover); trips with <{min_trip_coverage:.0%} "
        "of stops observed excluded; coverage misses bias delay optimistic",
    ]
    return C9Data(
        stats=stats, subset=subset, notes=notes, route=route, direction=direction,
        headsign=headsign, min_n=min_n, min_trip_coverage=min_trip_coverage,
    )


def dot_and_whisker(
    table: pd.DataFrame,
    *,
    out_prefix: Path,
    source,
    route: str,
    direction: str | None = None,
    min_n: int = 20,
    min_trip_coverage: float = 0.6,
    interactive: bool = False,
) -> style.ChartResult:
    """C9 - delay distribution at each stop along one route direction."""
    data = _prepare_c9(table, route, direction, min_n, min_trip_coverage)
    stats = data.stats

    fig, ax = style.new_figure(width=max(10.0, 0.38 * len(stats)), height=6.0)
    solid = stats[~stats.below_min_n]
    # p25-p75 only. The p10-p90 band that used to sit behind it made the chart read as two
    # nested blocks of colour, and the outer decile is not a quantity anyone acts on - it stays
    # in the sidecar CSV for whoever wants the tail.
    ax.vlines(solid.stop_sequence, solid.p25_min, solid.p75_min, color="#0072B2", linewidth=5,
              label="p25-p75")
    # White fill, dark edge: the median used to be the same blue as the bar it sits inside, so
    # the one point every reader looks for was invisible against its own band.
    ax.plot(solid.stop_sequence, solid.p50_min, "o", markerfacecolor="white",
            markeredgecolor="#083D5C", markeredgewidth=1.1, markersize=5.5, linestyle="none",
            zorder=3, label="median")
    ax.axhline(0, color="black", linewidth=0.9, alpha=0.6)
    style.mark_thin_buckets(ax, stats.stop_sequence.tolist(), stats.below_min_n.tolist())

    ax.set_xlabel("stop sequence")
    ax.set_ylabel("delay vs schedule (minutes)")
    ax.set_title(
        "C9 · delay distribution along "
        + tidy.route_direction_title(data.route, data.direction, data.headsign)
    )
    ax.set_xticks(stats.stop_sequence[:: max(1, len(stats) // 30)])
    ax.legend(handles=[*ax.get_legend_handles_labels()[0], style.thin_legend_handle(min_n)],
              loc="upper left", fontsize=8.5)

    style.caption(ax, data.notes)
    return style.save(
        fig, stats, style.chart_params("C9", source, len(table),
                            {"route": data.route, "direction": data.direction,
                             "headsign": data.headsign, "min_n": min_n,
                             "min_trip_coverage": min_trip_coverage}, data.notes),
        out_prefix,
        html_spec=html_mod.HtmlSpec(
            x="stop_sequence", x_label="stop sequence", y_label="delay (min)",
            series=[("p50_min", "median", "#0072B2")],
            bands=[("p25_min", "p75_min", "#0072B2")],
        ) if interactive else None,
    )


def _prepare_c10(
    table: pd.DataFrame,
    routes: list[str] | None,
    bucket_minutes: int,
    min_n: int,
) -> C10Data:
    """C10 - delay percentiles across the day, one panel per route.

    Faceted rather than overlaid: two routes' bands drawn on one axes obscure each other
    exactly where they are most interesting. Sharing the x axis keeps them comparable.
    """
    subset = tidy.observed(table, routes=routes)
    if subset.empty:
        raise ValueError("no observations after filtering")
    subset["bucket"] = tidy.local_time_bucket(subset.obs_local, bucket_minutes)

    keys = sorted(subset.route_short_name.unique())
    stats = _to_minutes(
        tidy.summarise(subset, ["route_short_name", "bucket"], "delay_s", min_n=min_n)
    )

    panels = []
    for key in keys:
        part = stats[stats.route_short_name == key].sort_values("bucket")
        # Blank the suppressed buckets in place, then lay the result on the full grid, so the
        # line lifts over them rather than bridging them with an invented value.
        solid = part.copy()
        solid.loc[solid.below_min_n, [c for c in solid.columns if c.endswith("_min")]] = float("nan")
        solid = style.on_full_bucket_grid(solid, "bucket", bucket_minutes)
        panels.append(C10Panel(key=key, part=part, solid=solid))

    notes = [
        style.window_note(subset),
        "one panel per route; band is p25-p75, and the sidecar CSV carries n plus p10/p90",
        "first stop excluded (FA-20); coverage misses bias delay optimistic",
    ]
    return C10Data(
        stats=stats, subset=subset, notes=notes, keys=keys, bucket_minutes=bucket_minutes,
        min_n=min_n, panels=panels,
    )


def percentile_fan(
    table: pd.DataFrame,
    *,
    out_prefix: Path,
    source,
    routes: list[str] | None = None,
    bucket_minutes: int = 15,
    min_n: int = 20,
    interactive: bool = False,
) -> style.ChartResult:
    """C10 - delay percentiles across the day, one panel per route.

    One band, p25-p75. The outer p10-p90 band this chart used to carry made a three-route
    figure into a wash of overlapping translucency, and the deciles are still in the sidecar
    CSV for anyone reading the tail.
    """
    data = _prepare_c10(table, routes, bucket_minutes, min_n)
    stats = data.stats

    fig, axes = _facet(data.keys, height_each=2.6)
    colours = style.colour_for(data.keys)
    for ax, panel in zip(axes, data.panels):
        solid = panel.solid
        ax.fill_between(solid.bucket, solid.p25_min, solid.p75_min, color=colours[panel.key],
                        alpha=0.32, linewidth=0, label="p25-p75")
        ax.plot(solid.bucket, solid.p50_min, color=colours[panel.key], linewidth=1.8, label="median")
        ax.axhline(0, color="black", linewidth=0.8, alpha=0.5)
        ax.set_ylabel(f"route {panel.key}\ndelay (min)")
        ax.grid(True, **style.GRID_KW)
        style.mark_thin_buckets(ax, panel.part.bucket.tolist(), panel.part.below_min_n.tolist())

    style.time_of_day_axis(axes[-1])
    axes[-1].set_xlabel("local time of day")
    fig.suptitle(f"C10 · delay percentiles through the day ({bucket_minutes}-min buckets)")
    style.facet_legend(
        fig,
        [*axes[0].get_legend_handles_labels()[0], style.thin_legend_handle(min_n)],
        ncols=4,
    )
    style.caption(axes[-1], data.notes)
    return style.save(
        fig, stats, style.chart_params("C10", source, len(table),
                            {"routes": data.keys, "bucket_minutes": bucket_minutes,
                             "min_n": min_n}, data.notes),
        out_prefix, rect=(0, 0.06, 1, 0.92),
        html_spec=html_mod.HtmlSpec(
            x="bucket", x_label="local time of day", y_label="delay (min)",
            series=[("p50_min", "median", "#0072B2")],
            bands=[("p25_min", "p75_min", "#0072B2")],
            group="route_short_name", x_is_time_of_day=True,
        ) if interactive else None,
    )


def _prepare_c11(
    table: pd.DataFrame,
    routes: list[str] | None,
    bucket_minutes: int,
    bands,
    min_n: int,
    combine: bool,
) -> C11Data:
    """C11 - share of observations in each punctuality class, through the day.

    *combine* adds a pooled panel above the per-route ones, keyed `ALL` in the sidecar. Pooled
    over observations rather than averaged over routes, because the two differ and only one of
    them is a network figure: averaging the shares would let a route with ten crossings weigh as
    much as one with four hundred.
    """
    subset = tidy.observed(table, routes=routes)
    if subset.empty:
        raise ValueError("no observations after filtering")
    subset["bucket"] = tidy.local_time_bucket(subset.obs_local, bucket_minutes)
    subset["band"] = _classify(subset.delay_s, bands)

    # One panel per route, like C10. The first version pooled every requested route into a
    # single stacked area and said so nowhere - a share with no stated population, which the
    # first reader immediately (and correctly) asked about.
    keys = sorted(subset.route_short_name.unique())
    band_names = [name for name, _lo, _hi, _c in bands]
    raw_panels = [(key, subset[subset.route_short_name == key]) for key in keys]
    # Pooling one route with itself draws the same panel twice and captions it "all 1 routes".
    # The flag is honoured where it means something and dropped where it does not.
    combine = combine and len(keys) > 1
    if combine:
        raw_panels.insert(0, (POOLED_KEY, subset))

    panels = []
    parts = []
    for key, part in raw_panels:
        counts = part.groupby(["bucket", "band"], observed=True).size().unstack(fill_value=0)
        for name in band_names:
            if name not in counts.columns:
                counts[name] = 0
        counts = counts[band_names].sort_index()
        totals = counts.sum(axis=1)
        shares = counts.div(totals, axis=0)

        block = shares.reset_index().merge(totals.rename("n").reset_index(), on="bucket")
        block.insert(0, "route_short_name", key)
        block["below_min_n"] = block.n < min_n
        parts.append(block)
        panels.append(C11Panel(key=key, shares=shares, totals=totals, block=block))

    out = pd.concat(parts, ignore_index=True)
    band_text = ", ".join(
        f"{name} "
        + ("<" if low == float("-inf") else f"{style.to_minutes(low):+.0f}")
        + ("" if low == float("-inf") or high == float("inf") else "..")
        + ("" if high == float("inf") else f"{style.to_minutes(high):+.0f}")
        + " min"
        for name, low, high, _c in bands
    )
    notes = [
        style.window_note(subset),
        f"bands: {band_text} (a policy choice, not a fact - configurable)",
        (
            f"top panel pools all {len(keys)} routes (sidecar key '{POOLED_KEY}'), pooled over "
            "observations and not averaged over routes - so it almost always clears --min-n "
            "even where a single route does not"
            if combine else "one panel per route; per-bucket n is in the sidecar CSV"
        ),
        "first stop excluded (FA-20); coverage misses bias delay optimistic",
    ]
    return C11Data(
        out=out, subset=subset, notes=notes, keys=keys, band_names=band_names, bands=bands,
        combine=combine, bucket_minutes=bucket_minutes, min_n=min_n, panels=panels,
    )


def punctuality_bands(
    table: pd.DataFrame,
    *,
    out_prefix: Path,
    source,
    routes: list[str] | None = None,
    bucket_minutes: int = 30,
    bands=DEFAULT_BANDS,
    min_n: int = 20,
    combine: bool = False,
) -> style.ChartResult:
    """C11 - share of observations in each punctuality class, through the day.

    The readable-at-a-glance member of the group. Shares rather than counts, so a quiet
    evening is comparable with a busy afternoon - with `n` printed per bucket in the sidecar,
    because a 100% on-time bucket built from four observations is not a result.
    """
    data = _prepare_c11(table, routes, bucket_minutes, bands, min_n, combine)

    fig, axes = _facet([panel.key for panel in data.panels], height_each=2.4)
    for ax, panel in zip(axes, data.panels):
        ax.stackplot(
            panel.shares.index,
            [panel.shares[name] for name in data.band_names],
            labels=data.band_names,
            colors=[colour for _n, _lo, _hi, colour in bands],
            alpha=0.9,
        )
        ax.set_ylim(0, 1)
        ax.set_ylabel(
            f"all {len(data.keys)} routes\nshare" if panel.key == POOLED_KEY
            else f"route {panel.key}\nshare"
        )
        ax.grid(True, **style.GRID_KW)
        style.mark_thin_buckets(ax, list(panel.shares.index), list(panel.totals < min_n))

    style.time_of_day_axis(axes[-1])
    axes[-1].set_xlabel("local time of day")
    fig.suptitle(f"C11 · punctuality mix through the day ({bucket_minutes}-min buckets)")
    style.facet_legend(
        fig,
        [*axes[0].get_legend_handles_labels()[0], style.thin_legend_handle(min_n)],
        ncols=len(bands) + 1,
    )
    style.caption(axes[-1], data.notes)
    return style.save(
        fig, data.out, style.chart_params("C11", source, len(table),
                          {"routes": data.keys, "bucket_minutes": bucket_minutes,
                           "min_n": min_n, "combine": data.combine,
                           "bands_seconds": [[n, lo, hi] for n, lo, hi, _c in bands]}, data.notes),
        out_prefix, rect=(0, 0.06, 1, 0.92),
    )


def _to_minutes(stats: pd.DataFrame) -> pd.DataFrame:
    """Rename percentile columns to minutes, keeping `n` and `below_min_n` untouched.

    The tidy table stays in seconds - that is the unit every family_a threshold uses - and the
    conversion happens once, here, on the way to a reader. The sidecar CSV therefore carries
    exactly what the axis shows, which is the point of shipping it at all.
    """
    out = stats.copy()
    for column in [c for c in out.columns if c.startswith("p") and c[1:].isdigit()]:
        out[f"{column}_min"] = style.to_minutes(out[column])
        out = out.drop(columns=[column])
    return out


def _classify(delay_s: pd.Series, bands) -> pd.Series:
    """Assign each delay to a band. Half-open [lo, hi) so a value never lands in two."""
    out = pd.Series(index=delay_s.index, dtype="object")
    for name, low, high, _colour in bands:
        out = out.mask((delay_s >= low) & (delay_s < high), name)
    return out


def _facet(keys: list[str], height_each: float):
    """One stacked axes per key with a shared x axis; always returns a list of axes."""
    fig, axes = style.plt.subplots(
        len(keys), 1, figsize=(12.0, max(3.0, height_each * len(keys))), sharex=True
    )
    if len(keys) == 1:
        axes = [axes]
    return fig, list(axes)
