"""Command-line interface for family_a_reconstruction (FA-1, FA-2, FA-3, FA-4).

Standalone CLI — never imported by, and never imports, easy_otp/. Run (all options on one
line - the "\" bash-style continuation shown in older revisions of this docstring breaks on
cmd.exe/PowerShell; see each subcommand's own --help for a copy-pasteable example):

    py -m family_a.cli record --url <VehiclePositions.pb URL> --out-dir <dir> [--duration-min N] [--interval-sec N]
    py -m family_a.cli match --positions-dir <dir> [<dir> ...] --static <gtfs.zip> --out <table> [--max-perpendicular-dist-m N]
    py -m family_a.cli build --matched <table> --static <gtfs.zip> --out-prefix <prefix> [--min-observations-per-segment N] [--time-bucket-minutes N] [--max-bracket-gap-seconds N]
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
import zipfile
import zoneinfo
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from family_a.build_gtfs import (
    DEFAULT_MIN_CORRECTED_ROUTE_SHARE,
    load_static_index,
    rebuild_stop_times,
    repackage_gtfs,
)
from family_a.calendar_scope import load_service_day_types, resolve_agency_timezone
from family_a.interpolate import DEFAULT_MAX_BRACKET_GAP_S, resolve_all_trip_stop_anchors
from family_a.matcher import (
    DEFAULT_MAX_REJECT_SHARE,
    DEFAULT_MIN_OBSERVATIONS_FOR_ROUTE_ALERT,
    DEFAULT_POSITION_SIGNAL_COVERAGE_THRESHOLD,
    load_shape_dist_traveled,
    load_stop_locations,
    load_trip_route_index,
    match_snapshots,
    observed_trip_ids,
    resolve_trip_shapes,
    snapshot_feed_timestamp,
)
from family_a.recorder import (
    SnapshotFetchError,
    earliest_recording_date,
    fetch_snapshot,
    parse_snapshot_filename,
    snapshot_filename,
    write_manifest,
    write_snapshot,
)
from family_a.segment_stats import (
    aggregate_segments,
    collect_segment_observations,
    filter_min_observations,
)
from family_a.shape_dist import evaluate_shape_trust, evaluate_trip_trust


def _cmd_record(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    total_sec = args.duration_min * 60
    ok_count = 0
    failed_count = 0
    total_bytes = 0
    started_at = datetime.now()

    print(
        f"Recording started. Duration: {args.duration_min} min, "
        f"interval: {args.interval_sec} s. Output: {out_dir}"
    )
    try:
        start_mono = time.monotonic()
        while True:
            elapsed = time.monotonic() - start_mono
            if elapsed >= total_sec:
                break

            now = datetime.now()
            try:
                data = fetch_snapshot(args.url)
                write_snapshot(out_dir, data, now)
                ok_count += 1
                total_bytes += len(data)
                print(f"[{ok_count}] {snapshot_filename(now)} ({len(data):,} B)")
            except (SnapshotFetchError, OSError) as exc:
                failed_count += 1
                print(f"Poll {ok_count + failed_count} failed: {exc}")

            # Sleep in 1-second steps so Ctrl+C (KeyboardInterrupt) stays responsive.
            next_poll = time.monotonic() + args.interval_sec
            while True:
                now_mono = time.monotonic()
                if now_mono - start_mono >= total_sec or now_mono >= next_poll:
                    break
                time.sleep(1)
    except KeyboardInterrupt:
        print("\nRecording interrupted by user (Ctrl+C). Writing manifest for partial archive...")
    finally:
        stopped_at = datetime.now()
        write_manifest(
            out_dir,
            args.url,
            "",
            args.interval_sec,
            started_at,
            stopped_at,
            ok_count,
            failed_count,
            total_bytes,
        )

    print(
        f"Recording finished: {ok_count} snapshots, {failed_count} failed, "
        f"{total_bytes / 1024:.1f} KB total. Manifest: {out_dir / 'recording.json'}"
    )
    return 0


def _positive_int(value: str) -> int:
    """argparse type: reject zero/negative values.

    A zero or negative --interval-sec turns the inter-poll sleep into a
    no-op, degenerating the loop into a tight hammering of the remote feed —
    the exact kind of client behaviour that got this tool's own target feed
    (mkuran.pl) to start blocking requests in the first place.
    """
    ivalue = int(value)
    if ivalue < 1:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {value}")
    return ivalue


def _duration_minutes(value: str) -> int:
    """argparse type: positive integer, capped at 1500 minutes (25h).

    Consistent with the plugin's RT3-1 cap (docs/prd/PR_easy-OTP_v07.md, RT3-1) - enough
    margin for overnight trips crossing midnight, while ruling out multi-day continuous
    recordings by construction. Family A's intended multi-day workflow (FA-6) is several
    separate one-day sessions merged at match time, not one long recording.
    """
    ivalue = _positive_int(value)
    if ivalue > 1500:
        raise argparse.ArgumentTypeError(
            f"must be at most 1500 minutes (25h) - got {value}. For multi-day coverage, run "
            "several separate 'record' sessions and merge them with 'match "
            "--positions-dir ... --positions-dir ...' (see README)."
        )
    return ivalue


def _validate_static_gtfs(path: str, required_files: tuple[str, ...]) -> str | None:
    """Return an error message if *path* is not a usable static GTFS zip, else None.

    Checked once here (rather than in matcher.py/build_gtfs.py, which each open
    the zip again on their own) so 'match'/'build' fail with a clear message
    instead of a raw zipfile/OSError traceback. *required_files* differs per
    caller - see _validate_static_gtfs_for_match for 'match's dynamic case;
    'build' always needs trips.txt/stop_times.txt/stops.txt regardless of
    shapes.txt, since it calls load_static_index/load_stop_locations directly.
    """
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
    except OSError:
        return f"Static GTFS not found or unreadable: {path}"
    except zipfile.BadZipFile:
        return f"Static GTFS is not a valid zip file: {path}"

    missing = [name for name in required_files if name not in names]
    if missing:
        return f"Static GTFS {path} is missing required file(s): {', '.join(missing)}"
    return None


def _validate_static_gtfs_for_match(path: str) -> str | None:
    """Like _validate_static_gtfs, but for 'match's dynamic requirement.

    'match' only ever touches trips.txt directly - but when shapes.txt is
    absent, resolve_trip_shapes falls back to
    matcher.load_fallback_shapes_from_stops, which opens stops.txt and
    stop_times.txt unconditionally. Without checking for those upfront too, a
    static GTFS missing shapes.txt *and* one of those fallback files would
    raise a raw zipfile KeyError deep in the fallback path instead of failing
    here with a clear message - exactly the failure mode this milestone exists
    to eliminate. Requires a second zipfile open (namelist() only reads the
    central directory, so this is cheap) to decide which set of required
    files applies before delegating to _validate_static_gtfs.
    """
    try:
        with zipfile.ZipFile(path) as zf:
            has_shapes = "shapes.txt" in zf.namelist()
    except (OSError, zipfile.BadZipFile):
        has_shapes = True  # let _validate_static_gtfs below report the real error

    required = ("trips.txt",) if has_shapes else ("trips.txt", "stops.txt", "stop_times.txt")
    return _validate_static_gtfs(path, required_files=required)


# ---------------------------------------------------------------------------
# FA-15: per-route diagnostics and low-yield reporting
# ---------------------------------------------------------------------------
#
# Why this exists: before FA-15 both commands only ever reported whole-run totals, so a route
# whose every observation was rejected produced a realized GTFS byte-identical to its static
# schedule - downstream, indistinguishable from a route that ran perfectly on time. Three real
# failures reached published releases that way (see the 2026-07-28 audit section of
# docs/handoffs/family-a-matching-accuracy_handoff.md): Łódź route 603 (7,478 observations, 100%
# rejected, while that day's whole-run reject share was an unremarkable 9.53%), Poznań 2026-07-17
# (79.19% of observations rejected outright), and Turin 2026-07-20 (217 corrected stop_times rows
# out of 1,416,230 - and, normalised against what was correctable at all that day, 0.0% of the
# 52.2% of rows and 0.9% of the 99.1% of routes whose service actually ran). None of them warned
# about anything.
#
# That normalisation matters and is easy to get wrong: a raw "% of stop_times rows changed" is NOT
# a performance measure, because a recording covers one day while the static feed is valid for
# weeks, so most rows were never correctable. Healthy cities land at 66-82% of what was achievable
# on their day (Łódź, for instance, could only ever have corrected 35.4% of its rows). None of the
# diagnostics below use that confounded ratio: the match-side ones are computed purely over
# observations seen in the RT feed, and the build-side one is normalised by routes actually
# observed, never by the static feed's own row count.
#
# Everything below is reporting only. It never changes an accept/reject decision, a matched row,
# or a corrected time.


def _merge_route_counts(
    total: dict[str, dict[str, int]], new: dict[str, dict[str, int]]
) -> None:
    """Accumulate one directory's per-route tally into the run-wide one, in place."""
    for route_id, counts in new.items():
        bucket = total.setdefault(route_id, {})
        for key, value in counts.items():
            bucket[key] = bucket.get(key, 0) + value


