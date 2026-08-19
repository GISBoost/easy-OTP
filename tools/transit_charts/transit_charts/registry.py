"""Declarative per-chart contract: which flags apply to which chart, and what kwargs its render
function wants.

This is the shared source of truth for `cli.py`'s dispatch (today) and `tools/chart_lab`'s GUI
(a later milestone, not built yet - see docs/prd/PR_easy-OTP_chart_lab.md). The GUI needs to
decide which widgets to draw for a selected chart from the exact same facts `cli.py` uses to
validate flags and build a render call, so that knowledge lives here once instead of twice.

`REGISTRY` itself is built lazily by `build_registry()`, not at module import time: the render
modules it wires up pull in matplotlib, and this module must stay importable in an environment
that only needs `extract` (no matplotlib), same reason `cli.py`'s own `_cmd_chart` imports them
lazily rather than at module scope.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Callable

import pandas as pd

from transit_charts import sources

# The 16 chart names, kept as a plain literal (no matplotlib import needed) so argparse's
# `choices=` can use it without paying for `build_registry()`.
CHART_NAMES = frozenset({
    "C9", "C10", "C11", "A2", "B5", "B6", "B7", "B8", "D14", "D17",
    "D15", "E20", "H28", "H29", "H30", "J39",
})

# Charts that take a `routes: list[str] | None` selection and so can honour --exclude-route.
# Kept as a plain literal rather than derived from REGISTRY: deriving it would force a
# matplotlib import (via build_registry()) on the --exclude-route-ignored message path, which
# costs nothing today.
EXCLUDE_ROUTE_AWARE_NAMES = frozenset({"C10", "C11", "B6", "D15", "H28", "H29", "H30"})


@dataclass(frozen=True)
class ChartSpec:
    """One chart's full parameter contract - the facts a CLI or a GUI both need."""

    key: str
    label: str
    route_mode: str  # "single" | "multi" | "none"
    exclude_route_aware: bool
    interactive_capable: bool
    multi_day_by_design: bool
    bucket_minutes_default: int | None
    min_n_default: int | None
    min_n_is_grid: bool
    min_trip_coverage_default: float | None
    supports_combine: bool
    supports_annotate: bool
    annotate_default: int
    supports_threshold: bool
    threshold_default: float
    supports_direction: bool
    render: Callable[..., object]
    build_kwargs: Callable[["ResolvedChartInputs"], dict]


@dataclass(frozen=True)
class ResolvedChartInputs:
    """Inputs common to every chart, resolved once per `chart` invocation."""

    args: argparse.Namespace
    table: pd.DataFrame
    tables: list[pd.DataFrame]
    interactive: bool


def _grid_min_n(args: argparse.Namespace) -> dict:
    """min_n for the grid charts, which is a different quantity from the series charts'.

    A time-series bucket pools every stop of a route and reaches n in the hundreds; a single
    segment-hour cell pools one stop pair and is bounded by the vehicles that ran. Deriving one
    from the other by division produced a threshold no cell could reach - so grid charts take
    the chart's own default unless --min-n was passed explicitly.
    """
    return {"min_n": args.min_n} if args.min_n_explicit else {}


def _resolve_route_filter(args: argparse.Namespace, table: pd.DataFrame) -> list[str] | None:
    """--route and --exclude-route composed into one list for the multi-route charts.

    Deliberately chart-level, not extract-level: `--route` in `extract` exists purely for
    speed (a filter BEFORE the interpolation loop), and one whole-feed tidy table today serves
    every per-line and network chart at once. Excluding a route at extract time would make that
    same table unusable for a chart ABOUT the excluded route - a second extraction for "city
    minus X" alongside the one for "just X". The contaminated-line problem this flag answers is
    a chart problem, not an extraction one, so the fix stays here.

    No flags at all -> None ("every route in the table", unchanged behaviour). The base set for
    subtraction is whatever the table(s) actually carry - not the full feed - so excluding a
    route already absent from a route-filtered extraction is reported the same way a typo would
    be, rather than silently doing nothing.
    """
    known = set(table.route_short_name.dropna()) | set(table.route_group.dropna())
    if not args.exclude_route:
        return args.route or None

    if args.route:
        _patterns, base, unmatched = sources.match_route_names(args.route, known)
        if unmatched:
            raise sources.InputError(
                "no route matches " + ", ".join(repr(p) for p in unmatched)
                + f"\n(routes present in this table: {', '.join(sorted(known))})"
            )
    else:
        base = known

    _patterns, excluded, unmatched = sources.match_route_names(args.exclude_route, base)
    if unmatched:
        raise sources.InputError(
            "--exclude-route matches nothing in the working set for "
            + ", ".join(repr(p) for p in unmatched)
            + f"\n(routes available to exclude: {', '.join(sorted(base))})"
        )

    remaining = sorted(base - excluded)
    if not remaining:
        raise ValueError(
            "--exclude-route removed every route from the working set - nothing left to chart"
        )
    return remaining


