"""gtfs_static_vs_realized_diff.py - per-stop_times delay between a static
GTFS feed and its Family A "realized" reconstruction.

Standalone ANALYSIS tooling. NOT part of the easy-OTP plugin and not imported
by it. Pure stdlib (zipfile + csv + statistics) for the diff itself; the
chart step additionally needs `matplotlib` (see `tools/analysis/requirements.txt`
- deliberately NOT added to `tools/family_a_reconstruction/requirements.txt`,
which is also installed on the Termux phone via TX-1; matplotlib has no
prebuilt wheels for Android's Bionic libc and is only ever needed where the
chart is rendered, i.e. a GitHub Actions runner or your own machine, never the
phone). No QGIS needed either way.

CLI, run with e.g.:
    py gtfs_static_vs_realized_diff.py --static warsaw.zip --realized warsaw_realized_p50.zip --out-prefix out/warsaw_2026-07-15_p50

--- The "no delays in static" problem, and how this script resolves it -----
Static GTFS has no delay field - a scheduled departure_time is just a plan.
Family A (tools/family_a_reconstruction, see its README) does not add a
delay field either: its `build` step rewrites stop_times.txt in place,
producing a feed that is byte-identical to the original static feed except
for corrected arrival_time/departure_time values on stop-to-stop segments a
recording actually observed vehicles crossing. Everywhere else, it keeps the
original scheduled time exactly (a "gap").

That is what makes this comparison possible without any RT-specific fields:
trip_id, stop_id and stop_sequence are preserved unchanged between the
static feed and its realized reconstruction (confirmed in the README's
"Verifying the result" section), so every stop_times.txt row has a matching
counterpart in both files. "Delay" here is simply defined as

    delay = realized_departure_time - static_departure_time

per (trip_id, stop_id, stop_sequence). This is exactly the quantity GTFS-RT
TripUpdates would have given you directly (if the source city published
one) - Family A exists for cities that don't (Warszawa, Wroclaw, Lodz; see
KNOWN_ISSUES.md #13), and reconstructs an equivalent static-shaped feed from
VehiclePositions instead.

CAVEAT - a delay of exactly 0 is ambiguous. It means either "the vehicle
really ran on schedule at that stop" or "the recording never observed that
segment, so the original scheduled time was kept unchanged (a gap)". This
script cannot tell those two apart from the GTFS files alone - only
`family_a.cli build`'s own console output (its "Segments corrected" vs
"Segments dropped" counts) can. Keep that context in mind when reading a
0.00 delay in the detail CSV. The chart step drops delay==0 rows for exactly
this reason (see plot_mean_delay's docstring); the detail/summary CSVs do not.

`family_a.cli build` writes two variants, `<prefix>_p50.zip` (median observed
segment time) and `<prefix>_p85.zip` (85th percentile / pessimistic) - point
--realized at whichever one you want to analyze; run again with the other
path (and a different --out-prefix) to compare both.

Output (all written under --out-prefix):
  - <prefix>_detail.csv - one row per matched stop_times.txt entry -
    route_id, trip_id, stop_sequence, stop_id, scheduled/realized time,
    delay_sec, delay_min. Only written if --detail-csv is passed (opt-in -
    the summary CSV below covers day-to-day monitoring; this is for
    manual, per-row debugging and is large enough per city per day that
    it isn't worth publishing by default).
  - <prefix>_summary.csv - mean / mean(|delay|) / stdev / min / max delay,
    plus count and % of rows actually changed, overall and per route_id.
    Always written.
  - <prefix>_chart.png - mean delay (minutes) vs. scheduled time-of-day,
    bucketed every --chart-bucket-minutes. Rows with delay_sec == 0 are
    excluded from the chart only (a 0 is far more often "never observed"
    than "exactly on time" per the caveat above, and including it would
    just wash the plotted mean toward zero without meaning anything) - the
    detail and summary CSVs above still contain every row, unfiltered. A
    faint grey bar behind the line shows how many non-zero observations
    back each bucket, since a bucket's mean is only as trustworthy as its
    sample count. The x-axis is cropped tightly to the actual measured
    range (the first/last bucket that has any non-zero-delay observation,
    snapped to --chart-bucket-minutes) unless --chart-start-hour/
    --chart-end-hour override it explicitly - a short/partial recording
    session produces a short/partial chart instead of a mostly-empty
    fixed-width one. Skipped (no file written) if there are zero
    non-zero-delay rows, or --no-chart is passed - the CLI reports this on
    stdout either way so a calling script (e.g. a CI step deciding whether
    to `gh release upload` this file) can tell whether it exists without
    guessing. --chart-title-prefix optionally adds a line above the title
    (e.g. a city name and date), since the script itself has no notion of
    either - the caller (e.g. easy-GTFS-RT's per-city build workflow) is
    what knows that context.
"""
from __future__ import annotations

