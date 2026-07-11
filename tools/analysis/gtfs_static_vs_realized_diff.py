"""gtfs_static_vs_realized_diff.py - per-stop_times delay between a static
GTFS feed and its Family A "realized" reconstruction.

Standalone ANALYSIS tooling. NOT part of the easy-OTP plugin and not imported
by it. Pure stdlib (zipfile + csv + statistics) - no QGIS needed, run it with
plain `py gtfs_static_vs_realized_diff.py` from any Python 3 interpreter.

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
"Segments dropped" counts, printed when you generated REALIZED_GTFS_ZIP_PATH)
can. Keep that context in mind when reading a 0.00 delay in the detail CSV.

`family_a.cli build` writes two variants, `<prefix>_p50.zip` (median observed
segment time) and `<prefix>_p85.zip` (85th percentile / pessimistic) - point
REALIZED_GTFS_ZIP_PATH at whichever one you want to analyze; run the script
again with the other path (and a different OUTPUT_*_PATH) to compare both.

Input:
  - STATIC_GTFS_ZIP_PATH: the original static GTFS .zip.
  - REALIZED_GTFS_ZIP_PATH: family_a's `<prefix>_p50.zip` or `<prefix>_p85.zip`
    output, built from STATIC_GTFS_ZIP_PATH (same feed - see README's `build`
    usage). Pointing this at an unrelated feed will show up as a large
    "unmatched" count below rather than silently producing nonsense numbers.

Output:
  - OUTPUT_DETAIL_CSV_PATH: one row per matched stop_times.txt entry -
    route_id, trip_id, stop_sequence, stop_id, scheduled/realized time,
    delay_sec, delay_min.
  - OUTPUT_SUMMARY_CSV_PATH: mean / mean(|delay|) / stdev / min / max delay,
    plus count and % of rows actually changed, overall and per route_id.
  - OUTPUT_CHART_PNG_PATH: mean delay (minutes) vs. scheduled time-of-day,
    bucketed every CHART_BUCKET_MINUTES. Rows with delay_sec == 0 are
    excluded from the chart only (a 0 is far more often "never observed"
    than "exactly on time" per the caveat above, and including it would
    just wash the plotted mean toward zero without meaning anything) - the
    detail and summary CSVs above still contain every row, unfiltered. A
    faint grey bar behind the line shows how many non-zero observations
    back each bucket, since a bucket's mean is only as trustworthy as its
    sample count.

Edit the CONFIG block below, then run: `py gtfs_static_vs_realized_diff.py`
"""
from __future__ import annotations

import csv
import io
import statistics
import zipfile
from pathlib import Path

# ------------------------------- CONFIG -------------------------------

STATIC_GTFS_ZIP_PATH = r"C:\Users\Michal\Desktop\easy-OTP\tools\family_a_reconstruction\lodz2.zip"
REALIZED_GTFS_ZIP_PATH = r"C:\Users\Michal\Desktop\easy-OTP\tools\family_a_reconstruction\gtfs-rt\fa6\out_new_lodz2-fa6_p50.zip"

DELAY_TIME_FIELD = "departure_time"   # or "arrival_time"

OUTPUT_DETAIL_CSV_PATH = r"C:\Users\Michal\Desktop\easy-OTP\tools\analysis\output\gtfs_static_vs_realized_diff_detail-fa6.csv"
OUTPUT_SUMMARY_CSV_PATH = r"C:\Users\Michal\Desktop\easy-OTP\tools\analysis\output\gtfs_static_vs_realized_diff_summary-fa6.csv"

CHART_BUCKET_MINUTES = 15   # width of the scheduled-time buckets on the chart's x-axis
CHART_START_HOUR = 9        # first hour shown on the chart; set to None to disable
CHART_END_HOUR = 17         # last hour shown on the chart; set to None to disable
CHART_TICK_INTERVAL_MINUTES = 15   # spacing between x-axis ticks, in minutes
CHART_TICK_LABEL_ROTATION = 45  # degrees to rotate x-axis tick labels (0=horizontal, 90=vertical)
CHART_LINE_COLOR = "tab:red"       # colour of the mean-delay line
CHART_BAR_COLOR = "grey"           # colour of the observation-count bars behind it
OUTPUT_CHART_PNG_PATH = r"C:\Users\Michal\Desktop\easy-OTP\tools\analysis\output\gtfs_static_vs_realized_mean_delay-fa6-9-17-15minute.png"

