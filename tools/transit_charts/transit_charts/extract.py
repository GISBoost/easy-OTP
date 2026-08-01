"""Orchestration: matched table + static GTFS -> the cached tidy table every chart reads.

Separated from plotting on purpose, and not only for tidiness. `family_a`'s requirements are
installed on a phone (Termux) where matplotlib has no wheels, so the extraction half has to
stay importable without it. The practical payoff is that iterating on how a chart *looks* never
re-runs the interpolation over Prague's 1.2M rows.
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from transit_charts import quality, sources, tidy

# family_a is a sibling standalone tool with its own venv - imported by path, the same way the
# scripts in gtfs-manual-test/ do it, rather than installed as a package.
_FAMILY_A = Path(__file__).resolve().parents[2] / "family_a_reconstruction"
if str(_FAMILY_A) not in sys.path:
    sys.path.insert(0, str(_FAMILY_A))

from family_a.build_gtfs import load_static_index  # noqa: E402
from family_a.calendar_scope import resolve_agency_timezone  # noqa: E402
from family_a.matcher import (  # noqa: E402
    load_shape_dist_traveled,
    load_stop_locations,
    resolve_trip_shapes,
)
from family_a.segment_stats import collect_stop_crossings  # noqa: E402
from family_a.shape_dist import evaluate_shape_trust, evaluate_trip_trust  # noqa: E402


@dataclass(frozen=True)
class ExtractResult:
    table: pd.DataFrame
    report: quality.QualityReport
    route_selection: sources.RouteSelection
    agency_tz: str
    elapsed_s: float


def extract(
    matched_path: Path,
    static_path: Path,
    city: str,
    route_patterns: list[str] | None = None,
    group_variants: bool = False,
    max_bracket_gap_s: float | None = 300.0,
    skip_first_segment: bool = True,
    outage_gap_s: float = quality.DEFAULT_OUTAGE_GAP_S,
    verbose: bool = True,
) -> ExtractResult:
    """Build the tidy table for one city-day.

    *route_patterns* is applied before the interpolation loop - filtering a whole city down to
    a handful of routes is the difference between seconds and minutes, and doing it afterwards
    would waste all of it. `None` means every route, which is what the cross-city and
    multi-day charts need.

    *skip_first_segment* defaults to True, matching `build`: the first stop pair of every trip
    absorbs the vehicle's layover on its origin terminus (FA-20). It stays a parameter because
    a chart deliberately *studying* that artifact needs to turn it off.
    """
    started = time.monotonic()
    report = quality.QualityReport()

    if route_patterns:
        selection = sources.resolve_routes(static_path, route_patterns, group_variants)
        if verbose:
            print(f"Routes resolved from {len(route_patterns)} pattern(s):")
            print(selection.describe())
        keep_trip_ids = set(sources.trip_route_index(static_path, selection.route_ids).trip_id)
    else:
        # Whole-feed mode reads no trips.txt at all: the route/direction of every crossing
        # already comes from family_a's StaticIndex, and parsing a 60k-row trips.txt only to
        # hand it to an unused parameter was pure cost on the largest cities.
        selection = sources.RouteSelection(set(), {}, {}, {})
        keep_trip_ids = None

    matched = sources.load_matched(matched_path, keep_trip_ids)
    matched = quality.drop_stale_observations(matched, report)
    outages = quality.find_outages(matched, outage_gap_s)
    report.outages = outages
    report.multi_vehicle_suspect_trips = len(quality.flag_non_monotonic_trips(matched))

    static_index = load_static_index(str(static_path))
    trip_shapes, shapes, _fallback = resolve_trip_shapes(str(static_path))
    stop_locations = load_stop_locations(str(static_path))
    agency_tz = resolve_agency_timezone(str(static_path))
    shape_cumulative, scale = evaluate_shape_trust(
        shapes, load_shape_dist_traveled(str(static_path))
    )
    trusted = evaluate_trip_trust(static_index, trip_shapes, shape_cumulative, scale)

    crossings, _counts = collect_stop_crossings(
        matched, static_index, trip_shapes, shapes, stop_locations,
        shape_cumulative_dist=shape_cumulative,
        trusted_stop_dist=trusted,
        max_bracket_gap_s=max_bracket_gap_s,
        skip_first_segment=skip_first_segment,
    )

    short_by_route = selection.short_name_by_route
    group_by_route = selection.display_group_by_route
    if not short_by_route:
        # Whole-feed mode: fall back to route_short_name from routes.txt for every route.
        routes = sources.read_gtfs_table(static_path, "routes.txt")
        names = routes.get("route_short_name", routes.route_id).fillna(routes.route_id)
        short_by_route = dict(zip(routes.route_id, names))
        group_by_route = dict(short_by_route)

    table = tidy.build(
        crossings,
        city=city,
        short_name_by_route=short_by_route,
        group_by_route=group_by_route,
        stop_names=sources.stop_name_index(static_path),
        # Read even in whole-feed mode, unlike the trip_id filter above: it is one extra parse
        # of trips.txt, and without it a whole-feed table can only ever title its charts
        # "direction 1".
        trip_headsigns=sources.trip_headsign_index(static_path),
        agency_tz=agency_tz,
        outages=outages,
        report=report,
    )

    elapsed = time.monotonic() - started
    if verbose:
        print(report.render())
        print(f"  tidy rows                 : {len(table):,}  ({elapsed:.1f}s)")
    return ExtractResult(table, report, selection, agency_tz, elapsed)


def write_table(table: pd.DataFrame, path: Path) -> Path:
    """Write the tidy table, gzipped CSV by default and Parquet when asked for and available.

    CSV is the default rather than Parquet because `pyarrow` is not a dependency of this tool
    and adding one for a cache format would be a poor trade. Parquet is honoured when the path
    says so and the library happens to be installed, and fails with a readable message if not.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".parquet":
        try:
            table.to_parquet(path, index=False)
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise sources.InputError(
                "Parquet output needs pyarrow or fastparquet installed; "
                "use a .csv.gz path instead"
            ) from exc
        return path
    table.to_csv(path, index=False, compression="gzip" if path.suffix == ".gz" else None)
    return path


