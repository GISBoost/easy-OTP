"""population_by_isochrone_time_compare.py — GTFS-static vs GTFS-RT isochrone
population comparison.

Standalone ANALYSIS tooling. NOT part of the easy-OTP plugin and not imported
by it. Ad-hoc script that answers: "how many people are within the isochrone
at each time step, and does that change once the same analysis is re-run with
a live GTFS-RT feed instead of the static schedule?"

Based on population_by_isochrone_time.py (single-source version, kept as-is
in this folder). This version runs the same area-weighted population overlay
twice — once per isochrone layer — and plots both curves on one chart so the
static and RT results can be compared directly.

Run inside the QGIS Python Console (Plugins -> Python Console -> "Show
Editor", paste, Run) or as a one-off Processing "Script" — it needs the
qgis.core / qgis.PyQt modules bundled with QGIS, plus matplotlib (already
shipped with the QGIS Python distribution; no pip install needed).

Input layers (must already be loaded in the current QGIS project):
  - ISOCHRONE_LAYER_STATIC_NAME / ISOCHRONE_LAYER_RT_NAME: two outputs of
    GenerateIsochronesOverTime for the SAME origin/mode/cutoffs/time window —
    one built against the static GTFS graph, one against a graph whose
    router-config.json has the GTFS-RT stop-time-updater wired in. Both share
    the same field schema: time (QDateTime), cutoff_min (int), mode, date,
    direction, area_km2.
  - POPULATION_LAYER_NAME: the raw GUS census-tract polygon layer (same one
    used as input to "Prepare student layer" / R1a) — one numeric population
    field per tract, in a PROJECTED metric CRS (e.g. EPSG:2180).

What it does:
  1. Builds the population density index (tract population / tract area)
     ONCE, shared between both isochrone layers.
  2. For each isochrone layer, area-weighted-interpolates population from
     every intersecting census tract (same method as the plugin's
     PopulationOverlay algorithm), writing the result into OUTPUT_FIELD on
     that layer in place.
  3. Plots population_covered vs. time for both layers on one chart: static
     as circle markers, RT as square markers (no connecting line), one
     colour per plotted line in draw order (static then RT, per cutoff) via
     CHART_COLOR_CYCLE, so the two point series can be compared directly.
  4. X-axis is rendered as HH:MM regardless of the underlying QGIS field type
     (QDateTime/QTime/str) — times are converted to minutes-since-midnight
     internally and formatted back to HH:MM for the tick labels.

Edit the CONFIG block below to match your project's actual layer/field
names, then run.
"""
from __future__ import annotations

import datetime as dt