# ------------------------------------------------------------------------


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


def main():
    print(f"Reading static feed:   {STATIC_GTFS_ZIP_PATH}")
    static_times = _read_stop_times(STATIC_GTFS_ZIP_PATH, DELAY_TIME_FIELD)
    print(f"Reading realized feed: {REALIZED_GTFS_ZIP_PATH}")
    realized_times = _read_stop_times(REALIZED_GTFS_ZIP_PATH, DELAY_TIME_FIELD)
    trip_route_map = _read_trip_route_map(STATIC_GTFS_ZIP_PATH)

    matched_keys = set(static_times) & set(realized_times)
    static_only = set(static_times) - set(realized_times)
    realized_only = set(realized_times) - set(static_times)

    print(f"stop_times.txt rows: static={len(static_times)}, realized={len(realized_times)}, matched={len(matched_keys)}")
    if static_only:
        print(
            f"WARNING: {len(static_only)} row(s) present in STATIC only "
            "(no matching trip_id/stop_id/stop_sequence in the realized feed). "
            "If this count is large, REALIZED_GTFS_ZIP_PATH may not have been "
            "built from STATIC_GTFS_ZIP_PATH."
        )
    if realized_only:
        print(
            f"WARNING: {len(realized_only)} row(s) present in REALIZED only "
            "(no matching row in the static feed)."
        )
    if not matched_keys:
        raise RuntimeError(
            "No matching stop_times.txt rows between the two feeds - nothing to "
            "diff. Check that REALIZED_GTFS_ZIP_PATH was built from "
            "STATIC_GTFS_ZIP_PATH (family_a.cli build --static <this same static.zip>)."
        )

    detail_rows = []  # (route_id, trip_id, stop_sequence, stop_id, static_sec, realized_sec, delay_sec)
    for trip_id, stop_id, stop_sequence in matched_keys:
        static_sec = static_times[(trip_id, stop_id, stop_sequence)]
        realized_sec = realized_times[(trip_id, stop_id, stop_sequence)]
        delay_sec = realized_sec - static_sec
        route_id = trip_route_map.get(trip_id, "")
        detail_rows.append((route_id, trip_id, stop_sequence, stop_id, static_sec, realized_sec, delay_sec))

    detail_rows.sort(key=lambda r: (r[0], r[1], r[2]))

    with open(OUTPUT_DETAIL_CSV_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "route_id", "trip_id", "stop_sequence", "stop_id",
            f"static_{DELAY_TIME_FIELD}", f"realized_{DELAY_TIME_FIELD}",
            "delay_sec", "delay_min",
        ])
        for route_id, trip_id, stop_sequence, stop_id, static_sec, realized_sec, delay_sec in detail_rows:
            w.writerow([
                route_id, trip_id, stop_sequence, stop_id,
                _seconds_to_hhmmss(static_sec), _seconds_to_hhmmss(realized_sec),
                delay_sec, round(delay_sec / 60.0, 2),
            ])
    print(f"Detail CSV written: {OUTPUT_DETAIL_CSV_PATH} ({len(detail_rows)} rows)")

    _write_summary(detail_rows)
    _plot_mean_delay(detail_rows)


def _summarize(delays_sec):
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


def _write_summary(detail_rows):
    by_route = {}
    for route_id, trip_id, stop_sequence, stop_id, static_sec, realized_sec, delay_sec in detail_rows:
        by_route.setdefault(route_id, []).append(delay_sec)
    all_delays = [row[6] for row in detail_rows]

    summary_rows = []
    for route_id, delays in sorted(by_route.items()):
        summary_rows.append((route_id, _summarize(delays)))
    summary_rows.append(("ALL", _summarize(all_delays)))

    with open(OUTPUT_SUMMARY_CSV_PATH, "w", newline="", encoding="utf-8") as f:
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
    print(f"Summary CSV written: {OUTPUT_SUMMARY_CSV_PATH}")

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


