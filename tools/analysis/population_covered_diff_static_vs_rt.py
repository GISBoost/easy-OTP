"""population_covered_diff_static_vs_rt.py — numeric diff between the
GTFS-static and GTFS-RT population_covered fields, per time step + summary.

Standalone ANALYSIS tooling. NOT part of the easy-OTP plugin and not imported
by it. Companion to population_by_isochrone_time_compare.py — that script
computes and charts population_covered on both isochrone layers; this one
assumes population_covered is ALREADY present on both (does not recompute
the area-weighted overlay) and just diffs the two columns per time step,
then writes a per-minute table plus summary statistics.

Run inside the QGIS Python Console (Plugins -> Python Console -> "Show
Editor", paste, Run) or as a one-off Processing "Script".

Input layers (must already be loaded in the current QGIS project, and must
already have POP_COVERED_FIELD populated by
population_by_isochrone_time_compare.py or population_by_isochrone_time.py):
  - ISOCHRONE_LAYER_STATIC_NAME / ISOCHRONE_LAYER_RT_NAME: the same two
    GenerateIsochronesOverTime outputs used for the comparison chart.

Matching: rows are paired by (time rounded to the nearest minute, cutoff_min)
since that's what GenerateIsochronesOverTime writes for both static and RT
runs given identical run parameters. A timestamp that failed the OTP request
on one side (skipped by GenerateIsochronesOverTime, per its own code) has no
counterpart on the other side — these are reported as unmatched, not
silently dropped or guessed at.

Output:
  - OUTPUT_DETAIL_CSV_PATH: one row per matched (time, cutoff_min) pair —
    population_covered_static, population_covered_rt, diff (rt - static),
    diff_pct.
  - OUTPUT_SUMMARY_CSV_PATH: mean / mean(|diff|) / stdev / min / max diff,
    overall and per cutoff_min.

Edit the CONFIG block below, then run.
"""
from __future__ import annotations

import csv
import datetime as dt
import statistics

from qgis.core import NULL, QgsProject
from qgis.PyQt.QtCore import QDateTime, QTime

# ------------------------------- CONFIG -------------------------------
# Fill in the two layer names (same ones used in
# population_by_isochrone_time_compare.py — reuse those values here).

ISOCHRONE_LAYER_STATIC_NAME = "isochrones-static"
ISOCHRONE_LAYER_RT_NAME = "isochrones-rt"

TIME_FIELD = "time"                  # per-row time field on both layers
CUTOFF_FIELD = "cutoff_min"          # set to None if only one cutoff / not present
POP_COVERED_FIELD = "population_covered"  # already computed on both layers — not recomputed here

OUTPUT_DETAIL_CSV_PATH = r"C:\Users\Michal\Desktop\easy-OTP\tools\analysis\population_covered_diff_detail.csv"
OUTPUT_SUMMARY_CSV_PATH = r"C:\Users\Michal\Desktop\easy-OTP\tools\analysis\population_covered_diff_summary.csv"

# ------------------------------------------------------------------------


