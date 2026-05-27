"""CountFromExistingSurfaces: count reachable minutes from pre-generated TIFFs.

Allows re-running only the counting step (PR section 8.2 steps 7-8) on an
existing folder of surface_*.tiff files without re-running OTP. Useful for:
- testing the counting logic in isolation
- re-counting with a different TRAVEL_TIME_THRESHOLD

Delegates entirely to :func:`~easy_otp.core.raster_processing.count_below_threshold`.
"""

from __future__ import annotations

from pathlib import Path

from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingFeedback,
    QgsProcessingParameterFile,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
)

from ..core.raster_processing import count_below_threshold


class CountFromExistingSurfaces(QgsProcessingAlgorithm):
    SURFACES_FOLDER = "SURFACES_FOLDER"
    TRAVEL_TIME_THRESHOLD = "TRAVEL_TIME_THRESHOLD"
    OUTPUT_COUNT_RASTER = "OUTPUT_COUNT_RASTER"

    def tr(self, string: str) -> str:
        return QCoreApplication.translate("Processing", string)

    def name(self) -> str:
        return "countfromexistingsurfaces"

    def displayName(self) -> str:  # noqa: N802
        return self.tr("Count reachable minutes from existing surfaces")

    def group(self) -> str:
        return self.tr("Analysis")

    def groupId(self) -> str:  # noqa: N802
        return "analysis"

    def shortHelpString(self) -> str:  # noqa: N802
        return self.tr(
            "Counts, for each pixel, how many surface_*.tiff files in the "
            "given folder have a travel-time value ≤ TRAVEL_TIME_THRESHOLD. "
            "Writes a single-band Int32 GeoTIFF where 0 means NoData "
            "(pixel never within threshold). "
            "Use this to re-run the counting step without re-generating "
            "surfaces via OTP."
        )

    def createInstance(self):  # noqa: N802
        return CountFromExistingSurfaces()

    def initAlgorithm(self, config=None):  # noqa: N802
        self.addParameter(
            QgsProcessingParameterFile(
                self.SURFACES_FOLDER,
                self.tr("Folder with surface_*.tiff files"),
                behavior=QgsProcessingParameterFile.Folder,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.TRAVEL_TIME_THRESHOLD,
                self.tr("Travel-time threshold (minutes)"),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=30,
                minValue=1,
                maxValue=120,
            )
        )
        self.addParameter(
            QgsProcessingParameterRasterDestination(
                self.OUTPUT_COUNT_RASTER,
                self.tr("Output count raster"),
            )
        )

    def processAlgorithm(  # noqa: N802
        self,
        parameters: dict,
        context: QgsProcessingContext,
        feedback: QgsProcessingFeedback,
    ) -> dict:
        folder = self.parameterAsString(parameters, self.SURFACES_FOLDER, context)
        threshold_min = self.parameterAsInt(parameters, self.TRAVEL_TIME_THRESHOLD, context)
        out_count_str = self.parameterAsOutputLayer(parameters, self.OUTPUT_COUNT_RASTER, context)

        # Lexicographic sort == chronological for surface_HH-MM-SS.tiff naming.
        surfaces = sorted(Path(folder).glob("surface_*.tiff"))
        if not surfaces:
            raise QgsProcessingException(self.tr(
                f"No surface_*.tiff files found in: {folder}"
            ))

        feedback.pushInfo(self.tr(
            f"Found {len(surfaces)} surface(s) in {folder}. "
            f"Threshold: {threshold_min} min."
        ))

        out_count_path = Path(out_count_str)
        try:
            count_below_threshold(surfaces, threshold_min, out_count_path, feedback)
        except RuntimeError as e:
            raise QgsProcessingException(str(e)) from e

        feedback.pushInfo(self.tr(
            f"Count raster written: {out_count_path}"
        ))
        return {self.OUTPUT_COUNT_RASTER: str(out_count_path)}
