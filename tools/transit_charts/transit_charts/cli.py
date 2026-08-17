"""Command-line entry point.

    py -m transit_charts.cli extract --matched <table.csv> --static <gtfs.zip> --city lodz \\
        --route 10* --route 11 --out out/lodz_2026-07-21.csv.gz

No interactive prompts and no hardcoded paths anywhere: this starts as a research tool but is
meant to be callable from a scheduled job later, and the difference between those two is mostly
a matter of never asking the operator anything.
"""
from __future__ import annotations

import argparse
import sys

import pandas as pd
from pathlib import Path

from transit_charts import extract as extract_mod
from transit_charts import quality, sources, tidy


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="transit_charts",
        description="Charts of scheduled vs observed transit, from Family A matched positions.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_extract = sub.add_parser(
        "extract",
        help="build the cached tidy table one or more charts then read",
        description=(
            "Interpolates every scheduled stop crossing from a matched table and writes the "
            "tidy table the chart commands consume. Run this once per city-day; re-running a "
            "chart never repeats it."
        ),
    )
    p_extract.add_argument("--matched", required=True, type=Path, help="match output table")
    p_extract.add_argument("--static", required=True, type=Path, help="static GTFS .zip")
    p_extract.add_argument("--city", required=True, help="city label carried into the table")
    p_extract.add_argument(
        "--route",
        action="append",
        default=[],
        metavar="NAME",
        help=(
            "route_short_name to include; repeatable. A trailing * matches by prefix, e.g. "
            "'55*' for 55A/55B/55C. Omit to extract the whole feed (slower). A pattern that "
            "matches nothing is an error, not an empty chart."
        ),
    )
    p_extract.add_argument(
        "--group-variants",
        action="store_true",
        help="chart 10A and 10B as one series '10' (off by default - merging branches is an "
             "analytical choice, not a formatting one)",
    )
    p_extract.add_argument(
        "--max-bracket-gap-seconds",
        type=float,
        default=300.0,
        help="FA-14: reject a crossing whose two bracketing GPS observations are further apart "
             "than this in time - past it the interpolation measures sampling sparsity rather "
             "than a travel time (default: %(default)s)",
    )
    p_extract.add_argument(
        "--keep-first-segment",
        action="store_true",
        help="FA-20: keep each trip's first stop pair. Off by default because that pair "
             "absorbs the vehicle's layover on its origin terminus; pass this only when the "
             "artifact itself is the subject.",
    )
    p_extract.add_argument(
        "--outage-gap-seconds",
        type=float,
        default=quality.DEFAULT_OUTAGE_GAP_S,
        help="a silence longer than this across the WHOLE feed is treated as an outage, and "
             "headways spanning it are flagged (default: %(default)s)",
    )
    p_extract.add_argument("--out", required=True, type=Path, help="tidy table to write")
    p_extract.set_defaults(func=_cmd_extract)

    p_chart = sub.add_parser(
        "chart",
        help="render one chart from a tidy table produced by `extract`",
        description=(
            "Every chart writes three files: <out-prefix>.png, <out-prefix>.csv with exactly "
            "the numbers plotted, and <out-prefix>.json with the parameters and a fingerprint "
            "of the tidy table it read."
        ),
    )
    p_chart.add_argument("name", choices=sorted(CHARTS), help="which chart to draw")
    p_chart.add_argument(
        "--table", required=True, type=Path, action="append", dest="table",
        help="tidy table from `extract`; repeatable. D15 needs several days and every other "
             "chart simply concatenates them.",
    )
    p_chart.add_argument("--out-prefix", required=True, type=Path)
    p_chart.add_argument(
        "--route", action="append", default=[],
        help="route_short_name to chart; repeatable. C9 and A2 take exactly one.",
    )
    p_chart.add_argument(
        "--exclude-route", action="append", default=[], metavar="NAME",
        help="route_short_name to drop from the working set; repeatable, same NAME/'PREFIX*' "
             "matching as --route. Composes with --route (include first, then subtract); with "
             "no --route, subtracts from every route present in the table(s). For multi-route "
             "charts only (C10/C11/B6/D15/H28/H29/H30) - a single-route chart ignores it, since "
             "there is nothing left to subtract from one explicit --route.",
    )
    p_chart.add_argument("--direction", default=None,
                         help="direction_id; defaults to whichever has more observations")
    p_chart.add_argument("--bucket-minutes", type=int, default=None,
                         help="time-of-day bucket width (C10 default 15, C11 default 30)")
    p_chart.add_argument(
        "--min-n", type=int, default=20,
        help="buckets below this are drawn as 'insufficient data' rather than plotted "
             "(default: %(default)s). Applies to C9/C10/C11/B6 and, when passed explicitly, to "
             "the grid charts B5/B7/D14/D17. A2, D15 and E20 have no such notion and ignore it.",
    )
    p_chart.add_argument(
        "--min-trip-coverage", type=float, default=0.6,
        help="drop trip runs with less than this fraction of their stops observed - the "
             "recording-window edge guard, used by C9 and A2 (default: %(default)s)",
    )
    p_chart.add_argument(
        "--combine", action="store_true",
        help="C11 only: add a pooled 'all routes' panel above the per-route ones, so one "
             "figure answers both 'how is route 11 doing' and 'how is this selection doing'.",
    )
    p_chart.add_argument(
        "--annotate", type=int, default=6, metavar="N",
        help="D15 only: label the N most extreme segments (default: %(default)s; 0 turns the "
             "labels off). Labelling every point would obliterate the cloud.",
    )
    p_chart.add_argument(
        "--threshold", type=float, default=0.25,
        help="B8/H30 only: a headway below this fraction of its OWN scheduled interval counts "
             "as bunched (default: %(default)s, matching the handoff's definition). Ratio, not "
             "minutes, so lines of different frequency are comparable.",
    )
    p_chart.add_argument(
        "--html", action="store_true",
        help="also write a self-contained interactive page beside the PNG (C9, C10, B6). "
             "Built from the same sidecar table, so the two cannot disagree.",
    )
    p_chart.set_defaults(func=_cmd_chart)

    p_stop_headway = sub.add_parser(
        "stop-headway",
        help="pooled-across-all-lines headway per stop: a CSV for the hex map plus the H31 "
             "city-wide fluctuation chart",
        description=(
            "Always extracts the WHOLE feed (a --route filter would understate every stop's "
            "real frequency, since a stop served by 3 lines would only see the ones kept). "
            "Writes <out-prefix>_stops.csv (stop_id, lat, lon, n, median_headway_min - the "
            "map's input, see gtfs-rt-visualisation-catalogue_handoff.md I37) and the H31 "
            "chart (<out-prefix>_H31.png/.csv/.json)."
        ),
    )
    p_stop_headway.add_argument("--matched", required=True, type=Path)
    p_stop_headway.add_argument("--static", required=True, type=Path)
    p_stop_headway.add_argument("--city", required=True)
    p_stop_headway.add_argument("--out-prefix", required=True, type=Path)
    p_stop_headway.add_argument(
        "--min-n-stop", type=int, default=3,
        help="a stop with fewer than this many pooled headways gets no median in the map CSV "
             "(default: %(default)s, matching B5/B7/B8's grid-cell convention)",
    )
    p_stop_headway.add_argument(
        "--min-n-hour", type=int, default=20,
        help="H31 buckets below this many pooled headways are marked insufficient data "
             "(default: %(default)s)",
    )
    p_stop_headway.add_argument("--bucket-minutes", type=int, default=60,
                                help="H31 time-of-day bucket width (default: %(default)s)")
    p_stop_headway.add_argument(
        "--outage-gap-seconds", type=float, default=quality.DEFAULT_OUTAGE_GAP_S,
        help="same feed-outage guard as `extract` (default: %(default)s)",
    )
    p_stop_headway.set_defaults(func=_cmd_stop_headway)
    return parser