def _plot_mean_delay(detail_rows):
    """Mean delay (minutes) vs. scheduled time-of-day, bucketed. Rows with
    delay_sec == 0 are dropped BEFORE bucketing (see module docstring: a 0
    is overwhelmingly "never observed" rather than a real on-time
    measurement, and including it would just drag every bucket's mean
    toward zero without that meaning anything).
    """
    nonzero_rows = [row for row in detail_rows if row[6] != 0]
    if not nonzero_rows:
        print(
            "\nNo non-zero-delay rows to chart - every matched stop_times row had "
            "delay_sec == 0 (either everything ran exactly on schedule, or nothing "
            "in the recording actually corrected these rows). Skipping chart."
        )
        return

    start_minute = CHART_START_HOUR * 60 if CHART_START_HOUR is not None else None
    end_minute = CHART_END_HOUR * 60 if CHART_END_HOUR is not None else None
    filtered_rows = []
    for row in nonzero_rows:
        _route_id, _trip_id, _stop_seq, _stop_id, static_sec, _realized_sec, delay_sec = row
        static_min = static_sec / 60.0
        if start_minute is not None and static_min < start_minute:
            continue
        if end_minute is not None and static_min > end_minute:
            continue
        filtered_rows.append(row)

    if not filtered_rows:
        print(
            f"\nNo non-zero-delay rows fall inside the configured chart window "
            f"({CHART_START_HOUR}:00-{CHART_END_HOUR}:00). Skipping chart."
        )
        return

    buckets = {}  # bucket_start_minutes -> list of delay_min
    for _route_id, _trip_id, _stop_seq, _stop_id, static_sec, _realized_sec, delay_sec in filtered_rows:
        static_min = static_sec / 60.0
        bucket_start = int(static_min // CHART_BUCKET_MINUTES) * CHART_BUCKET_MINUTES
        buckets.setdefault(bucket_start, []).append(delay_sec / 60.0)

    bucket_starts = sorted(buckets)
    mean_delays = [statistics.mean(buckets[b]) for b in bucket_starts]
    counts = [len(buckets[b]) for b in bucket_starts]

    import matplotlib
    matplotlib.use("Agg")  # headless
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker

    fig, ax = plt.subplots(figsize=(12, 6))

    ax2 = ax.twinx()
    ax2.bar(bucket_starts, counts, width=CHART_BUCKET_MINUTES * 0.9, align="edge",
            color=CHART_BAR_COLOR, alpha=0.25, zorder=1)
    ax2.set_ylabel("Observations per bucket (non-zero delay rows)", color=CHART_BAR_COLOR)
    ax2.tick_params(axis="y", labelcolor=CHART_BAR_COLOR)

    ax.plot(
        [b + CHART_BUCKET_MINUTES / 2 for b in bucket_starts], mean_delays,
        color=CHART_LINE_COLOR, marker="o", markersize=4, linewidth=1.5, zorder=2,
    )
    ax.axhline(0, color="black", linewidth=0.8, alpha=0.5)
    ax.set_zorder(ax2.get_zorder() + 1)
    ax.patch.set_visible(False)

    ax.set_xlabel("Scheduled time")
    ax.set_ylabel("Mean delay (min, realized minus static)")
    ax.set_title(
        f"Mean delay by scheduled time ({CHART_BUCKET_MINUTES}-min buckets, "
        "zero-delay rows excluded)"
    )
    if start_minute is not None and end_minute is not None:
        ax.set_xlim(start_minute, end_minute)
    elif start_minute is not None:
        ax.set_xlim(start_minute, None)
    elif end_minute is not None:
        ax.set_xlim(None, end_minute)
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, pos: _minutes_to_hhmm(x)))
    ax.xaxis.set_major_locator(mticker.MultipleLocator(CHART_TICK_INTERVAL_MINUTES))
    ax.tick_params(axis="x", rotation=CHART_TICK_LABEL_ROTATION)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUTPUT_CHART_PNG_PATH, dpi=150)
    print(
        f"Chart saved to {OUTPUT_CHART_PNG_PATH} ({len(filtered_rows)} non-zero rows, "
        f"{len(bucket_starts)} buckets, window {CHART_START_HOUR}:00-{CHART_END_HOUR}:00)"
    )


if __name__ == "__main__":
    main()