def _write_diagnostics_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    """Write the per-route breakdown, degrading to a warning if the path is unwritable.

    A diagnostic must never be able to fail the command it is reporting on: by the time this
    runs, 'match' has already written its output table and 'build' both realized zips, so
    raising here would abort with a traceback and a non-zero exit code purely because of an
    optional side file - breaking this milestone's own "reporting only, never changes an exit
    code outside --fail-on-low-yield" contract.
    """
    try:
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    except OSError as exc:
        print(f"Warning: could not write per-route diagnostics CSV to {path} ({exc}).", file=sys.stderr)
        return
    print(f"Per-route diagnostics CSV written to: {path} ({len(rows)} route(s))")


def _report_match_diagnostics(
    by_route: dict[str, dict[str, int]],
    unattributable: int,
    reject_counts: dict[str, int],
    accepted: int,
    excluded_route_ids: set[str],
    max_reject_share: float,
    min_observations_for_route_alert: int,
    diagnostics_csv: str | None,
) -> bool:
    """Print the per-route breakdown; return True if this run looks low-yield.

    Two things are deliberately kept OUT of the reject share, for different reasons:

    - corrupt_snapshot counts FILES that failed to decode, not observations, so folding it into
      an observation-level ratio would produce a number that is not a fraction of anything.
    - Observations belonging to routes the caller excluded via --exclude-route-id are rejected
      BY DESIGN, not by failure. Counting them would let a large excluded route (e.g. Bucharest's
      Metrorex) push the share over the threshold and trigger a warning whose text confidently
      blames a static/RT publication mismatch - a wrong diagnosis stated with certainty, which is
      precisely the failure mode this milestone exists to eliminate.

    no_trip_id IS counted, but is reported on its own line rather than folded into the
    unattributable total, because the two are different phenomena and the printed figures have to
    reconcile: a vehicle with no trip_id at all (a depot move, a non-service run) is rejected in
    the decode pass before any route could be looked up, so it appears in NEITHER the per-route
    breakdown nor the unattributable count. Without its own line the printed numbers would
    silently fail to add up to `attempted`.
    """
    excluded_accepted = sum(
        counts.get("accepted", 0)
        for route_id, counts in by_route.items()
        if route_id in excluded_route_ids
    )
    excluded_rejects = sum(
        counts.get("unknown_shape", 0) + counts.get("too_far_from_route", 0)
        for route_id, counts in by_route.items()
        if route_id in excluded_route_ids
    )
    no_trip_id = reject_counts.get("no_trip_id", 0)
    observation_rejects = (
        no_trip_id
        + reject_counts.get("unknown_shape", 0)
        + reject_counts.get("too_far_from_route", 0)
        - excluded_rejects
    )
    attempted = (accepted - excluded_accepted) + observation_rejects
    reject_share = observation_rejects / attempted if attempted else 0.0

    rows = []
    for route_id, counts in by_route.items():
        observations = (
            counts.get("accepted", 0)
            + counts.get("unknown_shape", 0)
            + counts.get("too_far_from_route", 0)
        )
        rows.append(
            {
                "route_id": route_id,
                "observations": observations,
                "accepted": counts.get("accepted", 0),
                "unknown_shape": counts.get("unknown_shape", 0),
                "too_far_from_route": counts.get("too_far_from_route", 0),
                "rejected_share": round(
                    (observations - counts.get("accepted", 0)) / observations, 4
                )
                if observations
                else 0.0,
            }
        )
    rows.sort(key=lambda r: (-r["rejected_share"], -r["observations"]))

    print(f"Observation reject share (FA-15): {reject_share * 100:.2f}% of {attempted} attempted")
    print(
        "  - not attributable to any route (trip_id absent from the static feed's trips.txt): "
        f"{unattributable}"
    )
    print(f"  - no trip_id at all (counted above, but never attributable to a route): {no_trip_id}")
    if excluded_rejects or excluded_accepted:
        print(
            f"  - excluded from the share entirely (--exclude-route-id, rejected by design): "
            f"{excluded_rejects + excluded_accepted}"
        )
    print(f"Routes observed (FA-15): {len(rows)}")

    # A route is only reported as silently invisible when it has enough observations to mean
    # something - see DEFAULT_MIN_OBSERVATIONS_FOR_ROUTE_ALERT. Routes the caller excluded on
    # purpose are not a finding: they are rejected by design, not by failure.
    invisible = [
        r
        for r in rows
        if r["accepted"] == 0
        and r["observations"] >= min_observations_for_route_alert
        and r["route_id"] not in excluded_route_ids
    ]
    print(
        f"  - routes with >={min_observations_for_route_alert} observations and NOT ONE accepted: "
        f"{len(invisible)}"
    )

    if diagnostics_csv:
        _write_diagnostics_csv(
            Path(diagnostics_csv),
            ["route_id", "observations", "accepted", "unknown_shape", "too_far_from_route", "rejected_share"],
            rows,
        )

    low_yield = False
    if reject_share > max_reject_share:
        low_yield = True
        print(
            f"WARNING (FA-15): {reject_share * 100:.1f}% of observations were rejected, above the "
            f"{max_reject_share * 100:.0f}% threshold. If most of them are unattributable "
            f"({unattributable}), the static feed is most likely from a different publication "
            "period than this recording (its trip_id namespace does not match) - check the "
            "static feed's own validity window against the recording date before trusting "
            "anything built from this table.",
            file=sys.stderr,
        )
    if invisible:
        low_yield = True
        named = ", ".join(f"{r['route_id']} ({r['observations']} obs)" for r in invisible[:10])
        more = f", and {len(invisible) - 10} more" if len(invisible) > 10 else ""
        print(
            f"WARNING (FA-15): {len(invisible)} route(s) had observations but not one was "
            f"accepted, so they will silently keep their scheduled times and look perfectly "
            f"on time downstream: {named}{more}.",
            file=sys.stderr,
        )
    return low_yield