# Chart registry. `needs_single_route` is enforced rather than documented: stop_sequence is
# meaningless across routes, so C9/A2 silently averaging two of them would look plausible.
CHARTS = {
    "C9": ("dot-and-whisker delay per stop", True),
    "C10": ("delay percentile fan through the day", False),
    "C11": ("punctuality mix through the day", False),
    "A2": ("every trip run as its own trajectory", True),
    "B5": ("headway regularity (CV) by stop and hour", True),
    "B6": ("actual vs scheduled wait, excess as the gap", False),
    "B7": ("headway distribution by hour, as a ridgeline", True),
    "B8": ("bunching frequency by stop and hour", True),
    "D14": ("segment speed by segment and hour", True),
    "D17": ("schedule slack: observed minus scheduled running time", True),
    "D15": ("systematic vs stochastic loss per segment (needs several days)", False),
    "E20": ("terminus-layover artifact profile across cities", False),
    "H28": ("network-wide headway regularity ranking", False),
    "H29": ("network-wide excess wait ranking (absolute + relative)", False),
    "H30": ("network-wide bunching frequency, route by hour", False),
    "J39": ("H31 overlaid across cities: median pooled stop headway through the day", False),
}

# Charts with a sensible interactive form. The rest are heatmaps and ridgelines, where a
# hover tooltip adds nothing a sorted sidecar table does not already give.
INTERACTIVE = {"C9", "C10", "B6"}

# Charts for which several days is the intended input, not an accident: D15 separates a
# persistent offset from run-to-run variability and cannot work on one day; E20 compares
# cities and pools whatever each contributes.
MULTI_DAY_BY_DESIGN = {"D15", "E20", "J39"}