def build_registry() -> dict[str, ChartSpec]:
    """Construct the full chart registry. Imports the render modules lazily (matplotlib)."""
    from transit_charts.render import crosscity, headway, punctuality, speed, trajectory
    from transit_charts.render import stop_headway as render_stop_headway

    def common(inputs: ResolvedChartInputs) -> dict:
        c = dict(out_prefix=inputs.args.out_prefix, source=inputs.args.table)
        if inputs.interactive:
            c["interactive"] = True
        return c

    def c9_kwargs(inputs: ResolvedChartInputs) -> dict:
        a = inputs.args
        return dict(table=inputs.table, route=a.route[0], direction=a.direction,
                    min_n=a.min_n, min_trip_coverage=a.min_trip_coverage, **common(inputs))

    def c10_kwargs(inputs: ResolvedChartInputs) -> dict:
        a = inputs.args
        return dict(table=inputs.table, routes=_resolve_route_filter(a, inputs.table),
                    min_n=a.min_n, bucket_minutes=a.bucket_minutes or 15, **common(inputs))

    def c11_kwargs(inputs: ResolvedChartInputs) -> dict:
        a = inputs.args
        return dict(table=inputs.table, routes=_resolve_route_filter(a, inputs.table),
                    min_n=a.min_n, bucket_minutes=a.bucket_minutes or 30,
                    combine=a.combine, **common(inputs))

    def a2_kwargs(inputs: ResolvedChartInputs) -> dict:
        a = inputs.args
        return dict(table=inputs.table, route=a.route[0], direction=a.direction,
                    min_trip_coverage=a.min_trip_coverage, **common(inputs))

    def b5_kwargs(inputs: ResolvedChartInputs) -> dict:
        a = inputs.args
        return dict(table=inputs.table, route=a.route[0], direction=a.direction,
                    bucket_minutes=a.bucket_minutes or 60, **_grid_min_n(a), **common(inputs))

    def b6_kwargs(inputs: ResolvedChartInputs) -> dict:
        a = inputs.args
        return dict(table=inputs.table, routes=_resolve_route_filter(a, inputs.table),
                    bucket_minutes=a.bucket_minutes or 60,
                    min_n=max(5, a.min_n // 4), **common(inputs))

    def b7_kwargs(inputs: ResolvedChartInputs) -> dict:
        a = inputs.args
        return dict(table=inputs.table, route=a.route[0], direction=a.direction,
                    bucket_minutes=a.bucket_minutes or 60, min_n=a.min_n, **common(inputs))

    def b8_kwargs(inputs: ResolvedChartInputs) -> dict:
        a = inputs.args
        return dict(table=inputs.table, route=a.route[0], direction=a.direction,
                    bucket_minutes=a.bucket_minutes or 60, threshold=a.threshold,
                    **_grid_min_n(a), **common(inputs))

    def h28_kwargs(inputs: ResolvedChartInputs) -> dict:
        a = inputs.args
        return dict(table=inputs.table, routes=_resolve_route_filter(a, inputs.table),
                    min_n=a.min_n, **common(inputs))

    def h29_kwargs(inputs: ResolvedChartInputs) -> dict:
        a = inputs.args
        return dict(table=inputs.table, routes=_resolve_route_filter(a, inputs.table),
                    min_n=a.min_n, **common(inputs))

    def h30_kwargs(inputs: ResolvedChartInputs) -> dict:
        a = inputs.args
        return dict(table=inputs.table, routes=_resolve_route_filter(a, inputs.table),
                    bucket_minutes=a.bucket_minutes or 60,
                    threshold=a.threshold, **_grid_min_n(a), **common(inputs))

    def d14_kwargs(inputs: ResolvedChartInputs) -> dict:
        a = inputs.args
        return dict(table=inputs.table, route=a.route[0], direction=a.direction,
                    bucket_minutes=a.bucket_minutes or 120, **_grid_min_n(a), **common(inputs))

    def d17_kwargs(inputs: ResolvedChartInputs) -> dict:
        a = inputs.args
        return dict(table=inputs.table, route=a.route[0], direction=a.direction,
                    bucket_minutes=a.bucket_minutes or 120, **_grid_min_n(a), **common(inputs))

    def e20_kwargs(inputs: ResolvedChartInputs) -> dict:
        a = inputs.args
        return dict(tables=crosscity.group_by_city(inputs.tables),
                    out_prefix=a.out_prefix, sources=a.table)

    def j39_kwargs(inputs: ResolvedChartInputs) -> dict:
        a = inputs.args
        return dict(tables=crosscity.group_by_city(inputs.tables),
                    out_prefix=a.out_prefix, sources=a.table,
                    bucket_minutes=a.bucket_minutes or 60, min_n=a.min_n)

    def d15_kwargs(inputs: ResolvedChartInputs) -> dict:
        a = inputs.args
        return dict(tables=inputs.tables, out_prefix=a.out_prefix, sources=a.table,
                    routes=_resolve_route_filter(a, inputs.table),
                    direction=a.direction, annotate=a.annotate)

    specs = [
        ChartSpec(
            key="C9", label="dot-and-whisker delay per stop",
            route_mode="single", exclude_route_aware=False, interactive_capable=True,
            multi_day_by_design=False, bucket_minutes_default=None, min_n_default=20,
            min_n_is_grid=False, min_trip_coverage_default=0.6, supports_combine=False,
            supports_annotate=False, annotate_default=6, supports_threshold=False,
            threshold_default=0.25, supports_direction=True,
            render=punctuality.dot_and_whisker, build_kwargs=c9_kwargs,
        ),
        ChartSpec(
            key="C10", label="delay percentile fan through the day",
            route_mode="multi", exclude_route_aware=True, interactive_capable=True,
            multi_day_by_design=False, bucket_minutes_default=15, min_n_default=20,
            min_n_is_grid=False, min_trip_coverage_default=None, supports_combine=False,
            supports_annotate=False, annotate_default=6, supports_threshold=False,
            threshold_default=0.25, supports_direction=False,
            render=punctuality.percentile_fan, build_kwargs=c10_kwargs,
        ),
        ChartSpec(
            key="C11", label="punctuality mix through the day",
            route_mode="multi", exclude_route_aware=True, interactive_capable=False,
            multi_day_by_design=False, bucket_minutes_default=30, min_n_default=20,
            min_n_is_grid=False, min_trip_coverage_default=None, supports_combine=True,
            supports_annotate=False, annotate_default=6, supports_threshold=False,
            threshold_default=0.25, supports_direction=False,
            render=punctuality.punctuality_bands, build_kwargs=c11_kwargs,
        ),
        ChartSpec(
            key="A2", label="every trip run as its own trajectory",
            route_mode="single", exclude_route_aware=False, interactive_capable=False,
            multi_day_by_design=False, bucket_minutes_default=None, min_n_default=None,
            min_n_is_grid=False, min_trip_coverage_default=0.6, supports_combine=False,
            supports_annotate=False, annotate_default=6, supports_threshold=False,
            threshold_default=0.25, supports_direction=True,
            render=trajectory.spaghetti, build_kwargs=a2_kwargs,
        ),
        ChartSpec(
            key="B5", label="headway regularity (CV) by stop and hour",
            route_mode="single", exclude_route_aware=False, interactive_capable=False,
            multi_day_by_design=False, bucket_minutes_default=60, min_n_default=3,
            min_n_is_grid=True, min_trip_coverage_default=None, supports_combine=False,
            supports_annotate=False, annotate_default=6, supports_threshold=False,
            threshold_default=0.25, supports_direction=True,
            render=headway.cv_heatmap, build_kwargs=b5_kwargs,
        ),
        ChartSpec(
            key="B6", label="actual vs scheduled wait, excess as the gap",
            route_mode="multi", exclude_route_aware=True, interactive_capable=True,
            multi_day_by_design=False, bucket_minutes_default=60, min_n_default=5,
            min_n_is_grid=False, min_trip_coverage_default=None, supports_combine=False,
            supports_annotate=False, annotate_default=6, supports_threshold=False,
            threshold_default=0.25, supports_direction=False,
            render=headway.excess_wait, build_kwargs=b6_kwargs,
        ),
        ChartSpec(
            key="B7", label="headway distribution by hour, as a ridgeline",
            route_mode="single", exclude_route_aware=False, interactive_capable=False,
            multi_day_by_design=False, bucket_minutes_default=60, min_n_default=20,
            min_n_is_grid=False, min_trip_coverage_default=None, supports_combine=False,
            supports_annotate=False, annotate_default=6, supports_threshold=False,
            threshold_default=0.25, supports_direction=True,
            render=headway.headway_ridgeline, build_kwargs=b7_kwargs,
        ),
        ChartSpec(
            key="B8", label="bunching frequency by stop and hour",
            route_mode="single", exclude_route_aware=False, interactive_capable=False,
            multi_day_by_design=False, bucket_minutes_default=60, min_n_default=3,
            min_n_is_grid=True, min_trip_coverage_default=None, supports_combine=False,
            supports_annotate=False, annotate_default=6, supports_threshold=True,
            threshold_default=0.25, supports_direction=True,
            render=headway.bunching_heatmap, build_kwargs=b8_kwargs,
        ),
        ChartSpec(
            key="D14", label="segment speed by segment and hour",
            route_mode="single", exclude_route_aware=False, interactive_capable=False,
            multi_day_by_design=False, bucket_minutes_default=120, min_n_default=3,
            min_n_is_grid=True, min_trip_coverage_default=None, supports_combine=False,
            supports_annotate=False, annotate_default=6, supports_threshold=False,
            threshold_default=0.25, supports_direction=True,
            render=speed.speed_heatmap, build_kwargs=d14_kwargs,
        ),
        ChartSpec(
            key="D17", label="schedule slack: observed minus scheduled running time",
            route_mode="single", exclude_route_aware=False, interactive_capable=False,
            multi_day_by_design=False, bucket_minutes_default=120, min_n_default=3,
            min_n_is_grid=True, min_trip_coverage_default=None, supports_combine=False,
            supports_annotate=False, annotate_default=6, supports_threshold=False,
            threshold_default=0.25, supports_direction=True,
            render=speed.schedule_padding, build_kwargs=d17_kwargs,
        ),
        ChartSpec(
            key="D15", label="systematic vs stochastic loss per segment (needs several days)",
            route_mode="multi", exclude_route_aware=True, interactive_capable=False,
            multi_day_by_design=True, bucket_minutes_default=None, min_n_default=None,
            min_n_is_grid=False, min_trip_coverage_default=None, supports_combine=False,
            supports_annotate=True, annotate_default=6, supports_threshold=False,
            threshold_default=0.25, supports_direction=True,
            render=speed.systematic_vs_stochastic, build_kwargs=d15_kwargs,
        ),
        ChartSpec(
            key="E20", label="terminus-layover artifact profile across cities",
            route_mode="none", exclude_route_aware=False, interactive_capable=False,
            multi_day_by_design=True, bucket_minutes_default=None, min_n_default=None,
            min_n_is_grid=False, min_trip_coverage_default=None, supports_combine=False,
            supports_annotate=False, annotate_default=6, supports_threshold=False,
            threshold_default=0.25, supports_direction=False,
            render=crosscity.artifact_profile, build_kwargs=e20_kwargs,
        ),
        ChartSpec(
            key="H28", label="network-wide headway regularity ranking",
            route_mode="multi", exclude_route_aware=True, interactive_capable=False,
            multi_day_by_design=False, bucket_minutes_default=None, min_n_default=20,
            min_n_is_grid=False, min_trip_coverage_default=None, supports_combine=False,
            supports_annotate=False, annotate_default=6, supports_threshold=False,
            threshold_default=0.25, supports_direction=False,
            render=headway.regularity_ranking, build_kwargs=h28_kwargs,
        ),
        ChartSpec(
            key="H29", label="network-wide excess wait ranking (absolute + relative)",
            route_mode="multi", exclude_route_aware=True, interactive_capable=False,
            multi_day_by_design=False, bucket_minutes_default=None, min_n_default=20,
            min_n_is_grid=False, min_trip_coverage_default=None, supports_combine=False,
            supports_annotate=False, annotate_default=6, supports_threshold=False,
            threshold_default=0.25, supports_direction=False,
            render=headway.excess_wait_ranking, build_kwargs=h29_kwargs,
        ),
        ChartSpec(
            key="H30", label="network-wide bunching frequency, route by hour",
            route_mode="multi", exclude_route_aware=True, interactive_capable=False,
            multi_day_by_design=False, bucket_minutes_default=60, min_n_default=3,
            min_n_is_grid=True, min_trip_coverage_default=None, supports_combine=False,
            supports_annotate=False, annotate_default=6, supports_threshold=True,
            threshold_default=0.25, supports_direction=False,
            render=headway.bunching_by_route_heatmap, build_kwargs=h30_kwargs,
        ),
        ChartSpec(
            key="J39",
            label="H31 overlaid across cities: median pooled stop headway through the day",
            route_mode="none", exclude_route_aware=False, interactive_capable=False,
            multi_day_by_design=True, bucket_minutes_default=60, min_n_default=20,
            min_n_is_grid=False, min_trip_coverage_default=None, supports_combine=False,
            supports_annotate=False, annotate_default=6, supports_threshold=False,
            threshold_default=0.25, supports_direction=False,
            render=render_stop_headway.citywide_comparison, build_kwargs=j39_kwargs,
        ),
    ]
    return {s.key: s for s in specs}