def _report_build_diagnostics(
    route_counts: dict[str, dict[str, int]],
    observed_routes: set[str],
    min_corrected_route_share: float,
    diagnostics_csv: str | None,
) -> bool:
    """Print per-route correction coverage; return True if this build looks low-yield.

    Note on the CSV's own corrected_share column: unlike this function's low-yield metric, that
    per-route ratio is NOT normalised by what the recording observed. rebuild_stop_times walks
    the whole static feed - every trip, every service day inside the feed's validity window - so
    a perfectly healthy route recorded for one day out of a nine-day feed still shows a small
    corrected_share. Read that column as "how much of this route's whole published timetable got
    corrected", never as "how well was this route observed".
    """
    rows = []
    for route_id in sorted(observed_routes):
        counts = route_counts.get(route_id, {})
        corrected = counts.get("corrected", 0)
        gap = counts.get("gap", 0)
        rows.append(
            {
                "route_id": route_id,
                "corrected_segments": corrected,
                "gap_segments": gap,
                "corrected_share_full_feed": round(corrected / (corrected + gap), 4)
                if corrected + gap
                else 0.0,
            }
        )

    uncorrected = [r for r in rows if r["corrected_segments"] == 0]
    covered_share = (
        (len(rows) - len(uncorrected)) / len(rows) if rows else 0.0
    )
    print(f"Routes observed in the matched table (FA-15): {len(rows)}")
    print(
        f"  - of those, with at least one corrected segment: {len(rows) - len(uncorrected)} "
        f"({covered_share * 100:.1f}%)"
    )

    if diagnostics_csv:
        _write_diagnostics_csv(
            Path(diagnostics_csv),
            ["route_id", "corrected_segments", "gap_segments", "corrected_share_full_feed"],
            rows,
        )

    if not rows:
        # The worst case of all, and it must never be silent: not one observed route reached
        # this build. An empty matched table (a day the RT feed died, or every observation
        # rejected upstream) and a --static that isn't the feed 'match' ran against both land
        # here, and both produce a realized GTFS identical to the schedule. Reported separately
        # because a share is undefined with no routes to take it over - guarding the ratio
        # without saying anything would turn the loudest possible signal into silence.
        print(
            "WARNING (FA-15): not one route from the matched table reached this build, so the "
            "realized feed is the static schedule verbatim and will read as perfect punctuality "
            "downstream. Either the matched table is empty, or --static is not the feed 'match' "
            "was run against.",
            file=sys.stderr,
        )
        return True

    if covered_share < min_corrected_route_share:
        print(
            f"WARNING (FA-15): only {covered_share * 100:.1f}% of the {len(rows)} routes observed "
            f"in this run got any correction at all, below the "
            f"{min_corrected_route_share * 100:.0f}% threshold. The realized feed is mostly just "
            "the static schedule - it will look like near-perfect punctuality downstream. Check "
            "the match step's own reject counts before publishing this build.",
            file=sys.stderr,
        )
        return True
    return False


