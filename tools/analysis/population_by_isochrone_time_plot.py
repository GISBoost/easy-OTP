"""population_by_isochrone_time_plot.py — chart-only: static vs RT
population_covered, no overlay recomputation.

Standalone ANALYSIS tooling. NOT part of the easy-OTP plugin and not imported
by it. Companion to population_by_isochrone_time_compare.py and
population_covered_diff_static_vs_rt.py — this one assumes POP_COVERED_FIELD
is ALREADY populated on both isochrone layers (by
population_by_isochrone_time_compare.py or population_by_isochrone_time.py)
and just reads + plots it. Use this when you want to try a few chart
variants (different title, output path, styling) without re-running the
area-weighted population overlay each time — that overlay is the slow part,
this script is just field reads + matplotlib.

Run inside the QGIS Python Console (Plugins -> Python Console -> "Show
Editor", paste, Run) or as a one-off Processing "Script".

Input layers (must already be loaded in the current QGIS project, and must
already have POP_COVERED_FIELD populated):
  - ISOCHRONE_LAYER_STATIC_NAME / ISOCHRONE_LAYER_RT_NAME: the same two
    GenerateIsochronesOverTime outputs used for the comparison / diff scripts.

Edit the CONFIG block below, then run.
"""
from __future__ import annotations

import datetime as dt

from qgis.core import NULL, QgsProject
from qgis.PyQt.QtCore import QDateTime, QTime

# ------------------------------- CONFIG -------------------------------
# Fill in the two layer names (same ones used in the compare / diff scripts).

ISOCHRONE_LAYER_STATIC_NAME = "isochrones-static"
ISOCHRONE_LAYER_RT_NAME = "isochrones-rt-fa6"

TIME_FIELD = "time"                        # per-row time field on both layers
CUTOFF_FIELD = "cutoff_min"                # set to None if only one cutoff / not present
POP_COVERED_FIELD = "population_covered"   # already computed on both layers — not recomputed here

CHART_START_HOUR = 13            # first hour shown on the chart (0-23); set to None to disable
CHART_END_HOUR = 17              # last hour shown on the chart (0-23); set to None to disable
CHART_TICK_INTERVAL_MINUTES = 15   # spacing between x-axis ticks, in minutes
CHART_COLOR_CYCLE = ["red", "blue"]           # list of colour strings, one per plotted line (static then RT, per cutoff, in that order); None = matplotlib's default cycle

CHART_TITLE = "Population covered by isochrone: GTFS-static vs GTFS-RT"
OUTPUT_PNG_PATH = r"C:\Users\Michal\Desktop\easy-OTP\tools\analysis\output\population_by_isochrone_time_plot-fa6-13-17.png"

# ------------------------------------------------------------------------


def _time_to_minutes(value) -> float:
    """Same robust converter as the other isochrone scripts in this folder —
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
            raise RuntimeError(
                f"Layer '{layer_name}' has no field '{field}'. "
                "Run population_by_isochrone_time_compare.py first to populate it."
            )
    if CUTOFF_FIELD and layer.fields().indexFromName(CUTOFF_FIELD) < 0:
        raise RuntimeError(
            f"Layer '{layer_name}' has no field '{CUTOFF_FIELD}'. "
            "Set CUTOFF_FIELD = None if neither layer has more than one cutoff."
        )


def _read_results(layer):
    """Returns [(time_value, cutoff_value, population_covered), ...], skipping
    rows where POP_COVERED_FIELD hasn't been computed (NULL)."""
    results = []
    skipped = 0
    for feat in layer.getFeatures():
        pop_value = feat[POP_COVERED_FIELD]
        if pop_value is None or pop_value == NULL:
            skipped += 1
            continue
        cutoff_value = feat[CUTOFF_FIELD] if CUTOFF_FIELD else None
        results.append((feat[TIME_FIELD], cutoff_value, float(pop_value)))
    if skipped:
        print(f"  ({skipped} feature(s) skipped — '{POP_COVERED_FIELD}' not yet computed.)")
    return results


