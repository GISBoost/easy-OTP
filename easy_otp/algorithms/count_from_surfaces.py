"""CountFromExistingSurfaces: count reachable minutes from pre-generated TIFFs.

Allows re-running the full pipeline (count → zonal stats → classification)
on an existing folder of surface_*.tiff files without re-running OTP. Useful for:
- testing the counting and classification logic in isolation
- re-counting with a different TRAVEL_TIME_THRESHOLD
- re-generating the hex classification without the ~22-min OTP surface loop

Delegates counting to :func:`~easy_otp.core.raster_processing.count_below_threshold`
and zonal/classification to :mod:`~easy_otp.core.zonal`.
"""

from __future__ import annotations

from pathlib import Path

from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsFeatureSink,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingFeedback,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFile,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterVectorLayer,
    QgsProcessingUtils,
)

from ..core.raster_processing import count_below_threshold
from ..core.time_utils import INTERVAL_MINUTES
from ..core.zonal import classify_service_time, log_summary_stats, run_zonal_stats
from .generate_hex_grid import build_hex_grid, extent_of_count_nonzero


class CountFromExistingSurfaces(QgsProcessingAlgorithm):
    SURFACES_FOLDER = "SURFACES_FOLDER"
    TRAVEL_TIME_THRESHOLD = "TRAVEL_TIME_THRESHOLD"
    INTERVAL = "INTERVAL"
    HEX_GRID = "HEX_GRID"
    GENERATE_GRID = "GENERATE_GRID"
    GRID_CELL_SIZE = "GRID_CELL_SIZE"
    OUTPUT_COUNT_RASTER = "OUTPUT_COUNT_RASTER"
    OUTPUT_HEX = "OUTPUT_HEX"

    EXPORT_REPORT = "EXPORT_REPORT"
    REPORT_PATH = "REPORT_PATH"

    INTERVAL_CHOICES = ["1 min", "15 min", "60 min"]

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
            "(pixel never within threshold).\n\n"
            "Set INTERVAL to match the sampling interval used when generating "
            "the surfaces — it is used to convert surface counts to minutes "
            "for the 4-category classification.\n\n"
            "Optionally, if a hexagonal grid is supplied, also runs zonal "
            "statistics and 4-category service-time classification, producing "
            "an OUTPUT_HEX layer styled with service_time.qml.\n\n"
            "Use this to re-run the full pipeline without re-generating "
            "surfaces via OTP (~22 min saved)."
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
            QgsProcessingParameterEnum(
                self.INTERVAL,
                self.tr("Sampling interval of the surfaces"),
                options=[self.tr(s) for s in self.INTERVAL_CHOICES],
                defaultValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterRasterDestination(
                self.OUTPUT_COUNT_RASTER,
                self.tr("Output count raster"),
            )
        )
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.HEX_GRID,
                self.tr("Hexagonal grid (optional; leave blank when 'Generate hex grid' is checked)"),
                types=[QgsProcessing.TypeVectorPolygon],
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.GENERATE_GRID,
                self.tr("Generate hex grid instead of using supplied layer"),
                defaultValue=False,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.GRID_CELL_SIZE,
                self.tr("Hex grid cell size (m)"),
                type=QgsProcessingParameterNumber.Double,
                defaultValue=500.0,
                minValue=1.0,
            )
        )
        _hex_param = QgsProcessingParameterFeatureSink(
            self.OUTPUT_HEX,
            self.tr("Output hex grid (service-time + classification)"),
            type=QgsProcessing.TypeVectorPolygon,
        )
        _hex_param.setFlags(_hex_param.flags() | QgsProcessingParameterDefinition.FlagOptional)
        self.addParameter(_hex_param)
        _export_param = QgsProcessingParameterBoolean(
            self.EXPORT_REPORT,
            self.tr("Export statistics report"),
            defaultValue=False,
            optional=True,
        )
        _export_param.setFlags(
            _export_param.flags() | QgsProcessingParameterDefinition.FlagAdvanced
        )
        self.addParameter(_export_param)
        _report_path_param = QgsProcessingParameterFileDestination(
            self.REPORT_PATH,
            self.tr("Report file (.xlsx or .csv)"),
            fileFilter=self.tr("Excel files (*.xlsx);;CSV files (*.csv)"),
            optional=True,
            createByDefault=False,
        )
        _report_path_param.setFlags(
            _report_path_param.flags() | QgsProcessingParameterDefinition.FlagAdvanced
        )
        self.addParameter(_report_path_param)

    def processAlgorithm(  # noqa: N802
        self,
        parameters: dict,
        context: QgsProcessingContext,
        feedback: QgsProcessingFeedback,
    ) -> dict:
        folder = self.parameterAsString(parameters, self.SURFACES_FOLDER, context)
        threshold_min = self.parameterAsInt(parameters, self.TRAVEL_TIME_THRESHOLD, context)
        interval_idx = self.parameterAsEnum(parameters, self.INTERVAL, context)
        interval_min = INTERVAL_MINUTES[interval_idx]
        out_count_str = self.parameterAsOutputLayer(parameters, self.OUTPUT_COUNT_RASTER, context)

        # Lexicographic sort == chronological for surface_HH-MM-SS.tiff naming.
        surfaces = sorted(Path(folder).glob("surface_*.tiff"))
        if not surfaces:
            raise QgsProcessingException(self.tr(
                f"No surface_*.tiff files found in: {folder}"
            ))

        feedback.pushInfo(self.tr(
            f"Found {len(surfaces)} surface(s) in {folder}. "
            f"Threshold: {threshold_min} min, interval: {interval_min} min."
        ))

        out_count_path = Path(out_count_str)
        try:
            count_below_threshold(surfaces, threshold_min, out_count_path, feedback)
        except RuntimeError as e:
            raise QgsProcessingException(str(e)) from e

        feedback.pushInfo(self.tr(f"Count raster written: {out_count_path}"))

        self._output_hex_dest_id = None
        generate_grid = self.parameterAsBool(parameters, self.GENERATE_GRID, context)
        if generate_grid:
            cell_size = self.parameterAsDouble(parameters, self.GRID_CELL_SIZE, context)
            feedback.pushInfo(self.tr(
                f"Generating hex grid from count raster extent (cell size {cell_size} m)…"
            ))
            _extent_result = extent_of_count_nonzero(out_count_path)
            if _extent_result is None:
                raise QgsProcessingException(self.tr(
                    "No pixels were accessible within the travel-time threshold. "
                    "Check TRAVEL_TIME_THRESHOLD or supply a HEX_GRID layer manually."
                ))
            _extent, _extent_crs = _extent_result
            hex_grid = build_hex_grid(
                _extent, _extent_crs, cell_size, context, feedback,
                buffer_m=cell_size * 3,
            )
        else:
            hex_grid = self.parameterAsVectorLayer(parameters, self.HEX_GRID, context)
        if hex_grid is not None:
            feedback.pushInfo(self.tr("Running zonal statistics on count raster…"))
            try:
                zonal_layer = run_zonal_stats(out_count_path, hex_grid, context, feedback)
            except RuntimeError as e:
                raise QgsProcessingException(str(e)) from e

            feedback.pushInfo(self.tr("Classifying service-time categories…"))
            try:
                classified_layer = classify_service_time(
                    zonal_layer, feedback, interval_min=interval_min,
                    n_surfaces=len(surfaces),
                )
            except RuntimeError as e:
                raise QgsProcessingException(str(e)) from e

            sink, dest_id = self.parameterAsSink(
                parameters, self.OUTPUT_HEX, context,
                classified_layer.fields(),
                classified_layer.wkbType(),
                classified_layer.sourceCrs(),
            )
            for feat in classified_layer.getFeatures():
                sink.addFeature(feat, QgsFeatureSink.FastInsert)

            log_summary_stats(classified_layer, feedback)

            if self.parameterAsBool(parameters, self.EXPORT_REPORT, context):
                report_path = self.parameterAsFileOutput(
                    parameters, self.REPORT_PATH, context
                )
                if report_path:
                    from ..core.report_writer import write_report  # noqa: PLC0415
                    actual_path = write_report(
                        classified_layer,
                        {
                            "analysis_date": "",
                            "destination_lat": "",
                            "destination_lon": "",
                            "threshold_min": threshold_min,
                            "window_start": "",
                            "window_end": "",
                            "interval_min": interval_min,
                        },
                        report_path,
                    )
                    feedback.pushInfo(self.tr(
                        f"Statistics report saved to: {actual_path}"
                    ))

            self._output_hex_dest_id = dest_id

        return {
            self.OUTPUT_COUNT_RASTER: str(out_count_path),
            self.OUTPUT_HEX: self._output_hex_dest_id,
        }

    def postProcessAlgorithm(self, context, feedback):  # noqa: N802
        dest_id = getattr(self, "_output_hex_dest_id", None)
        if dest_id:
            layer = QgsProcessingUtils.mapLayerFromString(dest_id, context)
            if layer:
                qml_path = Path(__file__).parent.parent / "styles" / "service_time.qml"
                if qml_path.exists():
                    layer.loadNamedStyle(str(qml_path))
                    layer.triggerRepaint()
        return {}