import argparse
import csv
import io
import statistics
import sys
import zipfile
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# GTFS time helpers
# ---------------------------------------------------------------------------

def _parse_gtfs_time(value: str) -> int:
    """GTFS times are H:MM:SS / HH:MM:SS, H allowed to exceed 23 (after-midnight
    trips) - NOT a wall-clock time, seconds-since-midnight-of-service-day only.
    """
    parts = value.strip().split(":")
    if len(parts) != 3:
        raise ValueError(f"Not a valid GTFS time string: {value!r}")
    h, m, s = (int(p) for p in parts)
    return h * 3600 + m * 60 + s


def _seconds_to_hhmmss(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _minutes_to_hhmm(minutes: float) -> str:
    total = int(round(minutes)) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


# ---------------------------------------------------------------------------
# GTFS zip readers
# ---------------------------------------------------------------------------

def _require_zip_member(zip_path: str, member: str) -> None:
    path = Path(zip_path)
    if not path.is_file():
        raise RuntimeError(f"GTFS zip not found: {zip_path}")
    with zipfile.ZipFile(path) as z:
        names = {n.split("/")[-1] for n in z.namelist()}
        if member not in names:
            raise RuntimeError(f"{zip_path} does not contain '{member}'.")


def _open_member(zip_path: str, member: str):
    z = zipfile.ZipFile(zip_path)
    name = next(n for n in z.namelist() if n.split("/")[-1] == member)
    return io.TextIOWrapper(z.open(name), encoding="utf-8-sig")


def _read_stop_times(zip_path: str, time_field: str) -> dict:
    """Returns {(trip_id, stop_id, stop_sequence): seconds_since_midnight}."""
    _require_zip_member(zip_path, "stop_times.txt")
    result = {}
    skipped_blank = 0
    with _open_member(zip_path, "stop_times.txt") as f:
        reader = csv.DictReader(f)
        if time_field not in (reader.fieldnames or []):
            raise RuntimeError(
                f"{zip_path}: stop_times.txt has no column '{time_field}'. "
                f"Available columns: {reader.fieldnames}"
            )
        for row in reader:
            raw_time = (row.get(time_field) or "").strip()
            if not raw_time:
                skipped_blank += 1
                continue
            key = (row["trip_id"], row["stop_id"], int(row["stop_sequence"]))
            result[key] = _parse_gtfs_time(raw_time)
    if skipped_blank:
        print(f"  ({zip_path}: {skipped_blank} stop_times row(s) skipped - blank '{time_field}'.)")
    return result


def _read_trip_route_map(zip_path: str) -> dict:
    """Returns {trip_id: route_id} from trips.txt."""
    _require_zip_member(zip_path, "trips.txt")
    mapping = {}
    with _open_member(zip_path, "trips.txt") as f:
        reader = csv.DictReader(f)
        for row in reader:
            mapping[row["trip_id"]] = row.get("route_id", "")
    return mapping


# ---------------------------------------------------------------------------
# Core diff
# ---------------------------------------------------------------------------

# One detail row: (route_id, trip_id, stop_sequence, stop_id, static_sec, realized_sec, delay_sec)
DetailRow = tuple[str, str, int, str, int, int, int]


def build_diff(static_zip: str, realized_zip: str, delay_time_field: str = "departure_time") -> list[DetailRow]:
    """Join static and realized stop_times.txt on (trip_id, stop_id, stop_sequence).

    Raises RuntimeError (with a message suitable for printing directly, no
    traceback needed) if the two feeds share no matching rows at all.
    """
    print(f"Reading static feed:   {static_zip}")
    static_times = _read_stop_times(static_zip, delay_time_field)
    print(f"Reading realized feed: {realized_zip}")
    realized_times = _read_stop_times(realized_zip, delay_time_field)
    trip_route_map = _read_trip_route_map(static_zip)

    matched_keys = set(static_times) & set(realized_times)
    static_only = set(static_times) - set(realized_times)
    realized_only = set(realized_times) - set(static_times)

    print(f"stop_times.txt rows: static={len(static_times)}, realized={len(realized_times)}, matched={len(matched_keys)}")
    if static_only:
        print(
            f"WARNING: {len(static_only)} row(s) present in STATIC only "
            "(no matching trip_id/stop_id/stop_sequence in the realized feed). "
            "If this count is large, --realized may not have been built from --static."
        )
    if realized_only:
        print(
            f"WARNING: {len(realized_only)} row(s) present in REALIZED only "
            "(no matching row in the static feed)."
        )
    if not matched_keys:
        raise RuntimeError(
            "No matching stop_times.txt rows between the two feeds - nothing to "
            "diff. Check that --realized was built from --static "
            "(family_a.cli build --static <this same static.zip>)."
        )

    detail_rows: list[DetailRow] = []
    for trip_id, stop_id, stop_sequence in matched_keys:
        static_sec = static_times[(trip_id, stop_id, stop_sequence)]
        realized_sec = realized_times[(trip_id, stop_id, stop_sequence)]
        delay_sec = realized_sec - static_sec
        route_id = trip_route_map.get(trip_id, "")
        detail_rows.append((route_id, trip_id, stop_sequence, stop_id, static_sec, realized_sec, delay_sec))

    detail_rows.sort(key=lambda r: (r[0], r[1], r[2]))
    return detail_rows


def write_detail_csv(detail_rows: list[DetailRow], out_path: str, delay_time_field: str) -> None:
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "route_id", "trip_id", "stop_sequence", "stop_id",
            f"static_{delay_time_field}", f"realized_{delay_time_field}",
            "delay_sec", "delay_min",
        ])
        for route_id, trip_id, stop_sequence, stop_id, static_sec, realized_sec, delay_sec in detail_rows:
            w.writerow([
                route_id, trip_id, stop_sequence, stop_id,
                _seconds_to_hhmmss(static_sec), _seconds_to_hhmmss(realized_sec),
                delay_sec, round(delay_sec / 60.0, 2),
            ])
    print(f"Detail CSV written: {out_path} ({len(detail_rows)} rows)")