def _cmd_match(args: argparse.Namespace) -> int:
    positions_dirs = [Path(p) for p in args.positions_dir]

    # Reject the same directory given twice up front - per_dir_snapshots below
    # is keyed by Path, so a duplicate would otherwise be silently processed
    # only once while the summary's "Directories merged" count (taken from
    # positions_dirs, before dedup) still reported the original, higher
    # count - an inconsistency, not just redundant work. Compared via
    # resolve() so "day1" and ".\day1" are recognised as the same directory.
    seen_resolved: dict[Path, Path] = {}
    for positions_dir in positions_dirs:
        resolved = positions_dir.resolve()
        if resolved in seen_resolved:
            print(
                f"--positions-dir given the same directory twice: {seen_resolved[resolved]} "
                f"and {positions_dir} both resolve to {resolved}. Each --positions-dir must "
                "be a distinct recording session.",
                file=sys.stderr,
            )
            return 1
        seen_resolved[resolved] = positions_dir

    # Validate every directory up front (cheap - just a glob + filename parse)
    # before running any (possibly slow) matching, so a typo'd/empty directory
    # anywhere in a multi-directory call fails immediately with no partial
    # output and no wasted work on earlier directories (FA-6).
    per_dir_snapshots: dict[Path, list[Path]] = {}
    for positions_dir in positions_dirs:
        snapshot_paths = sorted(positions_dir.glob("snapshot_*.pb"))
        if not snapshot_paths:
            print(f"No snapshot_*.pb files found in {positions_dir}", file=sys.stderr)
            return 1
        for snapshot_path in snapshot_paths:
            if parse_snapshot_filename(snapshot_path.name) is None:
                print(
                    f"{positions_dir} contains a snapshot filename that does not match "
                    f"snapshot_YYYYmmdd-HHMMSS.pb: {snapshot_path.name}",
                    file=sys.stderr,
                )
                return 1
        per_dir_snapshots[positions_dir] = snapshot_paths

    static_error = _validate_static_gtfs_for_match(args.static)
    if static_error:
        print(static_error, file=sys.stderr)
        return 1

    trip_shapes, shapes, fallback_used = resolve_trip_shapes(
        args.static, exclude_route_ids=frozenset(args.exclude_route_id)
    )
    if fallback_used:
        print(
            "Warning: shapes.txt not found in static GTFS - falling back to "
            "straight-line stop-to-stop shapes (reduced accuracy).",
            file=sys.stderr,
        )

    # FA-10: use a trustworthy static shape_dist_traveled as the live-matching
    # distance axis when the feed provides one - falls back to geometric
    # (haversine) projection when it doesn't (empty dict, no behaviour change).
    shape_dist_raw = load_shape_dist_traveled(args.static)
    shape_cumulative_dist, shape_scale_factor = evaluate_shape_trust(shapes, shape_dist_raw)

    # FA-12: resolve every trip's own (already FA-10/FA-11-corrected) stop anchor list once, up
    # front - match_snapshots uses this to window each live observation's own map-matching
    # search instead of searching the whole route. 'match' has never required stop_times.txt
    # unless shapes.txt was also missing (_validate_static_gtfs_for_match), and never touched
    # stops.txt's own content at all - a feed that genuinely lacks stop_times.txt/stops.txt, or
    # has malformed rows in either (missing columns, non-numeric stop_sequence/stop_lat/stop_lon),
    # must degrade gracefully here to "no anchors, no windowing" rather than making 'match' newly
    # hard-fail for a static feed shape it previously supported - so the whole attempt (not just
    # the initial stop_times.txt load) is wrapped in one try/except, always falling back to an
    # empty trip_stop_anchors on any failure.
    try:
        static_index = load_static_index(args.static)
        stop_locations = load_stop_locations(args.static)
        trusted_stop_dist = evaluate_trip_trust(
            static_index, trip_shapes, shape_cumulative_dist, shape_scale_factor
        )
        # Restrict resolution to trips actually seen in this run's RT data - a static feed's
        # own trip roster can be tens of thousands of trips (e.g. Gdańsk: ~93,600), while any
        # single day's snapshots only ever report the small subset actually running. Resolving
        # every trip in the whole feed regardless of whether 'match' could ever need it made
        # this otherwise-cheap step cost proportional to static feed size, not RT data volume.
        all_observed_trip_ids: set[str] = set()
        for snapshot_paths in per_dir_snapshots.values():
            all_observed_trip_ids |= observed_trip_ids(snapshot_paths)
        trip_stop_anchors = resolve_all_trip_stop_anchors(
            static_index,
            trip_shapes,
            shapes,
            stop_locations,
            shape_cumulative_dist=shape_cumulative_dist,
            trusted_stop_dist=trusted_stop_dist,
            trip_ids=all_observed_trip_ids,
        )
    except (KeyError, OSError, ValueError) as exc:
        print(
            f"Warning: could not resolve stop anchors from static GTFS ({exc}) - FA-12 "
            "live-position windowing disabled for this run (falls back to today's "
            "unrestricted matching).",
            file=sys.stderr,
        )
        trip_stop_anchors = {}

    # FA-15: trip_id -> route_id for the per-route reject breakdown. Loaded separately from (and
    # deliberately unfiltered relative to) load_trip_shape_index - see load_trip_route_index's
    # docstring for why both differences matter.
    #
    # Guarded the same way FA-12's anchor resolution below is: _validate_static_gtfs_for_match
    # only checks that trips.txt EXISTS in the zip, not that it has the columns this reads, so a
    # malformed-but-previously-workable feed must not start hard-failing 'match' because of a
    # purely diagnostic index. Degrading to {} loses the breakdown for that run and nothing else.
    try:
        trip_routes = load_trip_route_index(args.static)
    except (KeyError, OSError, ValueError) as exc:
        print(
            f"Warning: could not read trip_id -> route_id from the static GTFS ({exc}) - FA-15 "
            "per-route diagnostics disabled for this run.",
            file=sys.stderr,
        )
        trip_routes = {}

    agency_tz = resolve_agency_timezone(args.static)
    zone = zoneinfo.ZoneInfo(agency_tz)

    frames: list[pd.DataFrame] = []
    total_reject_counts: dict[str, int] = {}
    total_by_route: dict[str, dict[str, int]] = {}
    total_unattributable = 0
    total_snapshots_processed = 0
    recording_dates: list[date] = []

    for positions_dir, snapshot_paths in per_dir_snapshots.items():
        # Prefer each snapshot's own GTFS-RT FeedHeader.timestamp (absolute,
        # agency-server UTC) over the snapshot filename (the recording
        # machine's naive local clock) - a machine recording a feed for a
        # city in a different timezone from itself would otherwise get the
        # wrong calendar date with no way to detect or correct it. Falls back
        # to the filename only when a directory has no usable header
        # timestamp at all (GTFS-RT marks it "strongly recommended", not
        # required). This decodes every snapshot a second time (match_snapshots
        # decodes them again below) - an accepted, unoptimized cost at this
        # tool's data volumes, consistent with the rest of this module.
        feed_timestamps = [snapshot_feed_timestamp(p) for p in snapshot_paths]
        valid_feed_timestamps = [ts for ts in feed_timestamps if ts is not None]
        if valid_feed_timestamps:
            recording_date = min(valid_feed_timestamps).astimezone(zone).date()
        else:
            recording_date = earliest_recording_date(snapshot_paths)
            print(
                f"Warning: no snapshot in {positions_dir} has a usable GTFS-RT feed "
                "timestamp (header.timestamp unset/0, or the snapshot failed to "
                "decode); recording_date for this directory is approximate, derived "
                "from the recording machine's local clock instead of the feed.",
                file=sys.stderr,
            )
        recording_dates.append(recording_date)

        df = match_snapshots(
            snapshot_paths,
            trip_shapes,
            shapes,
            max_perpendicular_dist_m=args.max_perpendicular_dist_m,
            shape_cumulative_dist=shape_cumulative_dist,
            trip_stop_anchors=trip_stop_anchors,
            position_signal_coverage_threshold=args.position_signal_coverage_threshold,
            trip_routes=trip_routes,
        )
        # Scalar broadcast: recording_date identifies which recording SESSION
        # a row came from, not a per-observation calendar date - unlike
        # day_type (FA-5), which is derived per-observation from its own
        # timestamp for a different purpose.
        df["recording_date"] = recording_date

        # FA-12: capability is decided per-directory/day, so print it per directory rather than
        # only an aggregate - multiple --positions-dir values can legitimately differ.
        coverage = df.attrs.get("position_signal_coverage", {"sequence": 0.0, "stop_id": 0.0})
        print(
            f"Position signal (FA-12) for {positions_dir}: {df.attrs.get('position_signal', 'none')} "
            f"(sequence coverage {coverage['sequence'] * 100:.1f}%, "
            f"stop_id coverage {coverage['stop_id'] * 100:.1f}%)"
        )

        for reason, count in df.attrs.get("reject_counts", {}).items():
            total_reject_counts[reason] = total_reject_counts.get(reason, 0) + count
        _merge_route_counts(total_by_route, df.attrs.get("reject_counts_by_route", {}))
        total_unattributable += df.attrs.get("unattributable_observations", 0)
        total_snapshots_processed += df.attrs.get("snapshots_processed", len(snapshot_paths))
        frames.append(df)

    # pd.concat does not preserve .attrs, hence the manual summing above.
    df = pd.concat(frames, ignore_index=True)
    if not df.empty:
        # Concatenating an empty per-directory frame (whose "timestamp" stays
        # object dtype, per matcher.py's own empty-input guard) with a
        # non-empty one can degrade the combined column's dtype - normalize
        # once on the concatenated frame rather than relying on per-directory
        # dtype survival.
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)

    out_path = Path(args.out)
    try:
        if out_path.suffix.lower() == ".parquet":
            df.to_parquet(out_path)
        else:
            df.to_csv(out_path, index=False)
    except ImportError as exc:
        print(
            f"Could not write Parquet output ({exc}). Install pyarrow "
            "(pip install pyarrow) or use a .csv output path instead.",
            file=sys.stderr,
        )
        return 1

    total_rejected = sum(total_reject_counts.values())
    print(f"Agency timezone resolved: {agency_tz}")
    print(f"Directories merged: {len(positions_dirs)}")
    print(f"Recording date range: {min(recording_dates)} to {max(recording_dates)}")
    print(f"Snapshots processed: {total_snapshots_processed}")
    print(f"Observations matched: {len(df)}")
    print(f"Observations rejected: {total_rejected}")
    for reason in ("unknown_shape", "too_far_from_route", "no_trip_id", "corrupt_snapshot"):
        print(f"  - {reason}: {total_reject_counts.get(reason, 0)}")
    print(f"Fallback shapes used (shapes.txt missing): {'yes' if fallback_used else 'no'}")
    print(f"Shapes trustworthy for shape_dist_traveled (FA-10): {len(shape_cumulative_dist)}/{len(shape_dist_raw)}")

    low_yield = _report_match_diagnostics(
        total_by_route,
        total_unattributable,
        total_reject_counts,
        accepted=len(df),
        excluded_route_ids=set(args.exclude_route_id),
        max_reject_share=args.max_reject_share,
        min_observations_for_route_alert=args.min_observations_for_route_alert,
        diagnostics_csv=args.diagnostics_csv,
    )

    print(f"Output written to: {out_path}")
    # The output table is always written first: --fail-on-low-yield is about refusing to let a
    # bad run pass silently, not about withholding the evidence needed to diagnose it.
    if low_yield and args.fail_on_low_yield:
        print(
            "Failing because --fail-on-low-yield was passed and the run was flagged above.",
            file=sys.stderr,
        )
        return 1
    return 0


