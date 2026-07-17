"""gtfs_static_vs_realized_diff_grid.py - one figure, N cities, mean delay
(realized vs static) by scheduled time, all sharing the same axis window.

Standalone ANALYSIS tooling. NOT part of the easy-OTP plugin and not imported
by it. Pure stdlib (zipfile + csv + statistics) for the diff itself, plus
matplotlib for the grid chart - same split as gtfs_static_vs_realized_diff.py
and its CONFIG-block sibling gtfs_static_vs_realized_diff_config.py, which
this script deliberately does not import from (see 07527ef's message: the
CONFIG variant and the CLI variant are independent from here on, and this
grid variant follows the same "self-contained, edit-in-place" convention -
the read/parse/diff helpers below are intentional copies, not shared code).

Unlike the other two scripts (one city, one chart, per run), this one takes a
list of cities in the CONFIG block and renders all of them as subplots on a
single figure/PNG, with one shared CHART_START_HOUR/CHART_END_HOUR window and
one shared set of bucket/tick/colour settings applied identically to every
panel - built for side-by-side comparison across cities on the same day,
not for the day-to-day per-city CI chart (that's still
gtfs_static_vs_realized_diff.py, which easy-GTFS-RT's phone-build workflow
depends on and already produces one PNG per city per day).

Input: fill in CITIES below - each entry needs a display name plus a static
GTFS .zip and a Family A realized GTFS .zip (family_a.cli build's
<prefix>_p50.zip or <prefix>_p85.zip) built from that same static feed. Grab
both from the city's `<city>-realized-<date>-phone` GitHub release, e.g.:
    gh release download <city>-realized-<date>-phone -R GISBoost/easy-GTFS-RT \\
        -p "<city>_static_gtfs_<date>.zip" -p "<city>_realized_<date>_p50.zip"

Output: OUTPUT_CHART_PNG_PATH - a single PNG, one subplot per CITIES entry,
laid out GRID_ROWS x GRID_COLS (left-to-right, top-to-bottom, same order as
CITIES). A city whose recording has zero non-zero-delay rows (nothing
matched, or every matched row was an unobserved gap - see the CAVEAT in
gtfs_static_vs_realized_diff.py's module docstring) gets an empty panel with
a "no data" label instead of failing the whole grid.

Edit the CONFIG block below, then run: `py gtfs_static_vs_realized_diff_grid.py`
"""
from __future__ import annotations

import csv
import io
import statistics
import zipfile
from pathlib import Path

# ------------------------------- CONFIG -------------------------------

_GRID_DIR = r"C:\Users\Michal\Desktop\easy-OTP\tools\analysis\output\gtfs_grid_2026-07-16"

CITIES = [
    {"display_name": "Łódź",     "static_zip": rf"{_GRID_DIR}\lodz_static_gtfs_2026-07-16.zip",     "realized_zip": rf"{_GRID_DIR}\lodz_realized_2026-07-16_p50.zip"},
    {"display_name": "Poznań",   "static_zip": rf"{_GRID_DIR}\poznan_static_gtfs_2026-07-16.zip",   "realized_zip": rf"{_GRID_DIR}\poznan_realized_2026-07-16_p50.zip"},
    {"display_name": "Szczecin", "static_zip": rf"{_GRID_DIR}\szczecin_static_gtfs_2026-07-16.zip", "realized_zip": rf"{_GRID_DIR}\szczecin_realized_2026-07-16_p50.zip"},
    {"display_name": "Prague",   "static_zip": rf"{_GRID_DIR}\prague_static_gtfs_2026-07-16.zip",   "realized_zip": rf"{_GRID_DIR}\prague_realized_2026-07-16_p50.zip"},
    {"display_name": "Rome",     "static_zip": rf"{_GRID_DIR}\rome_static_gtfs_2026-07-16.zip",     "realized_zip": rf"{_GRID_DIR}\rome_realized_2026-07-16_p50.zip"},
    {"display_name": "Turin",    "static_zip": rf"{_GRID_DIR}\turin_static_gtfs_2026-07-16.zip",    "realized_zip": rf"{_GRID_DIR}\turin_realized_2026-07-16_p50.zip"},
    {"display_name": "Vilnius",  "static_zip": rf"{_GRID_DIR}\vilnius_static_gtfs_2026-07-16.zip",  "realized_zip": rf"{_GRID_DIR}\vilnius_realized_2026-07-16_p50.zip"},
    {"display_name": "Lisbon",   "static_zip": rf"{_GRID_DIR}\lisbon_static_gtfs_2026-07-16.zip",   "realized_zip": rf"{_GRID_DIR}\lisbon_realized_2026-07-16_p50.zip"},
]

