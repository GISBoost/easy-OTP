"""Loading the two inputs (a matched table and a static GTFS zip) and resolving route names.

Everything here is about failing loudly on the inputs rather than producing an empty chart.
The route resolver in particular exists because of a real trap: Łódź publishes no route named
`10`, `55` or `69` - they are `10A`/`10B`, `55A`/`55B`/`55C`, `69A`/`69B`, and `11` is a tram.
Asking for "10" and getting a blank figure with no error is exactly the failure mode this
module refuses to have.
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

# Only the columns the extraction needs. Named explicitly rather than read-everything because
# Prague's matched table is 93 MB / 1.2M rows and the unused columns cost real time.
_MATCHED_COLUMNS = ["trip_id", "timestamp", "distance_along_shape_m"]
# recording_date only: FA-6 needs it to keep two days of one trip_id apart. The other
# columns a matched table carries (perpendicular_dist_m, position_signal) are read by
# nothing here since FA-20 removed the signal condition, so they are not loaded.
_MATCHED_OPTIONAL = ["recording_date"]


class InputError(RuntimeError):
    """Raised for an input problem a user can fix - never a traceback in their face."""


def _open_member(zip_path: Path, member: str):
    with zipfile.ZipFile(zip_path) as zf:
        names = [n for n in zf.namelist() if n.split("/")[-1] == member]
        if not names:
            raise InputError(f"{zip_path.name} contains no {member}")
        return io.BytesIO(zf.read(names[0]))


def read_gtfs_table(zip_path: Path, member: str, usecols: list[str] | None = None) -> pd.DataFrame:
    """Read one GTFS text file as all-strings, tolerating a UTF-8 BOM.

    dtype=str throughout on purpose: GTFS ids are opaque strings, and letting pandas infer
    turns Boston's numeric trip_ids into ints that then match nothing (the FA-16 defect).
    """
    buf = _open_member(zip_path, member)
    frame = pd.read_csv(buf, dtype=str, encoding="utf-8-sig")
    if usecols is not None:
        missing = [c for c in usecols if c not in frame.columns]
        if missing:
            raise InputError(f"{zip_path.name}/{member} is missing column(s): {', '.join(missing)}")
        frame = frame[usecols]
    return frame


def load_matched(path: Path, keep_trip_ids: set[str] | None = None) -> pd.DataFrame:
    """Load a `match` output table, optionally filtered to a set of trip_ids.

    *keep_trip_ids* is applied here, before anything expensive downstream touches the rows -
    filtering a whole city to four routes cuts the interpolation loop by ~99%, and doing it
    later would waste that.
    """
    if not path.exists():
        raise InputError(f"matched table not found: {path}")
    header = pd.read_csv(path, nrows=0)
    missing = [c for c in _MATCHED_COLUMNS if c not in header.columns]
    if missing:
        raise InputError(f"{path.name} is missing column(s): {', '.join(missing)}")

    usecols = _MATCHED_COLUMNS + [c for c in _MATCHED_OPTIONAL if c in header.columns]
    frame = pd.read_csv(
        path, usecols=usecols, dtype={"trip_id": str, "recording_date": str, "position_signal": str}
    )
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, format="mixed")
    if keep_trip_ids is not None:
        frame = frame[frame.trip_id.isin(keep_trip_ids)].copy()
    if frame.empty:
        raise InputError(
            f"{path.name} has no rows left after filtering - check the route selection "
            "against the static feed's route_short_name values"
        )
    return frame


@dataclass(frozen=True)
class RouteSelection:
    """What a user's route patterns actually resolved to, so it can be printed and recorded."""

    route_ids: set[str]
    short_name_by_route: dict[str, str]
    display_group_by_route: dict[str, str]
    matched_patterns: dict[str, list[str]]

    def describe(self) -> str:
        lines = []
        for pattern in sorted(self.matched_patterns):
            names = ", ".join(sorted(self.matched_patterns[pattern]))
            lines.append(f"  {pattern!r} -> {names}")
        return "\n".join(lines)


def match_route_names(
    patterns: list[str], known: set[str],
) -> tuple[dict[str, list[str]], set[str], list[str]]:
    """(matches per pattern, union of matched names, patterns with no hit).

    Each pattern matches a name exactly, or as a prefix when it ends in `*` (`55*` catches
    `55A`, `55B`, `55C`). Matching is case-insensitive because feeds are not consistent about
    it. Shared by `resolve_routes` below (extract time - needs `route_id` from `routes.txt`)
    and `cli._resolve_route_filter` (chart time - matches directly against the
    `route_short_name`/`route_group` values already carried by the tidy table, with no GTFS
    file in reach), so the two never quietly drift into different wildcard semantics.
    """
    matched_patterns: dict[str, list[str]] = {}
    selected_names: set[str] = set()
    unmatched: list[str] = []

    for pattern in patterns:
        if pattern.endswith("*"):
            prefix = pattern[:-1].casefold()
            hits = {n for n in known if n.casefold().startswith(prefix)}
        else:
            hits = {n for n in known if n.casefold() == pattern.casefold()}
        if not hits:
            unmatched.append(pattern)
            continue
        matched_patterns[pattern] = sorted(hits)
        selected_names |= hits

    return matched_patterns, selected_names, unmatched


