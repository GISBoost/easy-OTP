"""lodz_festival_weekend_overlay.py - one-off comparison of Lodz realized-vs-static
delay between the 2026-07-24..26 weekend (Lodz Summer Festival, held on/near the
Retkinia estate's Blonia Lodzkie meadow) and the preceding, non-festival weekend
(2026-07-17..19).

Standalone ANALYSIS tooling. NOT part of the easy-OTP plugin and not imported by
it. Pure stdlib (zipfile + csv + statistics) for the diff itself, plus matplotlib
for the charts - same split as gtfs_static_vs_realized_diff_grid.py, whose
_read_stop_times/_parse_gtfs_time/_bucket helpers this script deliberately
re-implements rather than imports (established convention in this directory:
each script is self-contained / edit-in-place, not a shared library).

Unlike _grid.py (N cities, one day, side-by-side panels), this script does N
weekday pairs (Fri/Fri, Sat/Sat, Sun/Sun), each pair overlaid as two lines on
ONE shared axes per weekday - built to answer "did the festival weekend show
worse delays than a normal weekend, at the same time of day, on the same
weekday".

Also computes route-level before/after stats for route 603 (identified by
inspecting its stop list against stops.txt: it calls at "Kusocinskiego-Blonia
Lodzkie" / "Armii Krajowej-Blonia Lodzkie", ~0.5-1km from the "Retkinia
Kusocinskiego" stops - i.e. it is the line serving the festival meadow itself,
not just the surrounding estate) and every other route found to serve a stop
literally named "Retkinia*" in the 2026-07-24 static feed (RETKINIA_ROUTE_IDS
below - computed once via stop_times/trips/routes join, hardcoded here since
that join is expensive to redo per run and route<->stop assignment does not
change within this investigation's time window).

Input: for each date in WEEKEND_BEFORE + WEEKEND_FESTIVAL, needs that date's
`lodz_static_gtfs_<date>.zip`, `lodz_realized_<date>_p50.zip` (Family A
"realized" GTFS, family_a.cli build's p50 output) and
`lodz_diff_<date>_p50_summary.csv` (per-route whole-day aggregate, already
built by the phone pipeline) - all three attached to that date's
`lodz-realized-<date>-phone` GitHub release in GISBoost/easy-GTFS-RT:
    gh release download lodz-realized-<date>-phone -R GISBoost/easy-GTFS-RT \\
        -p "lodz_static_gtfs_<date>.zip" -p "lodz_realized_<date>_p50.zip" \\
        -p "lodz_diff_<date>_p50_summary.csv"

Output:
- OUTPUT_DIR/lodz_weekend_overlay_<weekday>.png - one PNG per weekday pair.
- OUTPUT_DIR/lodz_route603_and_retkinia_routes_before_after.csv - per-route,
  per-date mean/abs-mean delay and pct-changed, pulled straight from each
  date's diff_<date>_p50_summary.csv (whole-day aggregate, no recomputation).

Edit the CONFIG block below, then run: `py lodz_festival_weekend_overlay.py`
"""
from __future__ import annotations

import csv
import io
import statistics
import zipfile
from pathlib import Path

# ------------------------------- CONFIG -------------------------------

_DATA_DIR = r"C:\Users\Michal\AppData\Local\Temp\claude\C--Users-Michal-Desktop-easy-easy-OTP\d944888a-a206-4206-b361-4a863b93f273\scratchpad\lodz_festival"

# (weekday label, "before" date, "festival" date)
WEEKDAY_PAIRS = [
    ("Friday", "2026-07-17", "2026-07-24"),
    ("Saturday", "2026-07-18", "2026-07-25"),
    ("Sunday", "2026-07-19", "2026-07-26"),
]

def _static_zip(date: str) -> str:
    return rf"{_DATA_DIR}\lodz_static_gtfs_{date}.zip"

def _realized_zip(date: str) -> str:
    return rf"{_DATA_DIR}\lodz_realized_{date}_p50.zip"

def _diff_summary_csv(date: str) -> str:
    return rf"{_DATA_DIR}\lodz_diff_{date}_p50_summary.csv"

DELAY_TIME_FIELD = "departure_time"

CHART_START_HOUR = 6
CHART_END_HOUR = 22
CHART_BUCKET_MINUTES = 30
CHART_TICK_INTERVAL_MINUTES = 60
CHART_TICK_LABEL_ROTATION = 45
COLOR_BEFORE = "tab:blue"
COLOR_FESTIVAL = "tab:red"