DELAY_TIME_FIELD = "departure_time"   # or "arrival_time" - applied to every city

# Axis window and styling shared identically across every subplot.
CHART_START_HOUR = 6         # first hour shown on every panel; set to None to disable
CHART_END_HOUR = 22          # last hour shown on every panel; set to None to disable
CHART_BUCKET_MINUTES = 30    # width of the scheduled-time buckets on every panel's x-axis
CHART_TICK_INTERVAL_MINUTES = 60    # spacing between x-axis ticks, in minutes
CHART_TICK_LABEL_ROTATION = 45      # degrees to rotate x-axis tick labels
CHART_LINE_COLOR = "tab:red"        # colour of the mean-delay line
CHART_BAR_COLOR = "grey"            # colour of the observation-count bars behind it
CHART_SHARE_Y_AXIS = False   # if True, every panel uses the same mean-delay y-limits
                             # (computed from the worst city) so heights are directly
                             # comparable across cities; set False to let each panel
                             # auto-scale to its own data instead.

GRID_ROWS = 4
GRID_COLS = 2   # GRID_ROWS * GRID_COLS must be >= len(CITIES)

SUPTITLE = "Mean delay by scheduled time — realized vs static (2026-07-16)"
OUTPUT_CHART_PNG_PATH = r"C:\Users\Michal\Desktop\easy-OTP\tools\analysis\output\gtfs_static_vs_realized_grid_2026-07-16-non-comparable.png"

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
        print(f"    ({zip_path}: {skipped_blank} stop_times row(s) skipped - blank '{time_field}'.)")
    return result


def _build_delay_rows(static_zip: str, realized_zip: str) -> list[tuple[int, int]]:
    """Returns [(static_sec, delay_sec), ...] for every matched, non-zero-delay row."""
    static_times = _read_stop_times(static_zip, DELAY_TIME_FIELD)
    realized_times = _read_stop_times(realized_zip, DELAY_TIME_FIELD)
    matched_keys = set(static_times) & set(realized_times)
    print(
        f"    stop_times.txt rows: static={len(static_times)}, "
        f"realized={len(realized_times)}, matched={len(matched_keys)}"
    )
    if not matched_keys:
        return []
    rows = []
    for key in matched_keys:
        static_sec = static_times[key]
        delay_sec = realized_times[key] - static_sec
        if delay_sec != 0:
            rows.append((static_sec, delay_sec))
    return rows