# Charts that take a `routes: list[str] | None` selection and so can honour --exclude-route.
# E20 is multi-route in the CHARTS table (no single --route requirement) but pools whatever
# each input table contributes with no route filter of its own - excluded here explicitly so
# it gets the same "ignored" note as a single-route chart, rather than silently doing nothing.
EXCLUDE_ROUTE_AWARE = {"C10", "C11", "B6", "D15", "H28", "H29", "H30"}


def _cmd_extract(args: argparse.Namespace) -> int:
    result = extract_mod.extract(
        matched_path=args.matched,
        static_path=args.static,
        city=args.city,
        route_patterns=args.route or None,
        group_variants=args.group_variants,
        max_bracket_gap_s=args.max_bracket_gap_seconds,
        skip_first_segment=not args.keep_first_segment,
        outage_gap_s=args.outage_gap_seconds,
    )
    written = extract_mod.write_table(result.table, args.out)
    print(f"Tidy table written to {written}")
    return 0


def _cmd_stop_headway(args: argparse.Namespace) -> int:
    # Imported here for the same reason `_cmd_chart` does: extraction stays importable without
    # matplotlib, which is the whole point of the extract/chart split.
    from transit_charts import stop_headway
    from transit_charts.render import stop_headway as render_stop_headway

    result = extract_mod.extract(
        matched_path=args.matched,
        static_path=args.static,
        city=args.city,
        route_patterns=None,  # forced whole-feed - see class docstring in stop_headway.py
        outage_gap_s=args.outage_gap_seconds,
    )
    locations = sources.stop_location_index(args.static)
    stops, missing_coords = stop_headway.per_stop_summary(
        result.table, result.report.outages, locations, min_n=args.min_n_stop,
    )
    stops_path = args.out_prefix.with_name(args.out_prefix.name + "_stops.csv")
    stops_path.parent.mkdir(parents=True, exist_ok=True)
    stops.to_csv(stops_path, index=False)
    print(f"Stop-level headway CSV written to {stops_path}  ({len(stops)} stops, "
          f"{missing_coords} missing coordinates)")

    chart = render_stop_headway.fluctuation(
        result.table, result.report.outages,
        out_prefix=args.out_prefix.with_name(args.out_prefix.name + "_H31"),
        source=args.matched, bucket_minutes=args.bucket_minutes, min_n=args.min_n_hour,
    )
    print(f"H31: {chart.png}")
    return 0


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