OUTPUT_DIR = Path(r"C:\Users\Michal\Desktop\easy\easy-OTP\tools\analysis\output\lodz_festival_weekend")

# route_id -> route_short_name, for routes found (2026-07-24 static feed) to serve
# a stop literally named "Retkinia*" via a stop_times/trips/routes join. See module
# docstring for how this was derived; hardcoded to avoid redoing the expensive join
# on every run.
RETKINIA_ROUTE_IDS = {
    "10A": "10A", "10B": "10B", "12": "12", "14": "14", "16": "16",
    "50A": "50A", "50B": "50B", "51A": "51A", "52": "52",
    "55A": "55A", "55B": "55B", "55C": "55C", "57": "57", "68": "68",
    "69A": "69A", "69B": "69B", "70": "70", "76": "76", "80A": "80A", "80B": "80B",
    "86": "86", "94": "94", "99": "99", "G1": "G1", "G2": "G2",
    "N2": "N2", "N2A": "N2A", "N7A": "N7A", "N7B": "N7B", "N9": "N9",
    "R11": "R11", "R22": "R22", "W": "W",
}
# The festival-meadow line itself (calls at Kusocinskiego-Blonia Lodzkie /
# Armii Krajowej-Blonia Lodzkie), tracked separately from RETKINIA_ROUTE_IDS
# since it does not stop at any "Retkinia*"-named stop.
FESTIVAL_MEADOW_ROUTE_ID = "603"

# ------------------------------------------------------------------------


def _parse_gtfs_time(value: str) -> int:
    parts = value.strip().split(":")
    if len(parts) != 3:
        raise ValueError(f"Not a valid GTFS time string: {value!r}")
    h, m, s = (int(p) for p in parts)
    return h * 3600 + m * 60 + s


def _minutes_to_hhmm(minutes: float) -> str:
    total = int(round(minutes)) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def _open_member(zip_path: str, member: str):
    z = zipfile.ZipFile(zip_path)
    name = next(n for n in z.namelist() if n.split("/")[-1] == member)
    return io.TextIOWrapper(z.open(name), encoding="utf-8-sig")


def _read_stop_times(zip_path: str, time_field: str) -> dict:
    """Returns {(trip_id, stop_id, stop_sequence): seconds_since_midnight}."""
    result = {}
    with _open_member(zip_path, "stop_times.txt") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw_time = (row.get(time_field) or "").strip()
            if not raw_time:
                continue
            key = (row["trip_id"], row["stop_id"], int(row["stop_sequence"]))
            result[key] = _parse_gtfs_time(raw_time)
    return result


def _read_trip_route_map(zip_path: str) -> dict:
    """Returns {trip_id: route_id} from the static feed's trips.txt."""
    with _open_member(zip_path, "trips.txt") as f:
        return {row["trip_id"]: row["route_id"] for row in csv.DictReader(f)}


def _build_delay_rows(static_zip: str, realized_zip: str, trip_route_map: dict):
    """Returns [(static_sec, delay_sec, route_id), ...] for every matched,
    non-zero-delay row."""
    static_times = _read_stop_times(static_zip, DELAY_TIME_FIELD)
    realized_times = _read_stop_times(realized_zip, DELAY_TIME_FIELD)
    matched_keys = set(static_times) & set(realized_times)
    rows = []
    for key in matched_keys:
        static_sec = static_times[key]
        delay_sec = realized_times[key] - static_sec
        if delay_sec != 0:
            trip_id = key[0]
            rows.append((static_sec, delay_sec, trip_route_map.get(trip_id)))
    print(
        f"    stop_times.txt rows: static={len(static_times)}, "
        f"realized={len(realized_times)}, matched={len(matched_keys)}, "
        f"non-zero-delay={len(rows)}"
    )
    return rows


