"""Main algorithm: temporal accessibility pipeline via OpenTripPlanner.

Milestone 2 scope: validate paths, build (or cache-hit) the OTP graph,
start --analyst --pointSets serve, wait for the router, generate ONE travel
-time surface for ANALYSIS_DATE + TIME_START, and tear down cleanly.
Multi-surface loop, raster stacking and zonal stats arrive in milestones 3-5.
"""

from pathlib import Path

from qgis.PyQt.QtCore import QCoreApplication, QDate, QDateTime, QTime
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
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

from ..core.otp_client import OtpClient, OtpClientError
from ..core.otp_server import (
    OtpServer,
    build_graph,
    compute_router_id,
    discover_gtfs_files,
    ensure_pointsets_dir,
    ensure_router_dir,
    graph_obj_exists,
    probe_otp,
    wait_until_ready,
    write_meta,
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
        java = self._require_file(parameters, context, self.JAVA_PATH, "Java 8 binary")
        jar = self._require_file(parameters, context, self.OTP_JAR_PATH, "OTP 1.5.0 jar")
        pbf = self._require_file(parameters, context, self.OSM_PBF, "OSM .pbf extract")

        gtfs_dir_str = self.parameterAsFile(parameters, self.GTFS_FILES, context)
        if not gtfs_dir_str:
            raise QgsProcessingException(self.tr("GTFS folder is required."))
        gtfs_dir = Path(gtfs_dir_str)
        try:
            gtfs_files = discover_gtfs_files(gtfs_dir)
        except FileNotFoundError as e:
            raise QgsProcessingException(str(e)) from e
        feedback.pushInfo(self.tr(
            f"Discovered {len(gtfs_files)} GTFS feed(s): "
            f"{', '.join(p.name for p in gtfs_files)}"
        ))

        work_dir_str = self.parameterAsFile(parameters, self.WORK_DIR, context)
        if not work_dir_str:
            raise QgsProcessingException(self.tr("Working directory is required."))
        work_dir = Path(work_dir_str)
        work_dir.mkdir(parents=True, exist_ok=True)

        port = self.parameterAsInt(parameters, self.OTP_PORT, context)
        xmx_build = self.parameterAsString(parameters, self.OTP_XMX_BUILD, context) or "2G"
        xmx_serve = self.parameterAsString(parameters, self.OTP_XMX_SERVE, context) or "4G"
        keep_alive = self.parameterAsBool(parameters, self.KEEP_SERVER_ALIVE, context)

        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        pt = self.parameterAsPoint(parameters, self.DESTINATION, context, wgs84)
        # QGIS gives us X=lon, Y=lat. OTP wants "lat,lon".
        from_place_lat_lon = (pt.y(), pt.x())
        feedback.pushInfo(self.tr(
            f"Destination (lat, lon) sent to OTP: "
            f"({from_place_lat_lon[0]:.6f}, {from_place_lat_lon[1]:.6f})"
        ))

        qdt_date = self.parameterAsDateTime(parameters, self.ANALYSIS_DATE, context)
        qdt_time = self.parameterAsDateTime(parameters, self.TIME_START, context)
        date_s = qdt_date.date().toString("MM-dd-yyyy")
        time_s = qdt_time.time().toString("HH:mm:ss")

        router_id = compute_router_id(pbf, gtfs_files)
        feedback.pushInfo(self.tr(f"Router ID: {router_id}"))
        router_dir = ensure_router_dir(work_dir, router_id, pbf, gtfs_files)
        pointsets = ensure_pointsets_dir(work_dir)

        if graph_obj_exists(work_dir, router_id):
            feedback.pushInfo(self.tr("Graph cache hit — skipping build."))
        else:
            feedback.pushInfo(self.tr("Building OTP graph (this can take minutes)…"))
            try:
                build_graph(java, jar, xmx_build, work_dir, router_id, feedback)
            except RuntimeError as e:
                raise QgsProcessingException(str(e)) from e
            write_meta(router_dir, jar, [pbf, *gtfs_files])

        existing = probe_otp(port)
        server_ctx = None
        try:
            if existing:
                ver = existing.get("serverVersion", {})
                ver_str = ver.get("version") if isinstance(ver, dict) else str(ver)
                feedback.pushInfo(self.tr(
                    f"Reusing OTP already running on port {port} (version {ver_str})."
                ))
            else:
                feedback.pushInfo(self.tr(f"Starting OTP server on port {port}…"))
                server_ctx = OtpServer(
                    java_path=java,
                    jar_path=jar,
                    xmx=xmx_serve,
                    work_dir=work_dir,
                    router_id=router_id,
                    port=port,
                    pointsets_dir=pointsets,
                    keep_alive=keep_alive,
                    feedback=feedback,
                )
                server_ctx.__enter__()

            client = OtpClient(port=port, router=router_id)
            try:
                wait_until_ready(
                    client,
                    feedback,
                    timeout_s=300.0,
                    log_path=server_ctx.log_path if server_ctx else None,
                    proc=server_ctx.proc if server_ctx else None,
                )
            except RuntimeError as e:
                raise QgsProcessingException(str(e)) from e

            surfaces_dir = work_dir / "surfaces"
            surfaces_dir.mkdir(parents=True, exist_ok=True)
            out_path = surfaces_dir / f"surface_{time_s.replace(':', '-')}.tiff"
            feedback.pushInfo(self.tr(
                f"Requesting surface for date={date_s} time={time_s}…"
            ))
            try:
                surface_id = client.create_surface(
                    from_place_lat_lon=from_place_lat_lon,
                    date_mmddyyyy=date_s,
                    time_hhmmss=time_s,
                    max_walk_distance=self.parameterAsInt(parameters, self.MAX_WALK_DISTANCE, context),
                    walk_reluctance=self.parameterAsDouble(parameters, self.WALK_RELUCTANCE, context),
                    wait_reluctance=self.parameterAsDouble(parameters, self.WAIT_RELUCTANCE, context),
                    transfer_penalty=self.parameterAsInt(parameters, self.TRANSFER_PENALTY, context),
                    min_transfer_time=self.parameterAsInt(parameters, self.MIN_TRANSFER_TIME, context),
                    walk_speed=self.parameterAsDouble(parameters, self.WALK_SPEED, context),
                )
                client.download_surface_raster(surface_id, out_path)
            except OtpClientError as e:
                raise QgsProcessingException(self.tr(
                    f"OTP surface generation failed: {e}. "
                    f"Verify the destination is inside the graph extent and "
                    f"that ANALYSIS_DATE falls within the GTFS service calendar."
                )) from e

            feedback.pushInfo(self.tr(f"Test surface written: {out_path}"))
            feedback.pushInfo(self.tr(
                "Milestone 2 complete: one surface generated. "
                "Multi-surface loop, raster stacking and zonal stats arrive in milestones 3-5."
            ))
            if server_ctx is not None:
                server_ctx.__exit__(None, None, None)
                server_ctx = None
            return {}
        except BaseException:
            if server_ctx is not None:
                server_ctx.__exit__(*self._exc_info())
            raise

    def _require_file(self, parameters, context, key: str, label: str) -> Path:
        raw = self.parameterAsFile(parameters, key, context)
        if not raw:
            raise QgsProcessingException(self.tr(
                f"{label} is required (parameter {key})."
            ))
        path = Path(raw)
        if not path.is_file():
            raise QgsProcessingException(self.tr(
                f"{label} not found at: {path} (parameter {key})."
            ))
        return path

    @staticmethod
    def _exc_info():
        import sys
        return sys.exc_info()