def _cmd_chart(args: argparse.Namespace) -> int:
    # Imported here, not at module scope: `extract` must stay runnable in an environment with
    # no matplotlib, which is the whole reason the two layers are separate.
    from transit_charts.render import crosscity, headway, punctuality, speed, trajectory
    from transit_charts.render import stop_headway as render_stop_headway

    tables = [extract_mod.read_table(path) for path in args.table]
    table = tables[0] if len(tables) == 1 else pd.concat(tables, ignore_index=True)
    # Pooling several days must never be silent - see tidy.describe_days for why.
    days, day_types, warning = tidy.describe_days(table)
    if days > 1:
        print(f"note: {len(args.table)} tables loaded, {days} service days "
              f"({', '.join(day_types) or 'unknown'})", file=sys.stderr)
    mixed_day_types = len(day_types) > 1
    if warning and (mixed_day_types or args.name not in MULTI_DAY_BY_DESIGN):
        # A mixed-day-type warning is never silenced, not even for D15/E20: a Saturday runs a
        # DIFFERENT timetable, so pooling it inflates D15's stochastic axis with a planned
        # difference and reads as unreliable running time.
        print(f"note: {args.name} {warning}", file=sys.stderr)

    _description, needs_single_route = CHARTS[args.name]
    if needs_single_route and len(args.route) != 1:
        raise sources.InputError(
            f"{args.name} needs exactly one --route (stop_sequence is route-specific, so "
            f"averaging several would compare unrelated places); got {len(args.route)}"
        )

    # Every table that went in, not just the first: the sidecar's fingerprint has to describe
    # the whole input or it claims a provenance the figure does not have.
    common = dict(out_prefix=args.out_prefix, source=args.table)
    if args.html and args.name in INTERACTIVE:
        common["interactive"] = True
    elif args.html:
        print(f"note: {args.name} has no interactive form; writing the PNG only",
              file=sys.stderr)
    # Flags that belong to exactly one chart say so rather than being quietly dropped - a flag
    # that appears to have been accepted is how a reader ends up trusting a figure that never
    # honoured it.
    if args.combine and args.name != "C11":
        print(f"note: --combine applies to C11 only; {args.name} ignores it", file=sys.stderr)
    if args.annotate != 6 and args.name != "D15":
        print(f"note: --annotate applies to D15 only; {args.name} ignores it", file=sys.stderr)
    if args.threshold != 0.25 and args.name not in ("B8", "H30"):
        print(f"note: --threshold applies to B8/H30 only; {args.name} ignores it",
              file=sys.stderr)
    if args.exclude_route and args.name not in EXCLUDE_ROUTE_AWARE:
        print(f"note: --exclude-route applies to {'/'.join(sorted(EXCLUDE_ROUTE_AWARE))} only; "
              f"{args.name} ignores it", file=sys.stderr)
    if args.name == "C9":
        result = punctuality.dot_and_whisker(
            table, route=args.route[0], direction=args.direction, min_n=args.min_n,
            min_trip_coverage=args.min_trip_coverage, **common,
        )
    elif args.name == "C10":
        result = punctuality.percentile_fan(
            table, routes=_resolve_route_filter(args, table), min_n=args.min_n,
            bucket_minutes=args.bucket_minutes or 15, **common,
        )
    elif args.name == "C11":
        result = punctuality.punctuality_bands(
            table, routes=_resolve_route_filter(args, table), min_n=args.min_n,
            bucket_minutes=args.bucket_minutes or 30, combine=args.combine, **common,
        )
    elif args.name == "A2":
        result = trajectory.spaghetti(
            table, route=args.route[0], direction=args.direction,
            min_trip_coverage=args.min_trip_coverage, **common,
        )
    elif args.name == "B5":
        result = headway.cv_heatmap(
            table, route=args.route[0], direction=args.direction,
            bucket_minutes=args.bucket_minutes or 60, **_grid_min_n(args), **common,
        )
    elif args.name == "B6":
        result = headway.excess_wait(
            table, routes=_resolve_route_filter(args, table),
            bucket_minutes=args.bucket_minutes or 60,
            min_n=max(5, args.min_n // 4), **common,
        )
    elif args.name == "B7":
        result = headway.headway_ridgeline(
            table, route=args.route[0], direction=args.direction,
            bucket_minutes=args.bucket_minutes or 60, min_n=args.min_n, **common,
        )
    elif args.name == "B8":
        result = headway.bunching_heatmap(
            table, route=args.route[0], direction=args.direction,
            bucket_minutes=args.bucket_minutes or 60, threshold=args.threshold,
            **_grid_min_n(args), **common,
        )
    elif args.name == "H28":
        result = headway.regularity_ranking(
            table, routes=_resolve_route_filter(args, table), min_n=args.min_n, **common,
        )
    elif args.name == "H29":
        result = headway.excess_wait_ranking(
            table, routes=_resolve_route_filter(args, table), min_n=args.min_n, **common,
        )
    elif args.name == "H30":
        result = headway.bunching_by_route_heatmap(
            table, routes=_resolve_route_filter(args, table),
            bucket_minutes=args.bucket_minutes or 60,
            threshold=args.threshold, **_grid_min_n(args), **common,
        )
    elif args.name == "D14":
        result = speed.speed_heatmap(
            table, route=args.route[0], direction=args.direction,
            bucket_minutes=args.bucket_minutes or 120,
            **_grid_min_n(args), **common,
        )
    elif args.name == "D17":
        result = speed.schedule_padding(
            table, route=args.route[0], direction=args.direction,
            bucket_minutes=args.bucket_minutes or 120,
            **_grid_min_n(args), **common,
        )
    elif args.name == "E20":
        result = crosscity.artifact_profile(
            crosscity.group_by_city(tables), out_prefix=args.out_prefix, sources=args.table,
        )
    elif args.name == "J39":
        result = render_stop_headway.citywide_comparison(
            crosscity.group_by_city(tables), out_prefix=args.out_prefix, sources=args.table,
            bucket_minutes=args.bucket_minutes or 60, min_n=args.min_n,
        )
    else:  # D15
        result = speed.systematic_vs_stochastic(
            tables, out_prefix=args.out_prefix, sources=args.table,
            routes=_resolve_route_filter(args, table), direction=args.direction,
            annotate=args.annotate,
        )

    print(f"{args.name}: {result.png}")
    if result.html:
        print(f"  interactive     : {result.html}")
    print(f"  numbers plotted : {result.csv}")
    print(f"  parameters      : {result.json}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Distinguish "the user chose 20" from "20 is the default", so a grid chart can keep its
    # own reachable default without ignoring an explicit request.
    args.min_n_explicit = any(a == "--min-n" or a.startswith("--min-n=") for a in (argv or sys.argv[1:]))
    try:
        return args.func(args)
    except (sources.InputError, ValueError) as exc:
        # ValueError is how a chart says "this selection cannot produce me, and here is why"
        # (too few days for D15, no observations for the route). Those messages are written
        # for a user, so they get printed like one rather than thrown as a traceback.
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