def main():
    static_layer = _load_layer(ISOCHRONE_LAYER_STATIC_NAME)
    rt_layer = _load_layer(ISOCHRONE_LAYER_RT_NAME)
    _validate_layer(static_layer, ISOCHRONE_LAYER_STATIC_NAME)
    _validate_layer(rt_layer, ISOCHRONE_LAYER_RT_NAME)

    print(f"Reading '{POP_COVERED_FIELD}' from '{ISOCHRONE_LAYER_STATIC_NAME}'...")
    results_static = _read_results(static_layer)
    print(f"Reading '{POP_COVERED_FIELD}' from '{ISOCHRONE_LAYER_RT_NAME}'...")
    results_rt = _read_results(rt_layer)

    if not results_static and not results_rt:
        raise RuntimeError(
            f"No rows with '{POP_COVERED_FIELD}' computed on either layer. "
            "Run population_by_isochrone_time_compare.py first."
        )

    _plot_comparison(results_static, results_rt)


def _plot_comparison(results_static, results_rt):
    import matplotlib
    matplotlib.use("Agg")  # headless — QGIS embeds its own Qt event loop
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker

    start_minute = CHART_START_HOUR * 60 if CHART_START_HOUR is not None else None
    end_minute = CHART_END_HOUR * 60 if CHART_END_HOUR is not None else None

    def _in_window(minutes):
        if start_minute is not None and minutes < start_minute:
            return False
        if end_minute is not None and minutes > end_minute:
            return False
        return True

    def _to_series(results):
        by_cutoff = {}
        for time_value, cutoff_value, pop in results:
            minutes = _time_to_minutes(time_value)
            if not _in_window(minutes):
                continue
            by_cutoff.setdefault(cutoff_value, []).append((minutes, pop))
        for pts in by_cutoff.values():
            pts.sort(key=lambda p: p[0])
        return by_cutoff

    static_series = _to_series(results_static)
    rt_series = _to_series(results_rt)

    if not static_series and not rt_series:
        print(
            f"\nNo isochrone time steps fall inside the configured chart window "
            f"({CHART_START_HOUR}:00-{CHART_END_HOUR}:00). Skipping chart."
        )
        return

    cutoffs = sorted(
        {*static_series.keys(), *rt_series.keys()},
        key=lambda c: (c is None, c),
    )
    color_cycle = CHART_COLOR_CYCLE or plt.rcParams["axes.prop_cycle"].by_key()["color"]

    fig, ax = plt.subplots(figsize=(12, 6))
    color_index = 0
    for cutoff_value in cutoffs:
        cutoff_label = f" ({cutoff_value} min)" if cutoff_value is not None else ""

        if cutoff_value in static_series:
            xs, ys = zip(*static_series[cutoff_value])
            ax.plot(
                xs, ys, color=color_cycle[color_index % len(color_cycle)], linestyle="None", marker="o", markersize=3,
                label=f"GTFS-static{cutoff_label}",
            )
            color_index += 1
        if cutoff_value in rt_series:
            xs_rt, ys_rt = zip(*rt_series[cutoff_value])
            ax.plot(
                xs_rt, ys_rt, color=color_cycle[color_index % len(color_cycle)], linestyle="None", marker="s", markersize=3,
                label=f"GTFS-RT{cutoff_label}",
            )
            color_index += 1
        if cutoff_value in static_series and cutoff_value in rt_series:
            for (x, y_static), (_, y_rt) in zip(static_series[cutoff_value], rt_series[cutoff_value]):
                ax.vlines(x, y_static, y_rt, color="grey", linewidth=0.2, alpha=0.5)

    ax.set_xlabel("Time")
    ax.set_ylabel("Population covered")
    ax.set_title(CHART_TITLE)
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, pos: _minutes_to_hhmm(x)))
    ax.xaxis.set_major_locator(mticker.MultipleLocator(CHART_TICK_INTERVAL_MINUTES))
    if start_minute is not None and end_minute is not None:
        ax.set_xlim(start_minute, end_minute)
    elif start_minute is not None:
        ax.set_xlim(start_minute, None)
    elif end_minute is not None:
        ax.set_xlim(None, end_minute)
    plt.xticks(rotation=45, ha="right")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(OUTPUT_PNG_PATH, dpi=150)
    print(f"Chart saved to {OUTPUT_PNG_PATH}")


main()