def _cmd_build(args: argparse.Namespace) -> int:
    matched_path = Path(args.matched)
    try:
        if matched_path.suffix.lower() == ".parquet":
            matched = pd.read_parquet(matched_path)
        else:
            matched = pd.read_csv(matched_path)
    except ImportError as exc:
        print(
            f"Could not read Parquet input ({exc}). Install pyarrow "
            "(pip install pyarrow) or use a .csv input path instead.",
            file=sys.stderr,
        )
        return 1
    except FileNotFoundError:
        print(f"--matched file not found: {matched_path}", file=sys.stderr)
        return 1

    # recording_date (FA-6) is not required here and is not enumerated below -
    # this check only lists hard requirements, it does not reject extra
    # columns. A recording_date column (if present) survives the CSV/Parquet
    # round-trip in a form collect_segment_observations can group by
    # directly (str after CSV, datetime.date after Parquet) with no
    # reparsing needed here.
    missing_cols = [
        col for col in ("trip_id", "timestamp", "distance_along_shape_m") if col not in matched.columns
    ]
    if missing_cols:
        print(
            f"--matched file {matched_path} is missing required column(s): "
            f"{', '.join(missing_cols)} — expected the 'match' subcommand's output table.",
            file=sys.stderr,
        )
        return 1

    if matched_path.suffix.lower() != ".parquet":
        matched["timestamp"] = pd.to_datetime(matched["timestamp"], utc=True)

    static_error = _validate_static_gtfs(
        args.static, required_files=("trips.txt", "stop_times.txt", "stops.txt")
    )
    if static_error:
        print(static_error, file=sys.stderr)
        return 1

    trip_shapes, shapes, fallback_used = resolve_trip_shapes(args.static)
    if fallback_used:
        print(
            "Warning: shapes.txt not found in static GTFS - falling back to "
            "straight-line stop-to-stop shapes (reduced accuracy).",
            file=sys.stderr,
        )

    static_index = load_static_index(args.static)
    stop_locations = load_stop_locations(args.static)
    agency_tz = resolve_agency_timezone(args.static)
    service_day_types = load_service_day_types(args.static)

    # FA-10: use shape_dist_traveled directly for stop anchoring when the static feed
    # is trustworthy (fill-rate + unit-consistency checks) - falls back to today's
    # purely-geometric anchoring when it isn't (empty dicts, no behaviour change).
    shape_dist_raw = load_shape_dist_traveled(args.static)
    shape_cumulative_dist, shape_scale_factor = evaluate_shape_trust(shapes, shape_dist_raw)
    trusted_stop_dist = evaluate_trip_trust(
        static_index, trip_shapes, shape_cumulative_dist, shape_scale_factor
    )

    segment_times, collect_counts = collect_segment_observations(
        matched, static_index, trip_shapes, shapes, stop_locations, agency_tz,
        args.time_bucket_minutes,
        shape_cumulative_dist=shape_cumulative_dist,
        trusted_stop_dist=trusted_stop_dist,
        max_bracket_gap_s=args.max_bracket_gap_seconds,
    )
    segment_times, dropped_count = filter_min_observations(
        segment_times, args.min_observations_per_segment
    )
    p50_stats, p85_stats = aggregate_segments(segment_times)

    # FA-15: per-route split of corrected/gap, taken from the P50 pass only - P85 rebuilds the
    # same trips against the same segment keys, so counting it too would just double every number.
    route_counts: dict[str, dict[str, int]] = {}
    corrections_p50, corrected_count, gap_count = rebuild_stop_times(
        static_index, p50_stats, service_day_types, args.time_bucket_minutes,
        route_counts=route_counts,
    )
    corrections_p85, _corrected_p85, _gap_p85 = rebuild_stop_times(
        static_index, p85_stats, service_day_types, args.time_bucket_minutes
    )

    out_p50 = f"{args.out_prefix}_p50.zip"
    out_p85 = f"{args.out_prefix}_p85.zip"
    repackage_gtfs(args.static, out_p50, corrections_p50)
    repackage_gtfs(args.static, out_p85, corrections_p85)

    print(f"Agency timezone resolved: {agency_tz}")
    print(f"Trips processed: {collect_counts['trips_processed']}")
    print(f"  - skipped (no resolvable shape or fewer than 2 stops): {collect_counts['trips_skipped_unresolvable']}")
    print(f"Segment observations collected: {collect_counts['segments_observed']}")
    print(f"  - interpolation gaps (vehicle not observed at that point of the route): {collect_counts['interpolation_gaps']}")
    print(
        f"    - bracket time-gap rejections (> {args.max_bracket_gap_seconds:.0f}s between the two "
        f"real GPS observations used to interpolate a crossing; counts individual crossing "
        f"attempts, not stop pairs, so this can exceed the interpolation gaps count above): "
        f"{collect_counts['bracket_gap_rejected']}"
    )
    print(f"  - missing stop location (stop_id absent from stops.txt): {collect_counts['missing_stop_location']}")
    print(f"  - rejected (implausible segment time or speed, FA-13): {collect_counts['rejected_seg_time']}")
    print(f"Segments dropped (fewer than {args.min_observations_per_segment} observations): {dropped_count}")
    print(f"Segments corrected: {corrected_count}")
    # gap_count spans every trip in the static feed (all routes, all service
    # days) since rebuild_stop_times always rebuilds the whole schedule - it
    # is dominated by trips the recording never touched at all, so it is not
    # a useful measure of "how well did this recording go" on its own; use
    # segments_observed/interpolation_gaps above for that.
    print(f"Segments as gap across the full static schedule (kept scheduled time): {gap_count}")
    print(f"Fallback shapes used (shapes.txt missing): {'yes' if fallback_used else 'no'}")
    trusted_trip_count = len({trip_id for trip_id, _seq in trusted_stop_dist})
    print(f"Shapes trustworthy for shape_dist_traveled (FA-10): {len(shape_cumulative_dist)}/{len(shape_dist_raw)}")
    print(f"Trips using shape_dist_traveled for stop anchoring (FA-10): {trusted_trip_count}/{len(static_index.trip_stops)}")

    # FA-15: which routes this run actually observed - the only defensible denominator for "did
    # this build produce anything". rebuild_stop_times walks the WHOLE static feed (every route,
    # every service day), so measuring against it would mostly measure how long the static feed's
    # validity window is, not how the recording went.
    observed_routes = {
        static_index.trip_route[trip_id][0]
        for trip_id in matched["trip_id"].unique()
        if trip_id in static_index.trip_route
    }
    low_yield = _report_build_diagnostics(
        route_counts,
        observed_routes,
        min_corrected_route_share=args.min_corrected_route_share,
        diagnostics_csv=args.diagnostics_csv,
    )

    print(f"P50 output written to: {out_p50}")
    print(f"P85 output written to: {out_p85}")
    if low_yield and args.fail_on_low_yield:
        print(
            "Failing because --fail-on-low-yield was passed and the build was flagged above.",
            file=sys.stderr,
        )
        return 1
    return 0


