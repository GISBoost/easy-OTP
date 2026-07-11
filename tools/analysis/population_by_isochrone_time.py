"""population_by_isochrone_time.py — population covered per isochrone time step.

Standalone ANALYSIS tooling. NOT part of the easy-OTP plugin and not imported
by it. Ad-hoc script for a one-off question: "how many people from the city's
population are within the isochrone at 06:00, 06:01, ... ?"

Run inside the QGIS Python Console (Plugins -> Python Console -> "Show
Editor", paste, Run) or as a one-off Processing "Script" — it needs the
qgis.core / qgis.PyQt modules bundled with QGIS, plus matplotlib (already
shipped with the QGIS Python distribution; no pip install needed).

Input layers (must already be loaded in the current QGIS project):
  - ISOCHRONE_LAYER_NAME: output of GenerateIsochronesOverTime (N-2) — one
    polygon feature per time step (+ per cutoff, if you ran more than one).
    Expected fields: time, cutoff_min, mode, date.
  - POPULATION_LAYER_NAME: the raw GUS census-tract polygon layer (same one
    used as input to "Prepare student layer" / R1a) — one numeric population
    field per tract, in a PROJECTED metric CRS (e.g. EPSG:2180).

What it does:
  1. For each isochrone feature, area-weighted-interpolates population from
     every intersecting census tract (same method as the plugin's
     PopulationOverlay algorithm: tract population / tract area = density;
     intersection piece area x density, summed over all intersecting tracts).
     A QgsSpatialIndex keeps this to candidate tracts only, not O(N x M).
  2. Writes the result into a new field (OUTPUT_FIELD) on the isochrone
     layer itself, in place — the layer already has one row per time step,
     so no separate output layer is needed.
  3. Plots population_covered vs. time (one line per cutoff_min, if more
     than one) and saves a PNG next to the working directory below.

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

ISOCHRONE_LAYER_NAME = "transit-anim-layer"   # layer name as shown in Layers panel
POPULATION_LAYER_NAME = "pop"     # census-tract polygon layer, projected CRS
POPULATION_FIELD = "pop-all"                   # numeric population field on POPULATION_LAYER_NAME

TIME_FIELD = "time"           # per-row time field on the isochrone layer
CUTOFF_FIELD = "cutoff_min"   # per-row cutoff field (set to None if only one cutoff / not present)
OUTPUT_FIELD = "population_covered"

CHART_START_HOUR = None            # first hour shown on the chart (0-23); set to None to disable
CHART_END_HOUR = None              # last hour shown on the chart (0-23); set to None to disable
CHART_TICK_INTERVAL_MINUTES = 60   # spacing between x-axis ticks, in minutes
CHART_COLOR_CYCLE = None           # list of colour strings, one per cutoff line; None = matplotlib's default cycle

OUTPUT_PNG_PATH = r"C:\Users\Michal\Desktop\easy-OTP\tools\analysis\population_by_isochrone_time-6-7.png"

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
        "as a time. Check that TIME_FIELD points to the right field on the layer."
    )


def _minutes_to_hhmm(minutes: float) -> str:
    total = int(round(minutes)) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def main():
    project = QgsProject.instance()

    iso_layers = project.mapLayersByName(ISOCHRONE_LAYER_NAME)
    pop_layers = project.mapLayersByName(POPULATION_LAYER_NAME)
    if not iso_layers:
        raise RuntimeError(f"Layer '{ISOCHRONE_LAYER_NAME}' not found in the current project.")
    if not pop_layers:
        raise RuntimeError(f"Layer '{POPULATION_LAYER_NAME}' not found in the current project.")
    iso_layer = iso_layers[0]
    pop_layer = pop_layers[0]

    if iso_layer.fields().indexFromName(TIME_FIELD) < 0:
        raise RuntimeError(f"Isochrone layer has no field '{TIME_FIELD}'.")
    if CUTOFF_FIELD and iso_layer.fields().indexFromName(CUTOFF_FIELD) < 0:
        raise RuntimeError(
            f"Isochrone layer has no field '{CUTOFF_FIELD}'. "
            "Set CUTOFF_FIELD = None if the layer only has one cutoff / no such field."
        )
    if pop_layer.fields().indexFromName(POPULATION_FIELD) < 0:
        raise RuntimeError(f"Population layer has no field '{POPULATION_FIELD}'.")
    if pop_layer.geometryType() != QgsWkbTypes.PolygonGeometry:
        raise RuntimeError("Population layer must be polygonal.")
    if pop_layer.crs().isGeographic():
        raise RuntimeError(
            "Population layer must be in a projected CRS with metric units "
            f"(e.g. EPSG:2180, EPSG:3857). Got: {pop_layer.crs().authid()}."
        )

    # --- Precompute population density (persons / m^2) per census tract, in pop_layer's own CRS.
    # Geometries and densities are cached in memory here so the main loop below never has to
    # re-query the population layer's data provider (that repeated per-isochrone disk/DB
    # round-trip — one per time step — was the actual cause of QGIS appearing to hang: the
    # UI thread was stuck making thousands of feature requests with no visible progress).
    # makeValid() is also applied here: OTP 1.5 isochrone/tract geometries can be topologically
    # invalid (self-intersections), and GEOS overlay ops on invalid geometry can become
    # pathologically slow instead of failing fast.

    geom_by_fid = {}
    density_by_fid = {}
    for feat in pop_layer.getFeatures():
        geom = feat.geometry()
        if not geom.isGeosValid():
            geom = geom.makeValid()
        area = geom.area()
        value = feat[POPULATION_FIELD]
        if area <= 0 or value is None or value == NULL:
            continue
        geom_by_fid[feat.id()] = geom
        density_by_fid[feat.id()] = float(value) / area

    index = QgsSpatialIndex()
    for fid, geom in geom_by_fid.items():
        index.addFeature(fid, geom.boundingBox())
    print(f"Population index ready: {len(geom_by_fid)} valid tracts out of {pop_layer.featureCount()}.")

    # --- Isochrone geometries need to be in the population layer's CRS for correct area math ---

    transform = None
    if iso_layer.crs() != pop_layer.crs():
        transform = QgsCoordinateTransform(iso_layer.crs(), pop_layer.crs(), project)

    # --- Add (or reuse) the output field on the isochrone layer ---

    if iso_layer.fields().indexFromName(OUTPUT_FIELD) < 0:
        iso_layer.dataProvider().addAttributes([QgsField(OUTPUT_FIELD, QVariant.Double)])
        iso_layer.updateFields()
    out_idx = iso_layer.fields().indexFromName(OUTPUT_FIELD)

    total = iso_layer.featureCount()
    results = []  # (time_value, cutoff_value, population_covered) for the chart

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
            print(f"{i}/{total} isochrones processed...")
            QCoreApplication.processEvents()

    iso_layer.commitChanges()
    print(f"Done. Field '{OUTPUT_FIELD}' written to layer '{ISOCHRONE_LAYER_NAME}'.")

    _plot_results(results)


def _plot_results(results):
    import matplotlib
    matplotlib.use("Agg")  # headless — QGIS embeds its own Qt event loop
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker

    start_minute = CHART_START_HOUR * 60 if CHART_START_HOUR is not None else None
    end_minute = CHART_END_HOUR * 60 if CHART_END_HOUR is not None else None

    filtered = []
    for time_value, cutoff_value, pop in results:
        minutes = _time_to_minutes(time_value)
        if start_minute is not None and minutes < start_minute:
            continue
        if end_minute is not None and minutes > end_minute:
            continue
        filtered.append((minutes, cutoff_value, pop))

    if not filtered:
        print(
            f"\nNo isochrone time steps fall inside the configured chart window "
            f"({CHART_START_HOUR}:00-{CHART_END_HOUR}:00). Skipping chart."
        )
        return

    filtered.sort(key=lambda row: (row[0], row[1] if row[1] is not None else 0))

    series = {}
    for minutes, cutoff_value, pop in filtered:
        series.setdefault(cutoff_value, []).append((minutes, pop))

    color_cycle = CHART_COLOR_CYCLE or plt.rcParams["axes.prop_cycle"].by_key()["color"]

    fig, ax = plt.subplots(figsize=(12, 6))
    for i, (cutoff_value, points) in enumerate(sorted(series.items(), key=lambda kv: (kv[0] is None, kv[0]))):
        xs = [t for t, _ in points]
        values = [p for _, p in points]
        label = f"{cutoff_value} min" if cutoff_value is not None else "population_covered"
        ax.plot(xs, values, marker="o", markersize=3, label=label, color=color_cycle[i % len(color_cycle)])

    ax.set_xlabel("Time")
    ax.set_ylabel("Population covered")
    ax.set_title("Population covered by isochrone, per time step")
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, pos: _minutes_to_hhmm(x)))
    ax.xaxis.set_major_locator(mticker.MultipleLocator(CHART_TICK_INTERVAL_MINUTES))
    if start_minute is not None and end_minute is not None:
        ax.set_xlim(start_minute, end_minute)
    elif start_minute is not None:
        ax.set_xlim(start_minute, None)
    elif end_minute is not None:
        ax.set_xlim(None, end_minute)
    plt.xticks(rotation=45, ha="right")
    if len(series) > 1:
        ax.legend(title="Cutoff")
    fig.tight_layout()
    fig.savefig(OUTPUT_PNG_PATH, dpi=150)
    print(f"Chart saved to {OUTPUT_PNG_PATH}")


main()