def read_table(path: Path) -> pd.DataFrame:
    """Read a cached tidy table back with the datetime columns restored.

    A round trip through CSV loses tz-awareness unless it is asked for explicitly, and a chart
    that silently got naive timestamps would plot in UTC while claiming local time.
    """
    if not path.exists():
        raise sources.InputError(f"tidy table not found: {path} - run `extract` first")
    if path.suffix == ".parquet":
        table = pd.read_parquet(path)
    else:
        # low_memory=False: pandas otherwise infers dtypes per chunk and warns about mixed
        # types on the timestamp columns of a large table, which is noise rather than a
        # finding - they are parsed explicitly two lines below.
        table = pd.read_csv(
            path, dtype={"trip_id": str, "stop_id": str, "route_id": str}, low_memory=False
        )
    for column in ("obs_time", "sched_arr", "sched_dep"):
        if column in table.columns:
            table[column] = pd.to_datetime(table[column], utc=True, format="mixed")
    if "obs_local" in table.columns:
        table["obs_local"] = pd.to_datetime(table["obs_local"], utc=False, format="mixed")

    # A cached table written by an older version of this tool is missing whatever columns were
    # added since, and a chart then dies deep inside pandas with an AttributeError naming a
    # column the user never heard of. Checked here, once, with the fix in the message.
    missing = [c for c in tidy.TIDY_COLUMNS if c not in table.columns]
    if missing:
        raise sources.InputError(
            f"{path.name} was written by an older version of transit_charts and is missing "
            f"column(s): {', '.join(missing)}. Re-run `extract` to rebuild it."
        )
    return table