def resolve_routes(
    static_zip: Path,
    patterns: list[str],
    group_variants: bool = False,
) -> RouteSelection:
    """Turn user-supplied route names into route_ids, or fail with a usable message.

    Each pattern matches a `route_short_name` exactly, or as a prefix when it ends in `*`
    (`55*` catches `55A`, `55B`, `55C`). Matching is case-insensitive because feeds are not
    consistent about it.

    *group_variants* maps every matched route to a display group formed by stripping the
    trailing letter suffix, so `10A` and `10B` are charted as one line "10". Off by default:
    merging two directions-of-branching into one series is an analytical choice, not a default.

    Raises InputError when a pattern matches nothing, listing near misses. A silently empty
    selection is the single easiest way to publish a chart of nothing.
    """
    routes = read_gtfs_table(static_zip, "routes.txt")
    if "route_short_name" not in routes.columns:
        raise InputError(f"{static_zip.name}/routes.txt has no route_short_name column")
    routes = routes.assign(route_short_name=routes.route_short_name.fillna(""))

    known = {name for name in routes.route_short_name if name}
    matched_patterns, selected_names, unmatched = match_route_names(patterns, known)

    if unmatched:
        raise InputError(
            "no route matches "
            + ", ".join(repr(p) for p in unmatched)
            + ".\nDid you mean one of: "
            + ", ".join(sorted(_near_misses(unmatched, known))[:20])
            + "\n(this feed has "
            + str(len(known))
            + " routes; a trailing * matches by prefix, e.g. '55*' for 55A/55B/55C)"
        )

    selected = routes[routes.route_short_name.isin(selected_names)]
    short_by_route = dict(zip(selected.route_id, selected.route_short_name))
    group_by_route = {
        rid: (_variant_group(name) if group_variants else name)
        for rid, name in short_by_route.items()
    }
    return RouteSelection(
        route_ids=set(selected.route_id),
        short_name_by_route=short_by_route,
        display_group_by_route=group_by_route,
        matched_patterns=matched_patterns,
    )


def _variant_group(short_name: str) -> str:
    """`10A` -> `10`, `55C` -> `55`, `N1A` -> `N1`; anything else is returned unchanged."""
    match = re.fullmatch(r"(.*\d)([A-Za-z])", short_name)
    return match.group(1) if match else short_name


def _near_misses(patterns: list[str], known: set[str]) -> set[str]:
    """Route names sharing a leading digit/letter run with an unmatched pattern.

    Cheap on purpose - the point is to surface `10A`/`10B` when someone asked for `10`, not to
    implement fuzzy matching.
    """
    out: set[str] = set()
    for pattern in patterns:
        stem = pattern.rstrip("*").casefold()
        if not stem:
            continue
        out |= {n for n in known if n.casefold().startswith(stem[:2])}
    return out or known


def trip_route_index(static_zip: Path, route_ids: set[str] | None = None) -> pd.DataFrame:
    """trips.txt reduced to (trip_id, route_id, direction_id), optionally route-filtered."""
    trips = read_gtfs_table(static_zip, "trips.txt")
    for column in ("trip_id", "route_id"):
        if column not in trips.columns:
            raise InputError(f"{static_zip.name}/trips.txt has no {column} column")
    if "direction_id" not in trips.columns:
        trips = trips.assign(direction_id="0")
    trips = trips[["trip_id", "route_id", "direction_id"]].fillna({"direction_id": "0"})
    if route_ids is not None:
        trips = trips[trips.route_id.isin(route_ids)]
    return trips.reset_index(drop=True)


def trip_headsign_index(static_zip: Path) -> dict[str, str]:
    """trip_id -> trip_headsign, for titling a direction the way a passenger sees it.

    `direction_id` is a feed-internal 0/1 that means nothing to a reader; the headsign is the
    terminus written on the front of the vehicle. Optional in GTFS and blank in some feeds, so
    the caller must tolerate an empty result - `tidy.direction_label` falls back to the last
    stop of the direction.
    """
    trips = read_gtfs_table(static_zip, "trips.txt")
    if "trip_headsign" not in trips.columns:
        return {}
    return {
        tid: name.strip()
        for tid, name in zip(trips.trip_id, trips.trip_headsign)
        if isinstance(name, str) and name.strip()
    }


def stop_name_index(static_zip: Path) -> dict[str, str]:
    """stop_id -> stop_name, for readable axis labels. Missing names fall back to the id."""
    stops = read_gtfs_table(static_zip, "stops.txt")
    if "stop_name" not in stops.columns:
        return {}
    return {
        sid: (name if isinstance(name, str) and name else sid)
        for sid, name in zip(stops.stop_id, stops.stop_name)
    }