class _AccumulateDirs(argparse.Action):
    """Accumulate --positions-dir values across repeated flag occurrences, on
    top of nargs="+"'s own accumulation of multiple values within a single
    occurrence - so both '--positions-dir a b' and
    '--positions-dir a --positions-dir b' produce ["a", "b"].

    Plain nargs="+" with argparse's default 'store' action instead OVERWRITES
    the whole value on a second occurrence of the flag - '--positions-dir a
    --positions-dir b' would silently end up with only ["b"], silently
    dropping "a" with no error. This is exactly the style shown in FA-6's own
    acceptance criteria (docs/prd/PR_easy-OTP_v07.md), so it must not be a
    silent trap.
    """

    def __call__(self, parser, namespace, values, option_string=None):
        existing = getattr(namespace, self.dest, None) or []
        setattr(namespace, self.dest, existing + list(values))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="family_a",
        description="Family A: reconstruct an observed GTFS from GTFS-RT VehiclePositions.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_record = sub.add_parser(
        "record",
        help="Poll a VehiclePositions feed and archive raw snapshots + manifest",
        epilog=(
            "Example:\n"
            "  py -m family_a.cli record --url https://mkuran.pl/gtfs/warsaw/vehicles.pb "
            "--out-dir recordings/ --duration-min 90 --interval-sec 30\n\n"
            "Recording duration is capped at 1500 minutes (25h) per session. For multi-day\n"
            "coverage, run 'record' once per day into a separate --out-dir, then merge with\n"
            "'match --positions-dir day1 day2 ...' (see README)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_record.add_argument("--url", required=True, help="VehiclePositions .pb feed URL")
    p_record.add_argument("--out-dir", required=True, help="Directory to write snapshots into")
    p_record.add_argument(
        "--duration-min",
        type=_duration_minutes,
        default=60,
        help="Recording duration in minutes (max 1500 = 25h)",
    )
    p_record.add_argument("--interval-sec", type=_positive_int, default=60, help="Polling interval in seconds")
    p_record.set_defaults(func=_cmd_record)

    p_match = sub.add_parser(
        "match",
        help="Map-match VehiclePosition snapshots onto GTFS shapes",
        epilog=(
            "Example:\n"
            "  py -m family_a.cli match --positions-dir recordings/ --static gtfs.zip "
            "--out matched.csv\n\n"
            "Multi-day example (merges several single-day recordings into one table):\n"
            "  py -m family_a.cli match --positions-dir day1_recording day2_recording "
            "day3_recording --static gtfs.zip --out matched_multiday.csv\n\n"
            "--positions-dir may also be repeated instead of space-separated - both "
            "accumulate into the same list, e.g.:\n"
            "  py -m family_a.cli match --positions-dir day1_recording --positions-dir "
            "day2_recording --static gtfs.zip --out matched_multiday.csv"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_match.add_argument(
        "--positions-dir",
        required=True,
        nargs="+",
        action=_AccumulateDirs,
        help=(
            "One or more FA-1 archive dirs containing snapshot_*.pb - space-separate for a "
            "multi-day merge (--positions-dir day1 day2 day3), or repeat the flag "
            "(--positions-dir day1 --positions-dir day2 day3); both accumulate. Each "
            "directory's recording_date is derived from its own snapshots' GTFS-RT feed "
            "timestamp (converted to the static feed's agency_timezone), never from the "
            "directory name or the recording machine's clock; falls back to snapshot "
            "filenames (with a warning) only if a directory has no usable feed timestamp."
        ),
    )
    p_match.add_argument("--static", required=True, help="Static GTFS zip path")
    p_match.add_argument(
        "--out",
        required=True,
        help="Output table path (.csv, or .parquet if pyarrow/fastparquet is installed)",
    )
    p_match.add_argument(
        "--max-perpendicular-dist-m",
        type=float,
        default=100.0,
        help="Reject matches farther than this from the route, in metres (default: 100)",
    )
    p_match.add_argument(
        "--exclude-route-id",
        action="append",
        default=[],
        metavar="ROUTE_ID",
        help=(
            "Drop this route_id's trips before matching (repeatable). For a feed where a "
            "whole route/agency's real-time trip_id isn't a reliable one-trip-per-day "
            "identifier - e.g. Bucharest's Metrorex metro (route_id 968-971,999), whose "
            "trip_id recurs for unrelated departures hours apart, corrupting distance-along-"
            "shape sequences into false multi-hour 'trips'. Excluded trips simply produce no "
            "matched rows, so 'build' passes their static schedule through unchanged - see "
            "matcher.load_trip_shape_index's docstring."
        ),
    )
    p_match.add_argument(
        "--position-signal-coverage-threshold",
        type=float,
        default=DEFAULT_POSITION_SIGNAL_COVERAGE_THRESHOLD,
        help=(
            "FA-12: fraction (0-1) of a day's VehiclePosition entities (with a non-empty "
            "trip_id) that must carry a usable current_stop_sequence, or failing that "
            "stop_id, before that whole day is treated as having 'capability' for windowed "
            "live-position matching - narrowing each observation's map-matching search to "
            "the segment between its neighboring scheduled stops instead of the whole route "
            f"(default: {DEFAULT_POSITION_SIGNAL_COVERAGE_THRESHOLD})."
        ),
    )
    p_match.add_argument(
        "--max-reject-share",
        type=float,
        default=DEFAULT_MAX_REJECT_SHARE,
        help=(
            # NOTE: argparse %-formats help strings, so every literal percent sign here must be
            # written %% or it is silently parsed as a format spec (which mangled this very
            # option's help into a raw dict dump until it was caught by running --help).
            "FA-15: warn when more than this fraction (0-1) of a run's observations are "
            "rejected. The classic cause is a static feed from a different publication period "
            "than the recording, whose trip_id namespace does not match (Poznań 2026-07-17: "
            f"79.19%% rejected, against 0.97-11.83%% on healthy city-days; default: "
            f"{DEFAULT_MAX_REJECT_SHARE})."
        ),
    )
    p_match.add_argument(
        "--min-observations-for-route-alert",
        type=_positive_int,
        default=DEFAULT_MIN_OBSERVATIONS_FOR_ROUTE_ALERT,
        help=(
            "FA-15: how many observations a route needs before a '100%% rejected' verdict is "
            "reported for it, so a route glimpsed once or twice does not generate noise "
            f"(default: {DEFAULT_MIN_OBSERVATIONS_FOR_ROUTE_ALERT})."
        ),
    )
    p_match.add_argument(
        "--diagnostics-csv",
        default=None,
        metavar="PATH",
        help=(
            "FA-15: also write a per-route breakdown (observations, accepted, unknown_shape, "
            "too_far_from_route, rejected_share) to this path. Off by default."
        ),
    )
    p_match.add_argument(
        "--fail-on-low-yield",
        action="store_true",
        help=(
            "FA-15: exit non-zero when the run is flagged as low-yield. Off by default so an "
            "automated daily pipeline keeps running and reporting rather than halting on a "
            "problem it cannot fix on its own."
        ),
    )
    p_match.set_defaults(func=_cmd_match)

    p_build = sub.add_parser(
        "build",
        help="Reconstruct a realized GTFS (P50/P85) from matched positions",
        epilog=(
            "Example:\n"
            "  py -m family_a.cli build --matched matched.csv --static gtfs.zip "
            "--out-prefix realized"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_build.add_argument(
        "--matched", required=True, help="FA-2 matched positions table (.csv or .parquet)"
    )
    p_build.add_argument(
        "--static", required=True, help="Static GTFS zip path (same one used in 'match')"
    )
    p_build.add_argument(
        "--out-prefix",
        required=True,
        help="Writes <out-prefix>_p50.zip and <out-prefix>_p85.zip",
    )
    p_build.add_argument(
        "--min-observations-per-segment",
        type=_positive_int,
        default=2,
        help="Minimum observed travel times required to trust a segment (default: 2)",
    )
    p_build.add_argument(
        "--time-bucket-minutes",
        type=_positive_int,
        default=120,
        help=(
            "Time-of-day bucket width in minutes for segment correction scoping "
            "(default: 120, i.e. 2-hour blocks)."
        ),
    )
    p_build.add_argument(
        "--max-bracket-gap-seconds",
        type=float,
        default=DEFAULT_MAX_BRACKET_GAP_S,
        help=(
            "Reject an interpolated stop crossing when its bracketing pair of real GPS "
            "observations is spaced further apart in time than this many seconds - a wide "
            "gap means sparse sampling, not a real travel time (FA-14, PRD §7 open "
            f"question #10; default: {DEFAULT_MAX_BRACKET_GAP_S:.0f})."
        ),
    )
    p_build.add_argument(
        "--min-corrected-route-share",
        type=float,
        default=DEFAULT_MIN_CORRECTED_ROUTE_SHARE,
        help=(
            "FA-15: warn when fewer than this fraction (0-1) of the routes actually observed in "
            "the matched table end up with any corrected segment - the signature of a build that "
            "is mostly just the static schedule and will read as near-perfect punctuality "
            "downstream (Turin 2026-07-20: 217 corrected rows out of 1,416,230). The default "
            f"({DEFAULT_MIN_CORRECTED_ROUTE_SHARE}) is a documented starting point, NOT yet "
            "validated against that Turin-class case on real data - see the PRD's open "
            "question #11."
        ),
    )
    p_build.add_argument(
        "--diagnostics-csv",
        default=None,
        metavar="PATH",
        help=(
            "FA-15: also write a per-route breakdown (corrected_segments, gap_segments, "
            "corrected_share) to this path. Off by default."
        ),
    )
    p_build.add_argument(
        "--fail-on-low-yield",
        action="store_true",
        help=(
            "FA-15: exit non-zero when the build is flagged as low-yield. Off by default, same "
            "rationale as 'match' - both realized GTFS zips are always written first regardless."
        ),
    )
    p_build.set_defaults(func=_cmd_build)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