def _summarize_one(delays_sec: list[int]) -> dict:
    n = len(delays_sec)
    abs_delays = [abs(d) for d in delays_sec]
    changed = sum(1 for d in delays_sec if d != 0)
    return {
        "n": n,
        "n_changed": changed,
        "pct_changed": (changed / n * 100.0) if n else 0.0,
        "mean_delay_sec": statistics.mean(delays_sec),
        "mean_abs_delay_sec": statistics.mean(abs_delays),
        "stdev_delay_sec": statistics.pstdev(delays_sec) if n > 1 else 0.0,
        "min_delay_sec": min(delays_sec),
        "max_delay_sec": max(delays_sec),
    }


def summarize(detail_rows: list[DetailRow]) -> list[tuple[str, dict]]:
    """Returns [(route_id, stats), ..., ("ALL", stats)]."""
    by_route: dict[str, list[int]] = {}
    for route_id, _trip_id, _stop_seq, _stop_id, _static_sec, _realized_sec, delay_sec in detail_rows:
        by_route.setdefault(route_id, []).append(delay_sec)
    all_delays = [row[6] for row in detail_rows]

    summary_rows = [(route_id, _summarize_one(delays)) for route_id, delays in sorted(by_route.items())]
    summary_rows.append(("ALL", _summarize_one(all_delays)))
    return summary_rows


def write_summary_csv(summary_rows: list[tuple[str, dict]], out_path: str) -> None:
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "route_id", "n_rows", "n_changed", "pct_changed",
            "mean_delay_sec", "mean_abs_delay_sec", "stdev_delay_sec",
            "min_delay_sec", "max_delay_sec",
        ])
        for route_id, stats in summary_rows:
            w.writerow([
                route_id, stats["n"], stats["n_changed"], round(stats["pct_changed"], 2),
                round(stats["mean_delay_sec"], 2), round(stats["mean_abs_delay_sec"], 2),
                round(stats["stdev_delay_sec"], 2), stats["min_delay_sec"], stats["max_delay_sec"],
            ])
    print(f"Summary CSV written: {out_path}")

    print("\n--- Summary (delay = realized - static, seconds) ---")
    for route_id, stats in summary_rows:
        label = f"route={route_id}" if route_id != "ALL" else "ALL routes"
        print(
            f"  {label}: n={stats['n']} ({stats['n_changed']} changed, {stats['pct_changed']:.1f}%), "
            f"mean={stats['mean_delay_sec']:.1f}s, mean_abs={stats['mean_abs_delay_sec']:.1f}s, "
            f"stdev={stats['stdev_delay_sec']:.1f}s, min={stats['min_delay_sec']}s, max={stats['max_delay_sec']}s"
        )
    print(
        "\nReminder: a 0s delay can mean either 'on schedule' or 'never observed, kept as "
        "scheduled (gap)' - see this script's module docstring."
    )


