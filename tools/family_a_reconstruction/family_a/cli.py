"""Command-line interface for family_a_reconstruction (FA-1).

Standalone CLI — never imported by, and never imports, easy_otp/. Run:

    python -m family_a.cli record --url <VehiclePositions.pb URL> --out-dir <dir> \\
        [--duration-min N] [--interval-sec N]
    python -m family_a.cli match   (not implemented yet — see FA-2)
    python -m family_a.cli build   (not implemented yet — see FA-3)
"""

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

from family_a.recorder import (
    SnapshotFetchError,
    fetch_snapshot,
    snapshot_filename,
    write_manifest,
    write_snapshot,
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


def _cmd_match(args: argparse.Namespace) -> int:
    # PRD prose says "NotImplementedError"; a print + non-zero exit is friendlier
    # CLI UX (no traceback) while still failing loudly, not silently no-op-ing.
    print("'match' is not implemented yet - see FA-2.", file=sys.stderr)
    return 1


def _cmd_build(args: argparse.Namespace) -> int:
    print("'build' is not implemented yet - see FA-3.", file=sys.stderr)
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="family_a",
        description="Family A: reconstruct an observed GTFS from GTFS-RT VehiclePositions.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_record = sub.add_parser(
        "record", help="Poll a VehiclePositions feed and archive raw snapshots + manifest"
    )
    p_record.add_argument("--url", required=True, help="VehiclePositions .pb feed URL")
    p_record.add_argument("--out-dir", required=True, help="Directory to write snapshots into")
    p_record.add_argument("--duration-min", type=_positive_int, default=60, help="Recording duration in minutes")
    p_record.add_argument("--interval-sec", type=_positive_int, default=60, help="Polling interval in seconds")
    p_record.set_defaults(func=_cmd_record)

    p_match = sub.add_parser("match", help="Not implemented yet - see FA-2")
    p_match.set_defaults(func=_cmd_match)

    p_build = sub.add_parser("build", help="Not implemented yet - see FA-3")
    p_build.set_defaults(func=_cmd_build)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
