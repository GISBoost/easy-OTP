"""Command-line interface for family_a_reconstruction (FA-1, FA-2, FA-3, FA-4).

Standalone CLI — never imported by, and never imports, easy_otp/. Run (all options on one
line - the "\" bash-style continuation shown in older revisions of this docstring breaks on
cmd.exe/PowerShell; see each subcommand's own --help for a copy-pasteable example):

    py -m family_a.cli record --url <VehiclePositions.pb URL> --out-dir <dir> [--duration-min N] [--interval-sec N]
    py -m family_a.cli match --positions-dir <dir> --static <gtfs.zip> --out <table> [--max-perpendicular-dist-m N]
    py -m family_a.cli build --matched <table> --static <gtfs.zip> --out-prefix <prefix> [--min-observations-per-segment N] [--time-bucket-minutes N]
"""

from __future__ import annotations

import argparse
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

import pandas as pd

from family_a.build_gtfs import load_static_index, rebuild_stop_times, repackage_gtfs
from family_a.calendar_scope import load_service_day_types, resolve_agency_timezone
from family_a.matcher import (
    load_stop_locations,
    match_snapshots,
    resolve_trip_shapes,
)
from family_a.recorder import (
    SnapshotFetchError,
    fetch_snapshot,
    snapshot_filename,
    write_manifest,
    write_snapshot,
)
from family_a.segment_stats import (
    aggregate_segments,
    collect_segment_observations,
    filter_min_observations,
)


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


def _cmd_match(args: argparse.Namespace) -> int:
    positions_dir = Path(args.positions_dir)
    snapshot_paths = sorted(positions_dir.glob("snapshot_*.pb"))
    if not snapshot_paths:
        print(f"No snapshot_*.pb files found in {positions_dir}", file=sys.stderr)
        return 1

    static_error = _validate_static_gtfs_for_match(args.static)
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

    df = match_snapshots(
        snapshot_paths, trip_shapes, shapes, max_perpendicular_dist_m=args.max_perpendicular_dist_m
    )

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

    rejects = df.attrs.get("reject_counts", {})
    total_rejected = sum(rejects.values())
    print(f"Snapshots processed: {df.attrs.get('snapshots_processed', len(snapshot_paths))}")
    print(f"Observations matched: {len(df)}")
    print(f"Observations rejected: {total_rejected}")
    for reason in ("unknown_shape", "too_far_from_route", "no_trip_id", "corrupt_snapshot"):
        print(f"  - {reason}: {rejects.get(reason, 0)}")
    print(f"Fallback shapes used (shapes.txt missing): {'yes' if fallback_used else 'no'}")
    print(f"Output written to: {out_path}")
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

    segment_times, collect_counts = collect_segment_observations(
        matched, static_index, trip_shapes, shapes, stop_locations, agency_tz,
        args.time_bucket_minutes,
    )
    segment_times, dropped_count = filter_min_observations(
        segment_times, args.min_observations_per_segment
    )
    p50_stats, p85_stats = aggregate_segments(segment_times)

    corrections_p50, corrected_count, gap_count = rebuild_stop_times(
        static_index, p50_stats, service_day_types, args.time_bucket_minutes
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
    print(f"  - missing stop location (stop_id absent from stops.txt): {collect_counts['missing_stop_location']}")
    print(f"  - rejected (implausible segment time): {collect_counts['rejected_seg_time']}")
    print(f"Segments dropped (fewer than {args.min_observations_per_segment} observations): {dropped_count}")
    print(f"Segments corrected: {corrected_count}")
    # gap_count spans every trip in the static feed (all routes, all service
    # days) since rebuild_stop_times always rebuilds the whole schedule - it
    # is dominated by trips the recording never touched at all, so it is not
    # a useful measure of "how well did this recording go" on its own; use
    # segments_observed/interpolation_gaps above for that.
    print(f"Segments as gap across the full static schedule (kept scheduled time): {gap_count}")
    print(f"Fallback shapes used (shapes.txt missing): {'yes' if fallback_used else 'no'}")
    print(f"P50 output written to: {out_p50}")
    print(f"P85 output written to: {out_p85}")
    return 0


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
            "--out-dir recordings/ --duration-min 90 --interval-sec 30"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_record.add_argument("--url", required=True, help="VehiclePositions .pb feed URL")
    p_record.add_argument("--out-dir", required=True, help="Directory to write snapshots into")
    p_record.add_argument("--duration-min", type=_positive_int, default=60, help="Recording duration in minutes")
    p_record.add_argument("--interval-sec", type=_positive_int, default=60, help="Polling interval in seconds")
    p_record.set_defaults(func=_cmd_record)

    p_match = sub.add_parser(
        "match",
        help="Map-match VehiclePosition snapshots onto GTFS shapes",
        epilog=(
            "Example:\n"
            "  py -m family_a.cli match --positions-dir recordings/ --static gtfs.zip "
            "--out matched.csv"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_match.add_argument(
        "--positions-dir", required=True, help="FA-1 archive dir containing snapshot_*.pb"
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
    p_build.set_defaults(func=_cmd_build)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