from qgis.core import (
    NULL,
    QgsCoordinateTransform,
    QgsField,
    QgsGeometry,
    QgsProject,
    QgsSpatialIndex,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication, QDateTime, QTime, QVariant

# ------------------------------- CONFIG -------------------------------
# Fill in the values marked TODO — layer names as shown in the Layers panel.
# TIME_FIELD / CUTOFF_FIELD below match GenerateIsochronesOverTime's output
# schema and should not need changing unless you renamed fields by hand.

ISOCHRONE_LAYER_STATIC_NAME = "isochrones-static"  # GenerateIsochronesOverTime run against static GTFS graph
ISOCHRONE_LAYER_RT_NAME = "isochrones-rt-fa6"          # GenerateIsochronesOverTime run against graph with GTFS-RT updater

POPULATION_LAYER_NAME = "pop"        # census-tract polygon layer, projected CRS
POPULATION_FIELD = "pop-all"         # numeric population field on POPULATION_LAYER_NAME

TIME_FIELD = "time"           # per-row time field on both isochrone layers
CUTOFF_FIELD = "cutoff_min"   # per-row cutoff field (set to None if only one cutoff / not present)
OUTPUT_FIELD = "population_covered"  # written onto BOTH isochrone layers

CHART_START_HOUR = 9            # first hour shown on the chart (0-23); set to None to disable
CHART_END_HOUR = 17              # last hour shown on the chart (0-23); set to None to disable
CHART_TICK_INTERVAL_MINUTES = 15   # spacing between x-axis ticks, in minutes
CHART_COLOR_CYCLE = ["red", "blue"]           # list of colour strings, one per plotted line (static then RT, per cutoff, in that order); None = matplotlib's default cycle

OUTPUT_PNG_PATH = r"C:\Users\Michal\Desktop\easy-OTP\tools\analysis\output\population_by_isochrone_time_compare-fa6-9-17.png"

# ------------------------------------------------------------------------


def _time_to_minutes(value) -> float:
    """Convert whatever QGIS/PyQt hands back for TIME_FIELD into minutes-since-midnight.

    Handles QDateTime and QTime (what PyQGIS actually returns for
    DateTime/Time attribute values — this is what the old script's string
    formatting choked on, printing the raw QDateTime/QTime repr instead of
    HH:MM), plus plain python datetime/time/str as a fallback. Raises rather
    than silently falling back to str(), so a genuinely unrecognised field
    type fails loudly instead of producing a garbled chart axis.
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


def _build_population_index(pop_layer, pop_field: str):
    """Precompute population density (persons / m^2) per census tract.

    Geometries and densities are cached in memory so the per-isochrone loop
    below never re-queries the population layer's data provider — repeated
    per-feature disk/DB round-trips were the actual cause of QGIS appearing
    to hang in the original single-layer script.
    """
    geom_by_fid = {}
    density_by_fid = {}
    for feat in pop_layer.getFeatures():
        geom = feat.geometry()
        if not geom.isGeosValid():
            geom = geom.makeValid()
        area = geom.area()
        value = feat[pop_field]
        if area <= 0 or value is None or value == NULL:
            continue
        geom_by_fid[feat.id()] = geom
        density_by_fid[feat.id()] = float(value) / area

    index = QgsSpatialIndex()
    for fid, geom in geom_by_fid.items():
        index.addFeature(fid, geom.boundingBox())
    return geom_by_fid, density_by_fid, index


def _validate_isochrone_layer(iso_layer, layer_name: str) -> None:
    if iso_layer.fields().indexFromName(TIME_FIELD) < 0:
        raise RuntimeError(f"Layer '{layer_name}' has no field '{TIME_FIELD}'.")
    if CUTOFF_FIELD and iso_layer.fields().indexFromName(CUTOFF_FIELD) < 0:
        raise RuntimeError(
            f"Layer '{layer_name}' has no field '{CUTOFF_FIELD}'. "
            "Set CUTOFF_FIELD = None if neither layer has more than one cutoff."
        )


def _compute_coverage(
    iso_layer,
    layer_name: str,
    pop_layer,
    geom_by_fid,
    density_by_fid,
    index,
    project,
):
    """Area-weighted population overlay for one isochrone layer. Writes
    OUTPUT_FIELD in place and returns [(time_value, cutoff_value, population_covered), ...].
    """
    transform = None
    if iso_layer.crs() != pop_layer.crs():
        transform = QgsCoordinateTransform(iso_layer.crs(), pop_layer.crs(), project)

    if iso_layer.fields().indexFromName(OUTPUT_FIELD) < 0:
        iso_layer.dataProvider().addAttributes([QgsField(OUTPUT_FIELD, QVariant.Double)])
        iso_layer.updateFields()
    out_idx = iso_layer.fields().indexFromName(OUTPUT_FIELD)

    total = iso_layer.featureCount()
    results = []

    iso_layer.startEditing()
    for i, iso_feat in enumerate(iso_layer.getFeatures(), start=1):
        iso_geom = QgsGeometry(iso_feat.geometry())
        if transform is not None:
            iso_geom.transform(transform)
        if not iso_geom.isGeosValid():
            iso_geom = iso_geom.makeValid()

        covered_pop = 0.0
        for fid in index.intersects(iso_geom.boundingBox()):
            pop_geom = geom_by_fid[fid]
            if not pop_geom.intersects(iso_geom):
                continue
            piece = pop_geom.intersection(iso_geom)
            if piece.isEmpty():
                continue
            covered_pop += piece.area() * density_by_fid[fid]

        covered_pop = round(covered_pop, 2)
        iso_layer.changeAttributeValue(iso_feat.id(), out_idx, covered_pop)

        time_value = iso_feat[TIME_FIELD]
        cutoff_value = iso_feat[CUTOFF_FIELD] if CUTOFF_FIELD else None
        results.append((time_value, cutoff_value, covered_pop))

        if i % 10 == 0 or i == total:
            print(f"[{layer_name}] {i}/{total} isochrones processed...")
            QCoreApplication.processEvents()

    iso_layer.commitChanges()
    print(f"[{layer_name}] Done. Field '{OUTPUT_FIELD}' written.")
    return results


def main():
    project = QgsProject.instance()

    static_layers = project.mapLayersByName(ISOCHRONE_LAYER_STATIC_NAME)
    rt_layers = project.mapLayersByName(ISOCHRONE_LAYER_RT_NAME)
    pop_layers = project.mapLayersByName(POPULATION_LAYER_NAME)
    if not static_layers:
        raise RuntimeError(f"Layer '{ISOCHRONE_LAYER_STATIC_NAME}' not found in the current project.")
    if not rt_layers:
        raise RuntimeError(f"Layer '{ISOCHRONE_LAYER_RT_NAME}' not found in the current project.")
    if not pop_layers:
        raise RuntimeError(f"Layer '{POPULATION_LAYER_NAME}' not found in the current project.")
    static_layer = static_layers[0]
    rt_layer = rt_layers[0]
    pop_layer = pop_layers[0]

    _validate_isochrone_layer(static_layer, ISOCHRONE_LAYER_STATIC_NAME)
    _validate_isochrone_layer(rt_layer, ISOCHRONE_LAYER_RT_NAME)
    if pop_layer.fields().indexFromName(POPULATION_FIELD) < 0:
        raise RuntimeError(f"Population layer has no field '{POPULATION_FIELD}'.")
    if pop_layer.geometryType() != QgsWkbTypes.PolygonGeometry:
        raise RuntimeError("Population layer must be polygonal.")
    if pop_layer.crs().isGeographic():
        raise RuntimeError(
            "Population layer must be in a projected CRS with metric units "
            f"(e.g. EPSG:2180, EPSG:3857). Got: {pop_layer.crs().authid()}."
        )

    geom_by_fid, density_by_fid, index = _build_population_index(pop_layer, POPULATION_FIELD)
    print(f"Population index ready: {len(geom_by_fid)} valid tracts out of {pop_layer.featureCount()}.")

    results_static = _compute_coverage(
        static_layer, "static", pop_layer, geom_by_fid, density_by_fid, index, project
    )
    results_rt = _compute_coverage(
        rt_layer, "rt", pop_layer, geom_by_fid, density_by_fid, index, project
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
            xs, ys = zip(*rt_series[cutoff_value])
            ax.plot(
                xs, ys, color=color_cycle[color_index % len(color_cycle)], linestyle="None", marker="s", markersize=3,
                label=f"GTFS-RT{cutoff_label}",
            )
            color_index += 1

    ax.set_xlabel("Time")
    ax.set_ylabel("Population covered")
    ax.set_title("Population covered by isochrone: GTFS-static vs GTFS-RT")
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
