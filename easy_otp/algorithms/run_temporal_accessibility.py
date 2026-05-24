"""Main algorithm: temporal accessibility pipeline via OpenTripPlanner.

Milestone 1: parameters declared per PR section 8.1; no pipeline logic yet.
Subsequent milestones add OTP server management, surface generation, raster
stacking, zonal statistics and 4-category classification.
"""

from qgis.PyQt.QtCore import QCoreApplication, QDate, QDateTime, QTime
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterDateTime,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFile,
    QgsProcessingParameterNumber,
    QgsProcessingParameterPoint,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterString,
    QgsProcessingParameterVectorLayer,
)


class RunTemporalAccessibility(QgsProcessingAlgorithm):
    OSM_PBF = "OSM_PBF"
    GTFS_FILES = "GTFS_FILES"
    DESTINATION = "DESTINATION"
    HEX_GRID = "HEX_GRID"
    GENERATE_GRID = "GENERATE_GRID"
    GRID_CELL_SIZE = "GRID_CELL_SIZE"

    ANALYSIS_DATE = "ANALYSIS_DATE"
    TIME_START = "TIME_START"
    TIME_END = "TIME_END"
    INTERVAL = "INTERVAL"
    TRAVEL_TIME_THRESHOLD = "TRAVEL_TIME_THRESHOLD"

    WALK_RELUCTANCE = "WALK_RELUCTANCE"
    WAIT_RELUCTANCE = "WAIT_RELUCTANCE"
    TRANSFER_PENALTY = "TRANSFER_PENALTY"
    MIN_TRANSFER_TIME = "MIN_TRANSFER_TIME"
    MAX_WALK_DISTANCE = "MAX_WALK_DISTANCE"
    WALK_SPEED = "WALK_SPEED"

    JAVA_PATH = "JAVA_PATH"
    OTP_JAR_PATH = "OTP_JAR_PATH"
    OTP_XMX_BUILD = "OTP_XMX_BUILD"
    OTP_XMX_SERVE = "OTP_XMX_SERVE"
    OTP_PORT = "OTP_PORT"
    EXISTING_GRAPH_DIR = "EXISTING_GRAPH_DIR"
    KEEP_SERVER_ALIVE = "KEEP_SERVER_ALIVE"

    WORK_DIR = "WORK_DIR"
    OUTPUT_HEX = "OUTPUT_HEX"
    OUTPUT_COUNT_RASTER = "OUTPUT_COUNT_RASTER"

    INTERVAL_CHOICES = ["1 min", "15 min", "60 min"]

    def tr(self, string: str) -> str:
        return QCoreApplication.translate("Processing", string)

    def name(self) -> str:
        return "runtemporalaccessibility"

    def displayName(self) -> str:  # noqa: N802 — Qt API name
        return self.tr("Run temporal accessibility")

    def group(self) -> str:
        return self.tr("Analysis")

    def groupId(self) -> str:  # noqa: N802 — Qt API name
        return "analysis"

    def createInstance(self):  # noqa: N802 — Qt API name
        return RunTemporalAccessibility()

    def shortHelpString(self) -> str:  # noqa: N802 — Qt API name
        return self.tr(
            "Runs the full temporal-accessibility pipeline against an "
            "OpenTripPlanner 1.5.0 instance: generates one travel-time "
            "surface per minute across the configured time window, stacks "
            "and counts surfaces below the travel-time threshold, and "
            "aggregates the result into a hexagonal grid with a "
            "4-category service-time classification.\n\n"
            "Requires user-provided Java 8 and otp-1.5.0-shaded.jar."
        )

    def initAlgorithm(self, config=None):  # noqa: N802 — Qt API name
        # --- Data inputs ---
        self.addParameter(
            QgsProcessingParameterFile(
                self.OSM_PBF,
                self.tr("OSM extract (.osm.pbf)"),
                behavior=QgsProcessingParameterFile.File,
                extension="pbf",
            )
        )
        self.addParameter(
            QgsProcessingParameterFile(
                self.GTFS_FILES,
                self.tr("GTFS folder (containing one or more .zip feeds)"),
                behavior=QgsProcessingParameterFile.Folder,
            )
        )
        self.addParameter(
            QgsProcessingParameterPoint(
                self.DESTINATION,
                self.tr("Destination point"),
            )
        )
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.HEX_GRID,
                self.tr("Hexagonal grid (optional, polygon layer)"),
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

        # --- Time / analysis ---
        self.addParameter(
            QgsProcessingParameterDateTime(
                self.ANALYSIS_DATE,
                self.tr("Analysis date"),
                type=QgsProcessingParameterDateTime.Date,
                defaultValue=QDateTime(QDate.currentDate(), QTime(0, 0)),
            )
        )
        self.addParameter(
            QgsProcessingParameterDateTime(
                self.TIME_START,
                self.tr("Window start time"),
                type=QgsProcessingParameterDateTime.Time,
                defaultValue=QDateTime(QDate(2000, 1, 1), QTime(6, 0)),
            )
        )
        self.addParameter(
            QgsProcessingParameterDateTime(
                self.TIME_END,
                self.tr("Window end time"),
                type=QgsProcessingParameterDateTime.Time,
                defaultValue=QDateTime(QDate(2000, 1, 1), QTime(22, 0)),
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.INTERVAL,
                self.tr("Sampling interval"),
                options=self.INTERVAL_CHOICES,
                defaultValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.TRAVEL_TIME_THRESHOLD,
                self.tr("Travel-time threshold (min)"),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=30,
                minValue=1,
                maxValue=120,
            )
        )

        # --- OTP routing (advanced) ---
        self._add_advanced(
            QgsProcessingParameterNumber(
                self.WALK_RELUCTANCE,
                self.tr("Walk reluctance"),
                type=QgsProcessingParameterNumber.Double,
                defaultValue=3.0,
                minValue=0.0,
            )
        )
        self._add_advanced(
            QgsProcessingParameterNumber(
                self.WAIT_RELUCTANCE,
                self.tr("Wait reluctance"),
                type=QgsProcessingParameterNumber.Double,
                defaultValue=2.0,
                minValue=0.0,
            )
        )
        self._add_advanced(
            QgsProcessingParameterNumber(
                self.TRANSFER_PENALTY,
                self.tr("Transfer penalty (s)"),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=60,
                minValue=0,
            )
        )
        self._add_advanced(
            QgsProcessingParameterNumber(
                self.MIN_TRANSFER_TIME,
                self.tr("Minimum transfer time (s)"),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=60,
                minValue=0,
            )
        )
        self._add_advanced(
            QgsProcessingParameterNumber(
                self.MAX_WALK_DISTANCE,
                self.tr("Maximum walk distance (m)"),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=800,
                minValue=0,
            )
        )
        self._add_advanced(
            QgsProcessingParameterNumber(
                self.WALK_SPEED,
                self.tr("Walk speed (m/s)"),
                type=QgsProcessingParameterNumber.Double,
                defaultValue=1.3,
                minValue=0.1,
            )
        )

        # --- OTP server (advanced) ---
        self._add_advanced(
            QgsProcessingParameterFile(
                self.JAVA_PATH,
                self.tr("Java 8 binary"),
                behavior=QgsProcessingParameterFile.File,
                optional=True,
            )
        )
        self._add_advanced(
            QgsProcessingParameterFile(
                self.OTP_JAR_PATH,
                self.tr("OpenTripPlanner 1.5.0 jar (otp-1.5.0-shaded.jar)"),
                behavior=QgsProcessingParameterFile.File,
                extension="jar",
                optional=True,
            )
        )
        self._add_advanced(
            QgsProcessingParameterString(
                self.OTP_XMX_BUILD,
                self.tr("OTP heap for graph build (e.g. 2G)"),
                defaultValue="2G",
            )
        )
        self._add_advanced(
            QgsProcessingParameterString(
                self.OTP_XMX_SERVE,
                self.tr("OTP heap for analyst server (e.g. 4G)"),
                defaultValue="4G",
            )
        )
        self._add_advanced(
            QgsProcessingParameterNumber(
                self.OTP_PORT,
                self.tr("OTP server port"),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=8801,
                minValue=1,
                maxValue=65535,
            )
        )
        self._add_advanced(
            QgsProcessingParameterFile(
                self.EXISTING_GRAPH_DIR,
                self.tr("Existing graph router directory (skip build)"),
                behavior=QgsProcessingParameterFile.Folder,
                optional=True,
            )
        )
        self._add_advanced(
            QgsProcessingParameterBoolean(
                self.KEEP_SERVER_ALIVE,
                self.tr("Keep OTP server alive after run"),
                defaultValue=True,
            )
        )

        # --- Working directory and outputs ---
        self.addParameter(
            QgsProcessingParameterFile(
                self.WORK_DIR,
                self.tr("Working directory (intermediate surfaces, graph, cache)"),
                behavior=QgsProcessingParameterFile.Folder,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT_HEX,
                self.tr("Output hex grid (service-time + classification)"),
                type=QgsProcessing.TypeVectorPolygon,
            )
        )
        self.addParameter(
            QgsProcessingParameterRasterDestination(
                self.OUTPUT_COUNT_RASTER,
                self.tr("Output count raster"),
            )
        )

    def _add_advanced(self, param) -> None:
        param.setFlags(param.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(param)

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802 — Qt API name
        feedback.pushInfo(self.tr(
            "Milestone 1 skeleton: parameters received but no pipeline logic "
            "is implemented yet. Real processing arrives in milestones 2-7."
        ))
        for key in sorted(parameters.keys()):
            feedback.pushInfo(f"  {key} = {parameters[key]!r}")
        return {}
