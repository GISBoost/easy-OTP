"""Hexagonal grid generator algorithm and shared build_hex_grid / extent helpers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsCoordinateTransformContext,
    QgsFeatureSink,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterExtent,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterNumber,
    QgsRectangle,
)
from qgis.PyQt.QtCore import QCoreApplication

if TYPE_CHECKING:
    from qgis.core import (
        QgsProcessingContext,
        QgsProcessingFeedback,
        QgsVectorLayer,
    )


def build_hex_grid(
    extent: "QgsRectangle",
    extent_crs: QgsCoordinateReferenceSystem,
    cell_size_m: float,
    context: "QgsProcessingContext",
    feedback: "QgsProcessingFeedback",
    buffer_m: float = 0.0,
) -> "QgsVectorLayer":
    """Generate a hexagonal polygon grid covering *extent* with *cell_size_m* spacing.

    Output is always EPSG:3857.  ``cell_size_m`` is the flat-to-flat hexagon
    width in metres (HSPACING = VSPACING passed to native:creategrid TYPE 4).
    ``buffer_m`` expands the EPSG:3857 extent by this many metres on each side
    before generating the grid (useful to add a context ring around the core area).
    """
    import processing  # noqa: PLC0415 — available only inside the QGIS interpreter

    crs_3857 = QgsCoordinateReferenceSystem("EPSG:3857")
    transform = QgsCoordinateTransform(extent_crs, crs_3857, QgsCoordinateTransformContext())
    extent_3857 = transform.transformBoundingBox(extent)

    if buffer_m > 0:
        extent_3857.grow(buffer_m)

    extent_str = (
        f"{extent_3857.xMinimum()},{extent_3857.xMaximum()},"
        f"{extent_3857.yMinimum()},{extent_3857.yMaximum()} [EPSG:3857]"
    )

    result = processing.run(
        "native:creategrid",
        {
            "TYPE": 4,  # Hexagons (polygon)
            "EXTENT": extent_str,
            "HSPACING": cell_size_m,
            "VSPACING": cell_size_m,
            "HOVERLAY": 0,
            "VOVERLAY": 0,
            "CRS": crs_3857,
            "OUTPUT": "TEMPORARY_OUTPUT",
        },
        context=context,
        feedback=feedback,
        is_child_algorithm=True,
    )
    return context.getMapLayer(result["OUTPUT"])


def extent_of_count_nonzero(
    count_raster_path: Path,
) -> "tuple[QgsRectangle, QgsCoordinateReferenceSystem] | None":
    """Return the tight bbox of pixels with count > 0 in the count raster.

    The count raster uses NoData=0, so ``count > 0`` identifies cells that were
    accessible at least once within the travel-time threshold.  This gives the
    tightest meaningful grid extent: the transit-service area for the chosen
    threshold, independent of the OTP router/GTFS coverage area.

    Returns None only if no pixel has count > 0 (origin completely outside the
    transit network for the given threshold and time window).
    """
    from osgeo import gdal  # noqa: PLC0415 — available only inside the QGIS interpreter
    import numpy as np      # noqa: PLC0415 — bundled with QGIS

    ds = gdal.Open(str(count_raster_path))
    band = ds.GetRasterBand(1)
    data = band.ReadAsArray()
    gt = ds.GetGeoTransform()
    proj_wkt = ds.GetProjection()
    ds = None

    valid = data > 0          # non-NoData in count raster (NoData=0)
    rows = np.any(valid, axis=1)
    cols = np.any(valid, axis=0)
    if not rows.any():
        return None

    rmin = int(np.argmax(rows))
    rmax = int(len(rows) - 1 - np.argmax(rows[::-1]))
    cmin = int(np.argmax(cols))
    cmax = int(len(cols) - 1 - np.argmax(cols[::-1]))

    # Convert pixel indices to geographic coordinates.
    # gt[1] = pixel width (positive), gt[5] = pixel height (negative for north-up rasters).
    xmin = gt[0] + cmin * gt[1]
    xmax = gt[0] + (cmax + 1) * gt[1]
    ymax = gt[3] + rmin * gt[5]
    ymin = gt[3] + (rmax + 1) * gt[5]

    crs = QgsCoordinateReferenceSystem()
    crs.createFromWkt(proj_wkt)
    return QgsRectangle(xmin, ymin, xmax, ymax), crs


class GenerateHexGrid(QgsProcessingAlgorithm):
    EXTENT = "EXTENT"
    CELL_SIZE = "CELL_SIZE"
    OUTPUT = "OUTPUT"

    def tr(self, string: str) -> str:
        return QCoreApplication.translate(type(self).__name__, string)

    def name(self) -> str:
        return "generatehexgrid"

    def displayName(self) -> str:  # noqa: N802 — Qt API name
        return self.tr("Generate hexagonal grid")

    def group(self) -> str:
        return self.tr("3 · Analysis")

    def groupId(self) -> str:  # noqa: N802 — Qt API name
        return "analysis"

    def createInstance(self):  # noqa: N802 — Qt API name
        return GenerateHexGrid()

    def shortHelpString(self) -> str:  # noqa: N802 — Qt API name
        return self.tr(
            "Generates a hexagonal polygon grid covering the given extent.\n\n"
            "Output CRS is EPSG:3857 (Web Mercator). Cell size is the "
            "flat-to-flat hexagon width in metres. Default 500 m matches the "
            "spatial resolution used in the accessibility article.\n\n"
            "Use this to pre-generate the grid for Run temporal accessibility "
            "when you need a custom extent or want to inspect the grid first."
        )

    def initAlgorithm(self, config=None):  # noqa: N802 — Qt API name
        self.addParameter(
            QgsProcessingParameterExtent(
                self.EXTENT,
                self.tr("Grid extent"),
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.CELL_SIZE,
                self.tr("Cell size (m)"),
                type=QgsProcessingParameterNumber.Double,
                defaultValue=500.0,
                minValue=1.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                self.tr("Hexagonal grid"),
                type=QgsProcessing.TypeVectorPolygon,
            )
        )

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802 — Qt API name
        extent = self.parameterAsExtent(parameters, self.EXTENT, context)
        extent_crs = self.parameterAsExtentCrs(parameters, self.EXTENT, context)
        cell_size = self.parameterAsDouble(parameters, self.CELL_SIZE, context)

        hex_layer = build_hex_grid(extent, extent_crs, cell_size, context, feedback)

        sink, dest_id = self.parameterAsSink(
            parameters, self.OUTPUT, context,
            hex_layer.fields(),
            hex_layer.wkbType(),
            hex_layer.sourceCrs(),
        )
        for feat in hex_layer.getFeatures():
            if feedback.isCanceled():
                break
            sink.addFeature(feat, QgsFeatureSink.FastInsert)

        return {self.OUTPUT: dest_id}
