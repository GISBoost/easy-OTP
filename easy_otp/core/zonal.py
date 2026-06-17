"""Zonal statistics and service-time classification for the hex grid."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qgis.core import QgsProcessingContext, QgsProcessingFeedback, QgsVectorLayer


def _tr(string: str) -> str:
    from qgis.PyQt.QtCore import QCoreApplication
    return QCoreApplication.translate("Processing", string)


# Reference analysis window from the paper (06:00–22:00 = 16 h = 960 min).
# Thresholds scale proportionally for shorter windows; capped at 1.0 so the
# original absolute values (720 / 360 / 180 min) apply for the full window.
_REFERENCE_WINDOW_MIN = 960

# "" is the inaccessible sentinel stored in st_class; matches QML value=""
_CATEGORY_ORDER = [
    "constantly accessible",
    "regularly accessible",
    "periodically accessible",
    "episodically accessible",
    "",
]


def _classify_value(val: "float | None", interval_min: int, n_surfaces: int) -> str:
    """Pure Python: otp_mean float → service-time category string.

    No QGIS dependency — safe to import and test outside the QGIS interpreter.
    ``val`` is the zonal mean of the count raster (number of timestamps where
    travel-time ≤ threshold). ``interval_min`` is the sampling interval in
    minutes (1, 15, or 60). ``n_surfaces`` is the total number of surfaces
    generated for this run.

    Thresholds scale proportionally when the analysis window is shorter than
    the paper's reference window (960 min = 06:00–22:00); they are capped at
    the original absolute values (720 / 360 / 180 min) for full-window runs.
    """
    if val is None:
        return ""
    try:
        fval = float(val)
    except (TypeError, ValueError):
        return ""
    if fval <= 0:
        return ""
    service_min = fval * interval_min
    scale = min(1.0, (n_surfaces * interval_min) / _REFERENCE_WINDOW_MIN)
    if service_min >= 720 * scale:
        return "constantly accessible"
    if service_min >= 360 * scale:
        return "regularly accessible"
    if service_min >= 180 * scale:
        return "periodically accessible"
    return "episodically accessible"


def run_zonal_stats(
    count_raster_path: Path,
    hex_layer: "QgsVectorLayer",
    context: "QgsProcessingContext",
    feedback: "QgsProcessingFeedback",
) -> "QgsVectorLayer":
    """Aggregate count raster to hex cells; return layer with ``otp_mean`` field.

    CRS differences between raster and grid are handled internally by
    native:zonalstatisticsfb (it transforms the vector features to the raster's
    CRS for sampling), so no raster reprojection is performed here.
    """
    import processing  # noqa: PLC0415 — available only inside the QGIS interpreter
    from qgis.core import QgsRasterLayer  # noqa: PLC0415

    raster_layer = QgsRasterLayer(str(count_raster_path), "count_raster", "gdal")
    if not raster_layer.isValid():
        raise RuntimeError(
            f"Cannot load count raster as QgsRasterLayer: {count_raster_path}"
        )

    raster_crs = raster_layer.crs()
    grid_crs = hex_layer.crs()
    if raster_crs != grid_crs:
        feedback.pushInfo(_tr(
            f"Raster CRS ({raster_crs.authid()}) differs from grid CRS "
            f"({grid_crs.authid()}); native:zonalstatisticsfb will handle "
            f"the transform internally."
        ))

    result = processing.run(
        "native:zonalstatisticsfb",
        {
            "INPUT": hex_layer,
            "INPUT_RASTER": raster_layer,
            "RASTER_BAND": 1,
            "COLUMN_PREFIX": "otp_",
            "STATISTICS": [2],  # mean
            "OUTPUT": "TEMPORARY_OUTPUT",
        },
        context=context,
        feedback=feedback,
        is_child_algorithm=True,
    )
    return context.getMapLayer(result["OUTPUT"])


def classify_service_time(
    zonal_layer: "QgsVectorLayer",
    feedback: "QgsProcessingFeedback",
    mean_field: str = "otp_mean",
    interval_min: int = 1,
    n_surfaces: int = 961,
) -> "QgsVectorLayer":
    """Add ``st_class`` field with 4 service-time categories.

    ``interval_min`` is the sampling interval in minutes (any integer >= 1).
    Accuracy improves with sampling density; intervals > 1 min are approximations
    (each sample represents ``interval_min`` minutes of service).
    ``n_surfaces`` is the total number of surfaces generated for this run;
    it controls threshold scaling for windows shorter than the reference
    06:00–22:00 window (960 min). Full-window runs are unaffected.

    ``otp_mean`` counts surface timestamps, so service time in minutes =
    ``otp_mean * interval_min``. Inaccessible cells (otp_mean = 0 or NULL)
    get st_class = "" (empty string), which the QML renderer maps to no
    symbol via value="".
    """
    from qgis.core import QgsField  # noqa: PLC0415
    from qgis.PyQt.QtCore import QVariant  # noqa: PLC0415

    provider = zonal_layer.dataProvider()
    provider.addAttributes([QgsField("st_class", QVariant.String, "String", 30)])
    zonal_layer.updateFields()

    mean_idx = zonal_layer.fields().indexOf(mean_field)
    class_idx = zonal_layer.fields().indexOf("st_class")
    if mean_idx == -1:
        raise RuntimeError(
            f"Field '{mean_field}' not found in zonal layer. "
            f"Available fields: {[f.name() for f in zonal_layer.fields()]}"
        )

    attr_map: dict[int, dict[int, object]] = {}
    for feature in zonal_layer.getFeatures():
        raw = feature[mean_idx]
        cat = _classify_value(raw, interval_min, n_surfaces)
        attr_map[feature.id()] = {class_idx: cat}

    provider.changeAttributeValues(attr_map)
    zonal_layer.updateFields()
    feedback.pushInfo(_tr(
        f"Service-time classification complete (interval={interval_min} min)."
    ))
    window_min = n_surfaces * interval_min
    if window_min < _REFERENCE_WINDOW_MIN:
        scale = window_min / _REFERENCE_WINDOW_MIN
        feedback.pushWarning(_tr(
            f"Analysis window ({window_min} min) is shorter than the paper's "
            f"reference window (06:00–22:00 = {_REFERENCE_WINDOW_MIN} min). "
            f"Category thresholds were scaled by ×{scale:.3f} "
            f"(constantly accessible ≥ {720 * scale:.1f} min, "
            f"regularly ≥ {360 * scale:.1f} min, "
            f"periodically ≥ {180 * scale:.1f} min). "
            f"Results are not directly comparable to the Kaczorowski & "
            f"Wróblewski article, which assumes the full 06:00–22:00 window."
        ))
    return zonal_layer


def classify_delta(
    zonal_layer: "QgsVectorLayer",
    feedback: "QgsProcessingFeedback",
    mean_field: str = "otp_mean",
    interval_min: int = 1,
    positive_min: float = 60.0,
    negative_max: float = -60.0,
) -> "QgsVectorLayer":
    """Add ``delta_mean`` (minutes) and ``delta_class`` fields to a zonal layer.

    ``mean_field`` holds the mean count delta (count_B − count_A) per hex cell
    as returned by :func:`run_zonal_stats` on the delta raster.  Converts to
    minutes via ``interval_min``, then classifies:

    - ``"improved"``  — delta_mean ≥ positive_min
    - ``"degraded"``  — delta_mean ≤ negative_max
    - ``"unchanged"`` — otherwise

    NULL/NoData cells (hex cells where the delta raster had no valid pixels)
    get ``delta_class = ""`` and ``delta_mean = NULL``.
    """
    from qgis.core import QgsField  # noqa: PLC0415
    from qgis.PyQt.QtCore import QVariant  # noqa: PLC0415

    provider = zonal_layer.dataProvider()
    provider.addAttributes([
        QgsField("delta_mean", QVariant.Double, "double", 12, 2),
        QgsField("delta_class", QVariant.String, "String", 20),
    ])
    zonal_layer.updateFields()

    mean_idx = zonal_layer.fields().indexOf(mean_field)
    dmean_idx = zonal_layer.fields().indexOf("delta_mean")
    dclass_idx = zonal_layer.fields().indexOf("delta_class")

    if mean_idx == -1:
        raise RuntimeError(
            f"Field '{mean_field}' not found in zonal layer. "
            f"Available fields: {[f.name() for f in zonal_layer.fields()]}"
        )

    attr_map: dict[int, dict[int, object]] = {}
    for feature in zonal_layer.getFeatures():
        raw = feature[mean_idx]
        try:
            count_delta = float(raw)
        except (TypeError, ValueError):
            attr_map[feature.id()] = {dmean_idx: None, dclass_idx: ""}
            continue

        delta_min = count_delta * interval_min
        if delta_min >= positive_min:
            cat = "improved"
        elif delta_min <= negative_max:
            cat = "degraded"
        else:
            cat = "unchanged"

        attr_map[feature.id()] = {dmean_idx: delta_min, dclass_idx: cat}

    provider.changeAttributeValues(attr_map)
    zonal_layer.updateFields()
    feedback.pushInfo(_tr(
        f"Delta classification complete (interval={interval_min} min, "
        f"improved ≥ {positive_min} min, degraded ≤ {negative_max} min)."
    ))
    return zonal_layer


def log_summary_stats(
    layer: "QgsVectorLayer",
    feedback: "QgsProcessingFeedback",
) -> None:
    """Log per-category cell counts and percentages to the Processing log."""
    class_idx = layer.fields().indexOf("st_class")
    counts: dict[str, int] = {}
    total = 0
    for feature in layer.getFeatures():
        total += 1
        val = feature[class_idx]
        # val is always a str ("" or one of the 4 category strings);
        # QPyNullVariant or None fall back to "" (inaccessible).
        key = val if isinstance(val, str) else ""
        counts[key] = counts.get(key, 0) + 1

    feedback.pushInfo(_tr("=== Service-time classification summary ==="))
    for cat in _CATEGORY_ORDER:
        count = counts.get(cat, 0)
        pct = count / total * 100 if total > 0 else 0.0
        label = cat if cat != "" else "inaccessible"
        feedback.pushInfo(_tr(f"  {label}: {count} cells ({pct:.1f}%)"))
    feedback.pushInfo(_tr(f"  Total: {total} cells"))