def _bucket(rows: list[tuple[int, int]]) -> tuple[list[int], list[float], list[int]]:
    """rows -> (sorted bucket_starts_min, mean_delay_min per bucket, count per bucket)."""
    start_minute = CHART_START_HOUR * 60 if CHART_START_HOUR is not None else None
    end_minute = CHART_END_HOUR * 60 if CHART_END_HOUR is not None else None

    buckets: dict[int, list[float]] = {}
    for static_sec, delay_sec in rows:
        static_min = static_sec / 60.0
        if start_minute is not None and static_min < start_minute:
            continue
        if end_minute is not None and static_min > end_minute:
            continue
        bucket_start = int(static_min // CHART_BUCKET_MINUTES) * CHART_BUCKET_MINUTES
        buckets.setdefault(bucket_start, []).append(delay_sec / 60.0)

    bucket_starts = sorted(buckets)
    mean_delays = [statistics.mean(buckets[b]) for b in bucket_starts]
    counts = [len(buckets[b]) for b in bucket_starts]
    return bucket_starts, mean_delays, counts


def main() -> int:
    if len(CITIES) > GRID_ROWS * GRID_COLS:
        raise RuntimeError(
            f"{len(CITIES)} cities configured but GRID_ROWS*GRID_COLS = "
            f"{GRID_ROWS * GRID_COLS} - raise GRID_ROWS/GRID_COLS."
        )

    import matplotlib
    matplotlib.use("Agg")  # headless
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker

    per_city = []  # (display_name, bucket_starts, mean_delays, counts) or None on empty
    for city in CITIES:
        print(f"--- {city['display_name']} ---")
        print(f"  static:   {city['static_zip']}")
        print(f"  realized: {city['realized_zip']}")
        rows = _build_delay_rows(city["static_zip"], city["realized_zip"])
        if not rows:
            print("  no non-zero-delay rows - panel will be empty.")
            per_city.append((city["display_name"], [], [], []))
            continue
        bucket_starts, mean_delays, counts = _bucket(rows)
        if not bucket_starts:
            print(
                f"  no non-zero-delay rows fall inside the configured window "
                f"({CHART_START_HOUR}:00-{CHART_END_HOUR}:00) - panel will be empty."
            )
        per_city.append((city["display_name"], bucket_starts, mean_delays, counts))

    start_minute = CHART_START_HOUR * 60 if CHART_START_HOUR is not None else None
    end_minute = CHART_END_HOUR * 60 if CHART_END_HOUR is not None else None

    global_ymin = global_ymax = None
    if CHART_SHARE_Y_AXIS:
        all_means = [m for _, _, means, _ in per_city for m in means]
        if all_means:
            span = max(all_means) - min(all_means)
            pad = span * 0.1 if span else 1.0
            global_ymin, global_ymax = min(all_means) - pad, max(all_means) + pad

    fig, axes = plt.subplots(GRID_ROWS, GRID_COLS, figsize=(6 * GRID_COLS, 4.5 * GRID_ROWS), squeeze=False)

    for idx, (display_name, bucket_starts, mean_delays, counts) in enumerate(per_city):
        row, col = divmod(idx, GRID_COLS)
        ax = axes[row][col]

        if not bucket_starts:
            ax.set_title(f"{display_name} (no data)")
            ax.set_xlim(start_minute or 0, end_minute or 24 * 60)
            ax.grid(True, alpha=0.3)
        else:
            ax2 = ax.twinx()
            ax2.bar(bucket_starts, counts, width=CHART_BUCKET_MINUTES * 0.9, align="edge",
                    color=CHART_BAR_COLOR, alpha=0.25, zorder=1)
            if col == GRID_COLS - 1:
                ax2.set_ylabel("Observations/bucket", color=CHART_BAR_COLOR)
            ax2.tick_params(axis="y", labelcolor=CHART_BAR_COLOR)

            ax.plot(
                [b + CHART_BUCKET_MINUTES / 2 for b in bucket_starts], mean_delays,
                color=CHART_LINE_COLOR, marker="o", markersize=3, linewidth=1.2, zorder=2,
            )
            ax.axhline(0, color="black", linewidth=0.8, alpha=0.5)
            ax.set_zorder(ax2.get_zorder() + 1)
            ax.patch.set_visible(False)
            ax.set_title(display_name)
            if global_ymin is not None:
                ax.set_ylim(global_ymin, global_ymax)

            xlim_start = start_minute if start_minute is not None else bucket_starts[0]
            xlim_end = end_minute if end_minute is not None else bucket_starts[-1] + CHART_BUCKET_MINUTES
            ax.set_xlim(xlim_start, xlim_end)
            ax.grid(True, alpha=0.3)

        if col == 0:
            ax.set_ylabel("Mean delay (min)")
        if row == GRID_ROWS - 1:
            ax.set_xlabel("Scheduled time")
        ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, pos: _minutes_to_hhmm(x)))
        ax.xaxis.set_major_locator(mticker.MultipleLocator(CHART_TICK_INTERVAL_MINUTES))
        ax.tick_params(axis="x", rotation=CHART_TICK_LABEL_ROTATION)

    # Hide unused panels (GRID_ROWS*GRID_COLS > len(CITIES)).
    for idx in range(len(per_city), GRID_ROWS * GRID_COLS):
        row, col = divmod(idx, GRID_COLS)
        axes[row][col].set_visible(False)

    if SUPTITLE:
        fig.suptitle(SUPTITLE, fontsize=14)
    fig.tight_layout(rect=(0, 0, 1, 0.96) if SUPTITLE else None)
    fig.savefig(OUTPUT_CHART_PNG_PATH, dpi=150)
    print(f"\nGrid chart saved to {OUTPUT_CHART_PNG_PATH} ({len(CITIES)} cities, {GRID_ROWS}x{GRID_COLS} grid)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