def _time_to_minutes(value) -> float:
    """Same robust converter as population_by_isochrone_time_compare.py —
    PyQGIS hands back QDateTime/QTime for DateTime/Time fields, not plain
    python datetime/str. Raises on an unrecognised type instead of guessing.
    """
    if isinstance(value, QDateTime):
        t = value.time()
        return t.hour() * 60 + t.minute() + t.second() / 60.0
    if isinstance(value, QTime):
        return value.hour() * 60 + value.minute() + value.second() / 60.0
    if isinstance(value, dt.datetime):
        return value.hour * 60 + value.minute + value.second / 60.0
    if isinstance(value, dt.time):
        return value.hour * 60 + value.minute + value.second / 60.0
    if isinstance(value, str):
        for fmt in ("%H:%M:%S", "%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                parsed = dt.datetime.strptime(value, fmt)
                return parsed.hour * 60 + parsed.minute + parsed.second / 60.0
            except ValueError:
                continue
    raise ValueError(
        f"Cannot interpret TIME_FIELD value {value!r} (type {type(value).__name__}) "
        "as a time. Check that TIME_FIELD points to the right field on both layers."
    )


def _minutes_to_hhmm(minutes: float) -> str:
    total = int(round(minutes)) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def _load_layer(name: str):
    layers = QgsProject.instance().mapLayersByName(name)
    if not layers:
        raise RuntimeError(f"Layer '{name}' not found in the current project.")
    return layers[0]


def _validate_layer(layer, layer_name: str) -> None:
    for field in (TIME_FIELD, POP_COVERED_FIELD):
        if layer.fields().indexFromName(field) < 0:
            raise RuntimeError(f"Layer '{layer_name}' has no field '{field}'.")
    if CUTOFF_FIELD and layer.fields().indexFromName(CUTOFF_FIELD) < 0:
        raise RuntimeError(
            f"Layer '{layer_name}' has no field '{CUTOFF_FIELD}'. "
            "Set CUTOFF_FIELD = None if neither layer has more than one cutoff."
        )


def _index_by_time_cutoff(layer):
    """Returns {(minute_rounded, cutoff_value): (time_label, population_covered)}."""
    indexed = {}
    for feat in layer.getFeatures():
        pop_value = feat[POP_COVERED_FIELD]
        if pop_value is None or pop_value == NULL:
            continue
        minutes = _time_to_minutes(feat[TIME_FIELD])
        key = (round(minutes), feat[CUTOFF_FIELD] if CUTOFF_FIELD else None)
        indexed[key] = (_minutes_to_hhmm(minutes), float(pop_value))
    return indexed


def main():
    static_layer = _load_layer(ISOCHRONE_LAYER_STATIC_NAME)
    rt_layer = _load_layer(ISOCHRONE_LAYER_RT_NAME)
    _validate_layer(static_layer, ISOCHRONE_LAYER_STATIC_NAME)
    _validate_layer(rt_layer, ISOCHRONE_LAYER_RT_NAME)

    static_index = _index_by_time_cutoff(static_layer)
    rt_index = _index_by_time_cutoff(rt_layer)

    matched_keys = sorted(
        set(static_index) & set(rt_index),
        key=lambda k: (k[1] is None, k[1], k[0]),
    )
    static_only = sorted(set(static_index) - set(rt_index), key=lambda k: (k[1] is None, k[1], k[0]))
    rt_only = sorted(set(rt_index) - set(static_index), key=lambda k: (k[1] is None, k[1], k[0]))

    if static_only:
        print(f"WARNING: {len(static_only)} time/cutoff step(s) present in STATIC only (no RT counterpart):")
        for minute, cutoff in static_only[:20]:
            label = static_index[(minute, cutoff)][0]
            print(f"  {label}  cutoff={cutoff}")
        if len(static_only) > 20:
            print(f"  ... and {len(static_only) - 20} more.")
    if rt_only:
        print(f"WARNING: {len(rt_only)} time/cutoff step(s) present in RT only (no static counterpart):")
        for minute, cutoff in rt_only[:20]:
            label = rt_index[(minute, cutoff)][0]
            print(f"  {label}  cutoff={cutoff}")
        if len(rt_only) > 20:
            print(f"  ... and {len(rt_only) - 20} more.")

    if not matched_keys:
        raise RuntimeError(
            "No matching (time, cutoff_min) pairs found between the two layers — "
            "nothing to diff. Check TIME_FIELD/CUTOFF_FIELD and that both layers "
            "were generated with the same time window / cutoffs."
        )

    detail_rows = []  # (time_label, cutoff_value, pop_static, pop_rt, diff, diff_pct)
    for minute, cutoff in matched_keys:
        time_label, pop_static = static_index[(minute, cutoff)]
        _, pop_rt = rt_index[(minute, cutoff)]
        diff = pop_rt - pop_static
        diff_pct = (diff / pop_static * 100.0) if pop_static != 0 else None
        detail_rows.append((time_label, cutoff, pop_static, pop_rt, diff, diff_pct))

    with open(OUTPUT_DETAIL_CSV_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "time", "cutoff_min", "population_covered_static", "population_covered_rt",
            "diff_rt_minus_static", "diff_pct",
        ])
        for time_label, cutoff, pop_static, pop_rt, diff, diff_pct in detail_rows:
            w.writerow([
                time_label, cutoff, round(pop_static, 2), round(pop_rt, 2),
                round(diff, 2), round(diff_pct, 2) if diff_pct is not None else "",
            ])
    print(f"Detail CSV written: {OUTPUT_DETAIL_CSV_PATH} ({len(detail_rows)} rows)")

    _write_summary(detail_rows)


def _summarize(diffs, pcts):
    n = len(diffs)
    abs_diffs = [abs(d) for d in diffs]
    return {
        "n": n,
        "mean_diff": statistics.mean(diffs),
        "mean_abs_diff": statistics.mean(abs_diffs),
        "stdev_diff": statistics.pstdev(diffs) if n > 1 else 0.0,
        "min_diff": min(diffs),
        "max_diff": max(diffs),
        "mean_diff_pct": statistics.mean(pcts) if pcts else None,
    }


def _write_summary(detail_rows):
    by_cutoff = {}
    for time_label, cutoff, pop_static, pop_rt, diff, diff_pct in detail_rows:
        by_cutoff.setdefault(cutoff, {"diffs": [], "pcts": []})
        by_cutoff[cutoff]["diffs"].append(diff)
        if diff_pct is not None:
            by_cutoff[cutoff]["pcts"].append(diff_pct)

    all_diffs = [row[4] for row in detail_rows]
    all_pcts = [row[5] for row in detail_rows if row[5] is not None]

    summary_rows = []
    for cutoff, data in sorted(by_cutoff.items(), key=lambda kv: (kv[0] is None, kv[0])):
        stats = _summarize(data["diffs"], data["pcts"])
        summary_rows.append((cutoff, stats))
    overall_stats = _summarize(all_diffs, all_pcts)
    summary_rows.append(("ALL", overall_stats))

    with open(OUTPUT_SUMMARY_CSV_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "cutoff_min", "n_steps", "mean_diff_rt_minus_static", "mean_abs_diff",
            "stdev_diff", "min_diff", "max_diff", "mean_diff_pct",
        ])
        for cutoff, stats in summary_rows:
            w.writerow([
                cutoff,
                stats["n"],
                round(stats["mean_diff"], 2),
                round(stats["mean_abs_diff"], 2),
                round(stats["stdev_diff"], 2),
                round(stats["min_diff"], 2),
                round(stats["max_diff"], 2),
                round(stats["mean_diff_pct"], 2) if stats["mean_diff_pct"] is not None else "",
            ])
    print(f"Summary CSV written: {OUTPUT_SUMMARY_CSV_PATH}")

    print("\n--- Summary (population_covered, RT minus static) ---")
    for cutoff, stats in summary_rows:
        label = f"cutoff={cutoff} min" if cutoff != "ALL" else "ALL cutoffs"
        pct_str = f", mean_pct={stats['mean_diff_pct']:.2f}%" if stats["mean_diff_pct"] is not None else ""
        print(
            f"  {label}: n={stats['n']}, mean_diff={stats['mean_diff']:.2f}, "
            f"mean_abs_diff={stats['mean_abs_diff']:.2f}, stdev={stats['stdev_diff']:.2f}, "
            f"min={stats['min_diff']:.2f}, max={stats['max_diff']:.2f}{pct_str}"
        )


main()
