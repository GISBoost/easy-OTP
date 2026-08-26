"""benchmark_isochrones.py -- parse compute_isochrones_city.R's own per-hour
timing lines (already printed to <city>_<variant>_compute.log by
run_city_computations.sh's redirect) into a normalized throughput number, so
the r5r run here can later be compared against an OTP run of the same shape
(same origins/hours/cutoffs, via the plugin's GenerateIsochronesOverTime) on
equal footing -- no separate profiling harness needed, the timing was already
being printed, this just extracts it and does the "current progress + ETA"
arithmetic the terminal output doesn't do for you.

Usage: py benchmark_isochrones.py [--watch]
  (no args) -- one-shot: print progress/throughput for every *_compute.log
               found next to this script, append finished runs to
               benchmark_summary.csv (skips runs already recorded there).
  --watch   -- re-print every 30s until every log file expected for the
               current run_city_computations.sh sweep is finished (Ctrl+C to
               stop early; a one-shot check is always safe to run meanwhile).
"""
from __future__ import annotations

import csv
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
SUMMARY_CSV = HERE / "benchmark_summary.csv"
HOURS_PER_RUN = 17  # 06:00-22:00, matches compute_isochrones*.R

# Origin counts (accessibility_cities/<city>/<city>_hex_origins.csv row count - 1)
# and the exact run order from run_city_computations.sh, so not-yet-started
# runs can be estimated too, not just the one currently in progress.
CITY_ORIGINS = {"warszawa": 2546, "krakow": 1633, "gdansk": 1389, "poznan": 1350, "szczecin": 1567}
ALL_RUNS = [(city, variant) for city in CITY_ORIGINS for variant in ("static", "rt")]

STEP_RE = re.compile(r"^\[\s*(\d+)/(\d+)\]\s+\d\d:00 -> (\d+) features in ([\d.]+) s")
ORIGINS_RE = re.compile(r"^origins:\s*(\d+)")


def parse_log(path: Path) -> dict | None:
    origins = None
    step_times: list[float] = []
    total_steps = HOURS_PER_RUN
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = ORIGINS_RE.match(line)
        if m:
            origins = int(m.group(1))
            continue
        m = STEP_RE.match(line)
        if m:
            total_steps = int(m.group(2))
            step_times.append(float(m.group(4)))
    if origins is None or not step_times:
        return None
    done = len(step_times)
    elapsed = sum(step_times)
    s_per_origin_hour = elapsed / (origins * done)
    remaining = total_steps - done
    eta_s = remaining * (elapsed / done)
    return {
        "origins": origins,
        "steps_done": done,
        "steps_total": total_steps,
        "elapsed_s": elapsed,
        "s_per_origin_hour": s_per_origin_hour,
        "eta_remaining_s": eta_s,
        "complete": done >= total_steps,
    }


def fmt_hms(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:d}h{m:02d}m" if h else f"{m:d}m{s:02d}s"


def load_recorded() -> set[tuple[str, str]]:
    if not SUMMARY_CSV.exists():
        return set()
    with open(SUMMARY_CSV, encoding="utf-8") as fh:
        return {(row["city"], row["variant"]) for row in csv.DictReader(fh)}


def append_summary(rows: list[dict]) -> None:
    if not rows:
        return
    new_file = not SUMMARY_CSV.exists()
    with open(SUMMARY_CSV, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["method", "city", "variant", "origins", "hours", "elapsed_s", "s_per_origin_hour"])
        if new_file:
            w.writeheader()
        w.writerows(rows)


def run_once() -> bool:
    """Returns True if every log found is complete (steps_done == steps_total)."""
    logs = sorted(HERE.glob("*_compute.log"))
    if not logs:
        print("no *_compute.log files found yet")
        return False

    recorded = load_recorded()
    to_append = []
    parsed_by_run: dict[tuple[str, str], dict] = {}
    all_done = True

    print(f"{'city/variant':<22} {'origins':>8} {'step':>7} {'s/origin/h':>11} {'elapsed':>9} {'eta':>9}")
    for log in logs:
        name = log.stem.replace("_compute", "")  # e.g. "warszawa_static"
        city, variant = name.rsplit("_", 1)
        parsed = parse_log(log)
        if parsed is None:
            print(f"{name:<22} (no steps logged yet -- still building the r5r network)")
            all_done = False
            continue
        parsed_by_run[(city, variant)] = parsed
        step_str = f"{parsed['steps_done']}/{parsed['steps_total']}"
        eta_str = "done" if parsed["complete"] else fmt_hms(parsed["eta_remaining_s"])
        print(f"{name:<22} {parsed['origins']:>8} {step_str:>7} {parsed['s_per_origin_hour']:>11.4f} "
              f"{fmt_hms(parsed['elapsed_s']):>9} {eta_str:>9}")
        if not parsed["complete"]:
            all_done = False

        if parsed["complete"] and (city, variant) not in recorded:
            to_append.append({
                "method": "r5r",
                "city": city,
                "variant": variant,
                "origins": parsed["origins"],
                "hours": parsed["steps_total"],
                "elapsed_s": round(parsed["elapsed_s"], 1),
                "s_per_origin_hour": round(parsed["s_per_origin_hour"], 4),
            })

    if to_append:
        append_summary(to_append)
        print(f"\nrecorded {len(to_append)} finished run(s) to {SUMMARY_CSV.name}")

    # Whole-batch ETA: sum each run's own remaining time if it's already
    # logging, or estimate it from the observed average s/origin/hour (across
    # every run that has at least one step logged) for runs not started yet.
    total_origin_hours_done = sum(p["origins"] * p["steps_done"] for p in parsed_by_run.values())
    total_elapsed_done = sum(p["elapsed_s"] for p in parsed_by_run.values())
    avg_rate = total_elapsed_done / total_origin_hours_done if total_origin_hours_done else None

    batch_remaining = 0.0
    unknown_rate = False
    for run in ALL_RUNS:
        p = parsed_by_run.get(run)
        if p is not None:
            batch_remaining += p["eta_remaining_s"] if not p["complete"] else 0.0
        else:
            if avg_rate is None:
                unknown_rate = True
                continue
            city, variant = run
            batch_remaining += CITY_ORIGINS[city] * HOURS_PER_RUN * avg_rate

    if not all_done or any(r not in parsed_by_run for r in ALL_RUNS):
        note = " (some not-yet-started runs excluded -- no rate observed yet)" if unknown_rate else ""
        print(f"\nfull batch (5 cities x 2 variants) remaining: ~{fmt_hms(batch_remaining)}{note}")
    return all_done and all(r in parsed_by_run and parsed_by_run[r]["complete"] for r in ALL_RUNS)


if __name__ == "__main__":
    watch = "--watch" in sys.argv
    if not watch:
        run_once()
    else:
        try:
            while True:
                print(f"\n=== {time.strftime('%H:%M:%S')} ===")
                if run_once():
                    print("\nall found logs complete.")
                    break
                time.sleep(30)
        except KeyboardInterrupt:
            pass