def _bucket(rows) -> tuple[list[int], list[float], list[int]]:
    start_minute = CHART_START_HOUR * 60
    end_minute = CHART_END_HOUR * 60
    buckets: dict[int, list[float]] = {}
    for static_sec, delay_sec, _route_id in rows:
        static_min = static_sec / 60.0
        if static_min < start_minute or static_min > end_minute:
            continue
        bucket_start = int(static_min // CHART_BUCKET_MINUTES) * CHART_BUCKET_MINUTES
        buckets.setdefault(bucket_start, []).append(delay_sec / 60.0)
    bucket_starts = sorted(buckets)
    mean_delays = [statistics.mean(buckets[b]) for b in bucket_starts]
    counts = [len(buckets[b]) for b in bucket_starts]
    return bucket_starts, mean_delays, counts


def _read_diff_summary(path: str) -> dict:
    """Returns {route_id: row_dict} from a lodz_diff_<date>_p50_summary.csv."""
    with open(path, encoding="utf-8-sig") as f:
        return {row["route_id"]: row for row in csv.DictReader(f)}


def main() -> int:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    all_dates = sorted({d for pair in WEEKDAY_PAIRS for d in (pair[1], pair[2])})
    rows_by_date: dict[str, list] = {}
    for date in all_dates:
        print(f"--- reading {date} ---")
        trip_route_map = _read_trip_route_map(_static_zip(date))
        rows_by_date[date] = _build_delay_rows(
            _static_zip(date), _realized_zip(date), trip_route_map
        )

    # --- 3 overlay charts (city-wide, all routes) ---
    start_minute = CHART_START_HOUR * 60
    end_minute = CHART_END_HOUR * 60
    for weekday, before_date, festival_date in WEEKDAY_PAIRS:
        fig, ax = plt.subplots(figsize=(10, 5.5))
        for date, color, label in (
            (before_date, COLOR_BEFORE, f"{before_date} (przed festiwalem)"),
            (festival_date, COLOR_FESTIVAL, f"{festival_date} (Lodz Summer Festival)"),
        ):
            bucket_starts, mean_delays, counts = _bucket(rows_by_date[date])
            if not bucket_starts:
                print(f"  {date}: no data in window, skipping line")
                continue
            ax.plot(
                [b + CHART_BUCKET_MINUTES / 2 for b in bucket_starts], mean_delays,
                color=color, marker="o", markersize=3, linewidth=1.4, label=label,
            )
        ax.axhline(0, color="black", linewidth=0.8, alpha=0.5)
        ax.set_title(f"{weekday}: mean delay by scheduled time - Lodz, all routes")
        ax.set_xlabel("Scheduled time")
        ax.set_ylabel("Mean delay (min), realized vs static")
        ax.set_xlim(start_minute, end_minute)
        ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, pos: _minutes_to_hhmm(x)))
        ax.xaxis.set_major_locator(mticker.MultipleLocator(CHART_TICK_INTERVAL_MINUTES))
        ax.tick_params(axis="x", rotation=CHART_TICK_LABEL_ROTATION)
        ax.grid(True, alpha=0.3)
        ax.legend()
        fig.tight_layout()
        out_path = OUTPUT_DIR / f"lodz_weekend_overlay_{weekday.lower()}.png"
        fig.savefig(out_path, dpi=150)
        plt.close(fig)
        print(f"  saved {out_path}")

    # --- route 603 + Retkinia-serving routes: before/after table, straight from
    #     each date's whole-day diff_<date>_p50_summary.csv ---
    tracked_route_ids = {FESTIVAL_MEADOW_ROUTE_ID, *RETKINIA_ROUTE_IDS}
    csv_out_path = OUTPUT_DIR / "lodz_route603_and_retkinia_routes_before_after.csv"
    with open(csv_out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "route_id", "date", "weekday", "period", "n_rows", "n_changed",
            "pct_changed", "mean_delay_sec", "mean_abs_delay_sec", "stdev_delay_sec",
            "min_delay_sec", "max_delay_sec",
        ])
        for weekday, before_date, festival_date in WEEKDAY_PAIRS:
            for date, period in ((before_date, "before"), (festival_date, "festival")):
                summary = _read_diff_summary(_diff_summary_csv(date))
                for route_id in sorted(tracked_route_ids):
                    row = summary.get(route_id)
                    if row is None:
                        writer.writerow([route_id, date, weekday, period, "NO DATA", "", "", "", "", "", "", ""])
                        continue
                    writer.writerow([
                        route_id, date, weekday, period,
                        row["n_rows"], row["n_changed"], row["pct_changed"],
                        row["mean_delay_sec"], row["mean_abs_delay_sec"],
                        row["stdev_delay_sec"], row["min_delay_sec"], row["max_delay_sec"],
                    ])
    print(f"\nRoute before/after table saved to {csv_out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
