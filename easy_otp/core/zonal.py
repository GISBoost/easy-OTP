"""Zonal statistics and service-time classification for the hex grid."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qgis.core import QgsProcessingContext, QgsProcessingFeedback, QgsVectorLayer


def _tr(string: str) -> str:
    from qgis.PyQt.QtCore import QCoreApplication
    return QCoreApplication.translate("Processing", string)


_CATEGORIES = [
    ("constantly accessible", 720),    # [720, ∞)
    ("regularly accessible", 360),     # [360, 720)
    ("periodically accessible", 180),  # [180, 360)
    ("episodically accessible", 0),    # (0, 180)
    # val <= 0 or null → "" (inaccessible, no symbol)
]

# "" is the inaccessible sentinel stored in st_class; matches QML value=""
_CATEGORY_ORDER = [c for c, _ in _CATEGORIES] + [""]


def _classify_value(val: "float | None", interval_min: int) -> str:
    """Pure Python: otp_mean float → service-time category string.

    No QGIS dependency — safe to import and test outside the QGIS interpreter.
    ``val`` is the zonal mean of the count raster (number of timestamps where
    travel-time ≤ threshold). ``interval_min`` is the sampling interval used
    when generating the surfaces (1, 15, or 60 minutes).
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
    if service_min >= 720:
        return "constantly accessible"
    if service_min >= 360:
        return "regularly accessible"
    if service_min >= 180:
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
) -> "QgsVectorLayer":
    """Add ``st_class`` field with 4 service-time categories.

    ``interval_min`` is the sampling interval in minutes (1, 15, or 60).
    ``otp_mean`` counts surface timestamps, so service time in minutes =
    ``otp_mean * interval_min``. Thresholds are always expressed in minutes
    (720 / 360 / 180) regardless of the interval used.

    Inaccessible cells (otp_mean = 0 or NULL) get st_class = "" (empty string),
    which the QML renderer maps to no symbol via value="".
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
        cat = _classify_value(raw, interval_min)
        attr_map[feature.id()] = {class_idx: cat}

    provider.changeAttributeValues(attr_map)
    zonal_layer.updateFields()
    feedback.pushInfo(_tr(
        f"Service-time classification complete (interval={interval_min} min)."
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