def plot_mean_delay(
    detail_rows: list[DetailRow],
    out_path: str,
    bucket_minutes: int,
    start_hour: Optional[int],
    end_hour: Optional[int],
    tick_interval_minutes: int,
    tick_label_rotation: int,
    line_color: str,
    bar_color: str,
    title_prefix: Optional[str] = None,
) -> bool:
    """Mean delay (minutes) vs. scheduled time-of-day, bucketed. Rows with
    delay_sec == 0 are dropped BEFORE bucketing (see module docstring: a 0
    is overwhelmingly "never observed" rather than a real on-time
    measurement, and including it would just drag every bucket's mean
    toward zero without that meaning anything).

    Returns True if a chart file was written, False if skipped (nothing to
    plot) - callers (e.g. a CI step deciding whether to `gh release upload`
    this file) should check this rather than assuming out_path always exists.

    The x-axis is cropped to [start_hour, end_hour] only when those are
    explicitly given; otherwise it's cropped to the actual first/last
    bucket that has data (snapped to bucket_minutes) instead of matplotlib's
    default auto-margin, so a partial-day recording doesn't produce a chart
    that's mostly empty space out to a fixed nominal window.

    Raises ImportError with a clear pip-install hint if matplotlib is not
    installed - only reached if the caller didn't pass --no-chart.
    """
    nonzero_rows = [row for row in detail_rows if row[6] != 0]
    if not nonzero_rows:
        print(
            "\nNo non-zero-delay rows to chart - every matched stop_times row had "
            "delay_sec == 0 (either everything ran exactly on schedule, or nothing "
            "in the recording actually corrected these rows). Skipping chart."
        )
        return False

    start_minute = start_hour * 60 if start_hour is not None else None
    end_minute = end_hour * 60 if end_hour is not None else None
    filtered_rows = []
    for row in nonzero_rows:
        _route_id, _trip_id, _stop_seq, _stop_id, static_sec, _realized_sec, _delay_sec = row
        static_min = static_sec / 60.0
        if start_minute is not None and static_min < start_minute:
            continue
        if end_minute is not None and static_min > end_minute:
            continue
        filtered_rows.append(row)

    if not filtered_rows:
        print(
            f"\nNo non-zero-delay rows fall inside the configured chart window "
            f"({start_hour}:00-{end_hour}:00). Skipping chart."
        )
        return False

    buckets: dict[int, list[float]] = {}
    for _route_id, _trip_id, _stop_seq, _stop_id, static_sec, _realized_sec, delay_sec in filtered_rows:
        static_min = static_sec / 60.0
        bucket_start = int(static_min // bucket_minutes) * bucket_minutes
        buckets.setdefault(bucket_start, []).append(delay_sec / 60.0)

    bucket_starts = sorted(buckets)
    mean_delays = [statistics.mean(buckets[b]) for b in bucket_starts]
    counts = [len(buckets[b]) for b in bucket_starts]

    try:
        import matplotlib
        matplotlib.use("Agg")  # headless
        import matplotlib.pyplot as plt
        import matplotlib.ticker as mticker
    except ImportError as exc:
        raise ImportError(
            "matplotlib is required for the chart (pip install -r "
            "tools/analysis/requirements.txt, or pip install matplotlib). "
            "Pass --no-chart to skip chart generation and keep only the CSVs."
        ) from exc

    fig, ax = plt.subplots(figsize=(12, 6))

    ax2 = ax.twinx()
    ax2.bar(bucket_starts, counts, width=bucket_minutes * 0.9, align="edge",
            color=bar_color, alpha=0.25, zorder=1)
    ax2.set_ylabel("Observations per bucket (non-zero delay rows)", color=bar_color)
    ax2.tick_params(axis="y", labelcolor=bar_color)

    ax.plot(
        [b + bucket_minutes / 2 for b in bucket_starts], mean_delays,
        color=line_color, marker="o", markersize=4, linewidth=1.5, zorder=2,
    )
    ax.axhline(0, color="black", linewidth=0.8, alpha=0.5)
    ax.set_zorder(ax2.get_zorder() + 1)
    ax.patch.set_visible(False)

    ax.set_xlabel("Scheduled time")
    ax.set_ylabel("Mean delay (min, realized minus static)")
    title = (
        f"Mean delay by scheduled time ({bucket_minutes}-min buckets, "
        "zero-delay rows excluded)"
    )
    if title_prefix:
        title = f"{title_prefix}\n{title}"
    ax.set_title(title)
    # Default (no explicit --chart-start-hour/--chart-end-hour) crops tightly to
    # the actual measured data - the first/last bucket that has any observation -
    # rather than leaving it to matplotlib's auto-margin, which would otherwise
    # pad a partial-day recording out toward whatever range happened to be passed
    # in via the (already-filtered) rows, or show unwanted empty space.
    xlim_start = start_minute if start_minute is not None else bucket_starts[0]
    xlim_end = end_minute if end_minute is not None else bucket_starts[-1] + bucket_minutes
    ax.set_xlim(xlim_start, xlim_end)
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, pos: _minutes_to_hhmm(x)))
    ax.xaxis.set_major_locator(mticker.MultipleLocator(tick_interval_minutes))
    ax.tick_params(axis="x", rotation=tick_label_rotation)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(
        f"Chart saved to {out_path} ({len(filtered_rows)} non-zero rows, "
        f"{len(bucket_starts)} buckets, window "
        f"{_minutes_to_hhmm(xlim_start)}-{_minutes_to_hhmm(xlim_end)})"
    )
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Diff a static GTFS feed against its Family A realized reconstruction "
            "(<prefix>_p50.zip / <prefix>_p85.zip from family_a.cli build)."
        ),
    )
    parser.add_argument("--static", required=True, help="Static GTFS .zip path.")
    parser.add_argument("--realized", required=True, help="Family A realized GTFS .zip path (built from --static).")
    parser.add_argument(
        "--out-prefix", required=True,
        help="Writes <prefix>_summary.csv, (if --detail-csv) <prefix>_detail.csv, and (unless --no-chart) <prefix>_chart.png.",
    )
    parser.add_argument(
        "--delay-time-field", default="departure_time", choices=["departure_time", "arrival_time"],
        help="Which stop_times.txt column to diff (default: departure_time).",
    )
    parser.add_argument("--detail-csv", action="store_true", help="Also write <prefix>_detail.csv (one row per matched stop_times entry). Off by default - the summary CSV is always written regardless.")
    parser.add_argument("--no-chart", action="store_true", help="Skip PNG chart generation (the summary CSV is always written).")
    parser.add_argument("--chart-bucket-minutes", type=int, default=15, help="Chart x-axis bucket width in minutes (default: 15).")
    parser.add_argument("--chart-start-hour", type=int, default=None, help="First hour shown on the chart (default: auto-cropped to the earliest measured bucket).")
    parser.add_argument("--chart-end-hour", type=int, default=None, help="Last hour shown on the chart (default: auto-cropped to the latest measured bucket).")
    parser.add_argument("--chart-tick-interval-minutes", type=int, default=30, help="Spacing between x-axis ticks, in minutes (default: 30).")
    parser.add_argument("--chart-tick-label-rotation", type=int, default=45, help="Degrees to rotate x-axis tick labels (default: 45).")
    parser.add_argument("--chart-title-prefix", default=None, help="Optional line shown above the chart's title (e.g. 'Lodz — 2026-07-15'). Default: no extra line.")
    parser.add_argument("--chart-line-color", default="tab:red", help="Colour of the mean-delay line (default: tab:red).")
    parser.add_argument("--chart-bar-color", default="grey", help="Colour of the observation-count bars (default: grey).")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    try:
        detail_rows = build_diff(args.static, args.realized, args.delay_time_field)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    summary_path = f"{args.out_prefix}_summary.csv"
    chart_path = f"{args.out_prefix}_chart.png"

    if args.detail_csv:
        write_detail_csv(detail_rows, f"{args.out_prefix}_detail.csv", args.delay_time_field)
    write_summary_csv(summarize(detail_rows), summary_path)

    if args.no_chart:
        print("\n--no-chart passed - skipping chart generation.")
    else:
        try:
            wrote_chart = plot_mean_delay(
                detail_rows, chart_path,
                bucket_minutes=args.chart_bucket_minutes,
                start_hour=args.chart_start_hour,
                end_hour=args.chart_end_hour,
                tick_interval_minutes=args.chart_tick_interval_minutes,
                tick_label_rotation=args.chart_tick_label_rotation,
                line_color=args.chart_line_color,
                bar_color=args.chart_bar_color,
                title_prefix=args.chart_title_prefix,
            )
        except ImportError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        if not wrote_chart:
            print(f"(no {chart_path} written - see message above)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
