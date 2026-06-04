"""Main algorithm: full temporal-accessibility pipeline via OpenTripPlanner."""

from pathlib import Path

from qgis.PyQt.QtCore import QCoreApplication, QDate, QDateTime, QSettings, QTime, QVariant
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsFeatureSink,
    QgsField,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterDateTime,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFile,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterNumber,
    QgsProcessingParameterPoint,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterString,
    QgsProcessingParameterVectorLayer,
    QgsProcessingUtils,
)

from ..core.otp_client import OtpClient, OtpClientError
from ..core.otp_server import (
    OtpServer,
    build_graph,
    check_java_version,
    compute_router_id,
    discover_gtfs_files,
    ensure_pointsets_dir,
    ensure_router_config,
    ensure_router_dir,
    graph_build_complete,
    port_is_listening,
    probe_otp,
    wait_until_ready,
    write_meta,
)
from ..core.raster_processing import build_surface_vrt, count_below_threshold
from ..core.surface_runner import SurfaceJobParams, run_surface_loop
from ..core.time_utils import INTERVAL_MINUTES, build_time_list
from ..core.zonal import classify_service_time, log_summary_stats, run_zonal_stats
from .generate_hex_grid import build_hex_grid, extent_of_count_nonzero


class RunTemporalAccessibility(QgsProcessingAlgorithm):
    OSM_PBF = "OSM_PBF"
    GTFS_FILES = "GTFS_FILES"
    ORIGIN_POINT = "ORIGIN_POINT"
    HEX_GRID = "HEX_GRID"
    GENERATE_GRID = "GENERATE_GRID"
    GRID_CELL_SIZE = "GRID_CELL_SIZE"

    ANALYSIS_DATE = "ANALYSIS_DATE"
    TIME_START = "TIME_START"
    TIME_END = "TIME_END"
    INTERVAL = "INTERVAL"
    TRAVEL_TIME_THRESHOLD = "TRAVEL_TIME_THRESHOLD"

    ARRIVE_BY = "ARRIVE_BY"

    WALK_RELUCTANCE = "WALK_RELUCTANCE"
    WAIT_RELUCTANCE = "WAIT_RELUCTANCE"
    TRANSFER_PENALTY = "TRANSFER_PENALTY"
    MIN_TRANSFER_TIME = "MIN_TRANSFER_TIME"
    MAX_WALK_DISTANCE = "MAX_WALK_DISTANCE"
    WALK_SPEED = "WALK_SPEED"

    USE_SAVED_JAVA = "USE_SAVED_JAVA"
    JAVA_PATH = "JAVA_PATH"
    OTP_JAR_PATH = "OTP_JAR_PATH"
    OTP_XMX_BUILD = "OTP_XMX_BUILD"
    OTP_XMX_SERVE = "OTP_XMX_SERVE"
    OTP_PORT = "OTP_PORT"
    EXISTING_GRAPH_DIR = "EXISTING_GRAPH_DIR"
    ROUTER_CONFIG_PATH = "ROUTER_CONFIG_PATH"
    KEEP_SERVER_ALIVE = "KEEP_SERVER_ALIVE"
    SHOW_OTP_CONSOLE = "SHOW_OTP_CONSOLE"

    WORK_DIR = "WORK_DIR"
    OUTPUT_HEX = "OUTPUT_HEX"
    OUTPUT_COUNT_RASTER = "OUTPUT_COUNT_RASTER"

    EXPORT_REPORT = "EXPORT_REPORT"
    REPORT_PATH = "REPORT_PATH"

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
            "Requires user-provided Java 8 and otp-1.5.0-shaded.jar.\n\n"
            "Note: maxWalkDistance may have no effect on surface extent in "
            "OTP analyst mode — the SPT is time-bounded (120 min ceiling), "
            "not distance-bounded. Use walk_speed to control how far the "
            "model walks within that time budget."
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
                self.ORIGIN_POINT,
                self.tr("Origin point (where travel-time analysis starts; OTP fromPlace)"),
            )
        )
        _hex_grid_param = QgsProcessingParameterVectorLayer(
            self.HEX_GRID,
            self.tr("Hexagonal grid (polygon layer; leave blank when 'Generate hex grid' is checked)"),
            types=[QgsProcessing.TypeVectorPolygon],
        )
        _hex_grid_param.setFlags(
            _hex_grid_param.flags() | QgsProcessingParameterDefinition.FlagOptional
        )
        self.addParameter(_hex_grid_param)
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
                defaultValue=QTime(6, 0),
            )
        )
        self.addParameter(
            QgsProcessingParameterDateTime(
                self.TIME_END,
                self.tr("Window end time"),
                type=QgsProcessingParameterDateTime.Time,
                defaultValue=QTime(22, 0),
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
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.ARRIVE_BY,
                self.tr("Arrive by (reverse routing — measure latest departure to arrive at destination by T)"),
                defaultValue=False,
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
                self.tr("Maximum walk distance (m) — limited effect in OTP analyst mode"),
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
            QgsProcessingParameterBoolean(
                self.USE_SAVED_JAVA,
                self.tr(
                    "Use Java path saved by 'Download Java Runtime Environment' (QSettings)"
                ),
                defaultValue=True,
            )
        )
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
            QgsProcessingParameterFile(
                self.ROUTER_CONFIG_PATH,
                self.tr(
                    "Custom router-config.json (optional; overrides the "
                    "auto-generated default and the GTFS-folder convention)"
                ),
                behavior=QgsProcessingParameterFile.File,
                extension="json",
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
        self._add_advanced(
            QgsProcessingParameterBoolean(
                self.SHOW_OTP_CONSOLE,
                self.tr("Show OTP server in a separate console window (Windows; debugging)"),
                defaultValue=False,
            )
        )
        self._add_advanced(
            QgsProcessingParameterBoolean(
                self.EXPORT_REPORT,
                self.tr("Export statistics report"),
                defaultValue=False,
                optional=True,
            )
        )
        self._add_advanced(
            QgsProcessingParameterFileDestination(
                self.REPORT_PATH,
                self.tr("Report file (.xlsx or .csv)"),
                fileFilter=self.tr("Excel files (*.xlsx);;CSV files (*.csv)"),
                optional=True,
                createByDefault=False,
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
        _hex_param = QgsProcessingParameterFeatureSink(
            self.OUTPUT_HEX,
            self.tr("Output hex grid (service-time + classification)"),
            type=QgsProcessing.TypeVectorPolygon,
        )
        _hex_param.setFlags(_hex_param.flags() | QgsProcessingParameterDefinition.FlagOptional)
        self.addParameter(_hex_param)
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
        self._output_hex_dest_id = None
        use_saved = self.parameterAsBool(parameters, self.USE_SAVED_JAVA, context)
        if use_saved:
            saved = QSettings().value("easy_otp/java_path", "")
            if not saved:
                raise QgsProcessingException(self.tr(
                    "No Java path saved in QSettings. Run 'Download Java Runtime "
                    "Environment' first, or uncheck 'Use saved Java path (QSettings)' "
                    "and supply the path manually."
                ))
            java = Path(saved)
            feedback.pushInfo(self.tr(f"Using Java path from QSettings: {java}"))
        else:
            java = self._require_file(parameters, context, self.JAVA_PATH, "Java 8 binary")
        is_java8, java_ver, java_err = check_java_version(java)
        if not is_java8:
            raise QgsProcessingException(self.tr(java_err))
        feedback.pushInfo(self.tr(f"Java OK: version {java_ver}"))

        jar = self._require_file(
            parameters, context, self.OTP_JAR_PATH, "OTP 1.5.0 jar",
            fix_hint=self.tr(
                "Download otp-1.5.0-shaded.jar from Maven Central "
                "(groupId=org.opentripplanner, artifactId=otp, version=1.5.0, "
                "classifier=shaded) and set the 'OpenTripPlanner 1.5.0 jar' parameter."
            ),
        )
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
        show_console = self.parameterAsBool(parameters, self.SHOW_OTP_CONSOLE, context)

        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        pt = self.parameterAsPoint(parameters, self.ORIGIN_POINT, context, wgs84)
        # QGIS gives us X=lon, Y=lat. OTP wants "lat,lon".
        from_place_lat_lon = (pt.y(), pt.x())
        feedback.pushInfo(self.tr(
            f"Origin (lat, lon) sent to OTP: "
            f"({from_place_lat_lon[0]:.6f}, {from_place_lat_lon[1]:.6f})"
        ))

        qdt_date = self.parameterAsDateTime(parameters, self.ANALYSIS_DATE, context)
        date_s = qdt_date.date().toString("MM-dd-yyyy")

        # QgsProcessingParameterDateTime(type=Time) stores values as QTime in QGIS
        # 3.40. parameterAsDateTime() calls QVariant::toDateTime() on a QTime, which
        # returns an invalid QDateTime and falls back to the defaultValue — i.e., the
        # user's input is silently ignored. Read the raw value directly instead.
        raw_start = parameters.get(self.TIME_START)
        raw_end   = parameters.get(self.TIME_END)
        start_t = raw_start if isinstance(raw_start, QTime) else \
                  self.parameterAsDateTime(parameters, self.TIME_START, context).time()
        end_t   = raw_end   if isinstance(raw_end,   QTime) else \
                  self.parameterAsDateTime(parameters, self.TIME_END,   context).time()
        interval_idx = self.parameterAsEnum(parameters, self.INTERVAL, context)
        try:
            interval_min = INTERVAL_MINUTES[interval_idx]
        except KeyError as e:
            raise QgsProcessingException(self.tr(
                f"Unsupported sampling interval index: {interval_idx}."
            )) from e
        try:
            time_list = build_time_list(
                start_t.hour(), start_t.minute(),
                end_t.hour(), end_t.minute(),
                interval_min,
            )
        except ValueError as e:
            raise QgsProcessingException(self.tr(
                f"Invalid time window: {e}"
            )) from e
        feedback.pushInfo(self.tr(
            f"Time window: {len(time_list)} timestamp(s) from "
            f"{time_list[0]} to {time_list[-1]}, every {interval_min} min."
        ))

        self._warn_gtfs_date(gtfs_files, qdt_date.date(), feedback)

        threshold_min = self.parameterAsInt(parameters, self.TRAVEL_TIME_THRESHOLD, context)
        arrive_by = self.parameterAsBool(parameters, self.ARRIVE_BY, context)
        out_count_str = self.parameterAsOutputLayer(parameters, self.OUTPUT_COUNT_RASTER, context)
        if not out_count_str:
            raise QgsProcessingException(self.tr(
                "Output count raster path is required."
            ))
        out_count_path = Path(out_count_str)

        router_config_str = self.parameterAsFile(parameters, self.ROUTER_CONFIG_PATH, context)
        router_config_file = Path(router_config_str) if router_config_str else None

        existing_graph_dir_str = self.parameterAsFile(parameters, self.EXISTING_GRAPH_DIR, context)
        if existing_graph_dir_str:
            existing_dir = Path(existing_graph_dir_str)
            if not (existing_dir / "Graph.obj").exists():
                raise QgsProcessingException(self.tr(
                    f"EXISTING_GRAPH_DIR does not contain Graph.obj: {existing_dir}. "
                    "Point to the router directory (e.g. …/graphs/abc123/)."
                ))
            router_id = existing_dir.name
            router_dir = existing_dir
            server_work_dir = existing_dir.parent.parent
            feedback.pushInfo(self.tr(
                f"Using existing graph: {router_dir} (router_id={router_id}); skipping build."
            ))
            ensure_router_config(router_dir, gtfs_dir, feedback, config_file=router_config_file)
        else:
            server_work_dir = work_dir
            router_id = compute_router_id(pbf, gtfs_files)
            feedback.pushInfo(self.tr(f"Router ID: {router_id}"))
            router_dir = ensure_router_dir(work_dir, router_id, pbf, gtfs_files)
            ensure_router_config(router_dir, gtfs_dir, feedback, config_file=router_config_file)
            if graph_build_complete(work_dir, router_id):
                feedback.pushInfo(self.tr("Graph cache hit — skipping build."))
            else:
                # Check for the "off-by-one" case: user may have set WORK_DIR to
                # the 'graphs' subfolder rather than its parent. In that case the
                # graph lives at work_dir/router_id/ (without the extra 'graphs/').
                _off_by_one = work_dir / router_id / "Graph.obj"
                if _off_by_one.exists():
                    raise QgsProcessingException(self.tr(
                        f"Graph cache miss: expected {work_dir / 'graphs' / router_id}.\n"
                        f"However, a graph was found at {_off_by_one.parent} — "
                        f"WORK_DIR appears to point to the 'graphs' subfolder rather "
                        f"than its parent.\n"
                        f"Fix option A: set WORK_DIR to '{work_dir.parent}'.\n"
                        f"Fix option B: set EXISTING_GRAPH_DIR to '{_off_by_one.parent}'."
                    ))
                feedback.pushInfo(self.tr("Building OTP graph (this can take minutes)…"))
                try:
                    build_graph(java, jar, xmx_build, work_dir, router_id, feedback)
                except RuntimeError as e:
                    raise QgsProcessingException(str(e)) from e
                write_meta(router_dir, jar, [pbf, *gtfs_files])
        pointsets = ensure_pointsets_dir(work_dir)

        existing = probe_otp(port)
        if arrive_by and existing:
            feedback.pushWarning(self.tr(
                "ARRIVE_BY=True with a reused OTP server: if surfaces fail with "
                "HTTP 500, restart the server by setting KEEP_SERVER_ALIVE=False "
                "for one run. Reverse routing may require more heap than forward "
                "routing — ensure OTP_XMX_SERVE is set to at least 4G."
            ))
        server_ctx = None
        try:
            if existing:
                ver = existing.get("serverVersion", {})
                ver_str = ver.get("version") if isinstance(ver, dict) else str(ver)
                feedback.pushInfo(self.tr(
                    f"Reusing OTP already running on port {port} (version {ver_str})."
                ))
                if show_console:
                    feedback.pushInfo(self.tr(
                        "Note: SHOW_OTP_CONSOLE has no effect when reusing an "
                        "existing OTP server. Stop the running server (or run "
                        "with KEEP_SERVER_ALIVE=False once) to start a fresh "
                        "instance with the console window."
                    ))
            else:
                if port_is_listening(port):
                    raise QgsProcessingException(self.tr(
                        f"Port {port} is held by a non-OTP process. Pick a "
                        f"different OTP_PORT or stop the conflicting service. "
                        f"Run TestOtpServer for details."
                    ))
                feedback.pushInfo(self.tr(f"Starting OTP server on port {port}…"))
                server_ctx = OtpServer(
                    java_path=java,
                    jar_path=jar,
                    xmx=xmx_serve,
                    work_dir=server_work_dir,
                    router_id=router_id,
                    port=port,
                    pointsets_dir=pointsets,
                    keep_alive=keep_alive,
                    feedback=feedback,
                    show_console=show_console,
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

            router_bbox = self._log_router_diagnostic(client, feedback)

            date_slug = date_s.replace("-", "")  # "MM-DD-YYYY" → "MMDDYYYY"
            time_slug = f"{start_t.hour():02d}{start_t.minute():02d}-{end_t.hour():02d}{end_t.minute():02d}"
            arrive_slug = "_arriveBy" if arrive_by else ""
            surfaces_dir = work_dir / "surfaces" / f"{router_id}_{date_slug}_{interval_min}min_{time_slug}{arrive_slug}"
            job = SurfaceJobParams(
                from_place_lat_lon=from_place_lat_lon,
                date_mmddyyyy=date_s,
                max_walk_distance=self.parameterAsInt(parameters, self.MAX_WALK_DISTANCE, context),
                walk_reluctance=self.parameterAsDouble(parameters, self.WALK_RELUCTANCE, context),
                wait_reluctance=self.parameterAsDouble(parameters, self.WAIT_RELUCTANCE, context),
                transfer_penalty=self.parameterAsInt(parameters, self.TRANSFER_PENALTY, context),
                min_transfer_time=self.parameterAsInt(parameters, self.MIN_TRANSFER_TIME, context),
                walk_speed=self.parameterAsDouble(parameters, self.WALK_SPEED, context),
                arrive_by=arrive_by,
            )
            feedback.pushInfo(self.tr(
                f"Generating {len(time_list)} surface(s) for date={date_s}…"
            ))
            try:
                surfaces = run_surface_loop(
                    client=client,
                    time_list=time_list,
                    job=job,
                    surfaces_dir=surfaces_dir,
                    feedback=feedback,
                )
            except QgsProcessingException as e:
                err_text = str(e)
                if "VertexNotFoundException" in err_text:
                    raise QgsProcessingException(self.tr(
                        f"OTP could not snap the origin point to any vertex in the graph.\n"
                        f"Common causes:\n"
                        f"- ORIGIN_POINT is outside the OSM coverage area "
                        f"(check the router polygon bbox logged above).\n"
                        f"- OSM_PBF was empty or invalid (graph has no streets).\n"
                        f"- Coordinates entered with swapped lat/lon — check "
                        f"the 'Origin (lat, lon) sent to OTP' line above.\n"
                        f"Original error: {err_text}"
                    )) from e
                raise

            if len(surfaces) != len(time_list):
                raise QgsProcessingException(self.tr(
                    f"Surface count mismatch: expected {len(time_list)}, "
                    f"got {len(surfaces)}. Some surfaces may have failed silently. "
                    f"Check the OTP server log in {surfaces_dir.parent} for details."
                ))
            feedback.pushInfo(self.tr(
                f"Generated {len(surfaces)} surface(s) in {surfaces_dir}."
            ))

            vrt_path = work_dir / "surfaces_stack.vrt"
            try:
                build_surface_vrt(surfaces, vrt_path)
                feedback.pushInfo(self.tr(
                    f"Debug VRT written: {vrt_path} (visual inspection only)."
                ))
            except RuntimeError as e:
                feedback.pushWarning(self.tr(
                    f"VRT build failed (debug artifact only, pipeline continues): {e}"
                ))

            feedback.pushInfo(self.tr(
                f"Counting pixels with travel-time ≤ {threshold_min} min "
                f"across {len(surfaces)} surface(s) → {out_count_path}"
            ))
            try:
                count_below_threshold(surfaces, threshold_min, out_count_path, feedback)
            except RuntimeError as e:
                raise QgsProcessingException(str(e)) from e

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
                        "Check ORIGIN_POINT and TRAVEL_TIME_THRESHOLD, or supply a "
                        "HEX_GRID layer manually."
                    ))
                _extent, _extent_crs = _extent_result
                hex_grid = build_hex_grid(
                    _extent, _extent_crs, cell_size, context, feedback,
                    buffer_m=cell_size * 3,
                )
            else:
                hex_grid = self.parameterAsVectorLayer(parameters, self.HEX_GRID, context)
                if hex_grid is None:
                    raise QgsProcessingException(self.tr(
                        "HEX_GRID is required when 'Generate hex grid' is unchecked. "
                        "Supply a polygon layer or enable the 'Generate hex grid' option."
                    ))

            feedback.pushInfo(self.tr("Running zonal statistics on count raster…"))
            try:
                zonal_layer = run_zonal_stats(out_count_path, hex_grid, context, feedback)
            except RuntimeError as e:
                raise QgsProcessingException(str(e)) from e

            feedback.pushInfo(self.tr("Classifying service-time categories…"))
            try:
                classified_layer = classify_service_time(
                    zonal_layer, feedback, interval_min=interval_min,
                    n_surfaces=len(time_list),
                )
            except RuntimeError as e:
                raise QgsProcessingException(str(e)) from e

            out_fields = classified_layer.fields()
            out_fields.append(QgsField("arrive_by", QVariant.Bool))
            sink, dest_id = self.parameterAsSink(
                parameters, self.OUTPUT_HEX, context,
                out_fields,
                classified_layer.wkbType(),
                classified_layer.sourceCrs(),
            )
            for feat in classified_layer.getFeatures():
                out_feat = QgsFeature(out_fields)
                out_feat.setGeometry(feat.geometry())
                out_feat.setAttributes(feat.attributes() + [arrive_by])
                sink.addFeature(out_feat, QgsFeatureSink.FastInsert)

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
                            "analysis_date": qdt_date.date().toString("yyyy-MM-dd"),
                            "destination_lat": round(from_place_lat_lon[0], 6),
                            "destination_lon": round(from_place_lat_lon[1], 6),
                            "threshold_min": threshold_min,
                            "window_start": start_t.toString("HH:mm"),
                            "window_end": end_t.toString("HH:mm"),
                            "interval_min": interval_min,
                        },
                        report_path,
                    )
                    feedback.pushInfo(self.tr(
                        f"Statistics report saved to: {actual_path}"
                    ))

            self._output_hex_dest_id = dest_id
            feedback.pushInfo(self.tr(
                "Pipeline complete: hex grid with service-time classification ready."
            ))
            if server_ctx is not None:
                server_ctx.__exit__(None, None, None)
                server_ctx = None
            return {
                self.OUTPUT_COUNT_RASTER: str(out_count_path),
                self.OUTPUT_HEX: dest_id,
            }
        except BaseException:
            if server_ctx is not None:
                server_ctx.__exit__(*self._exc_info())
            raise

    def postProcessAlgorithm(self, context, feedback):  # noqa: N802 — Qt API name
        dest_id = getattr(self, "_output_hex_dest_id", None)
        if dest_id:
            layer = QgsProcessingUtils.mapLayerFromString(dest_id, context)
            if layer:
                qml_path = Path(__file__).parent.parent / "styles" / "service_time.qml"
                if qml_path.exists():
                    layer.loadNamedStyle(str(qml_path))
                    layer.triggerRepaint()
        return {}

    def _log_router_diagnostic(
        self, client: OtpClient, feedback
    ) -> "tuple[float, float, float, float] | None":
        """Fetch and pretty-print the router info so we can see what OTP loaded.

        Returns the router polygon bounding box as (min_lon, min_lat, max_lon, max_lat)
        in WGS84, or None if the polygon could not be parsed.  The bbox is used by
        processAlgorithm to generate the hex grid when GENERATE_GRID=True.

        Helps diagnose empty-surface bugs: if transitServiceStarts/Ends does
        not cover ANALYSIS_DATE, OTP will return an all-unreachable raster.
        """
        try:
            info = client.get_router_info()
        except OtpClientError as e:
            feedback.pushWarning(self.tr(f"Could not fetch router diagnostic: {e}"))
            return None

        from datetime import datetime, timezone

        def _epoch_to_iso(value) -> str:
            try:
                return datetime.fromtimestamp(int(value), tz=timezone.utc).date().isoformat()
            except (TypeError, ValueError, OSError):
                return str(value)

        transit_starts = info.get("transitServiceStarts")
        transit_ends = info.get("transitServiceEnds")
        has_transit = info.get("hasTransit")
        center_lat = info.get("centerLatitude")
        center_lon = info.get("centerLongitude")
        polygon = info.get("polygon")

        feedback.pushInfo(self.tr("--- OTP router diagnostic ---"))
        feedback.pushInfo(self.tr(
            f"hasTransit = {has_transit}; "
            f"transitServiceStarts = {_epoch_to_iso(transit_starts)} ({transit_starts}); "
            f"transitServiceEnds = {_epoch_to_iso(transit_ends)} ({transit_ends})"
        ))
        if center_lat is not None and center_lon is not None:
            feedback.pushInfo(self.tr(f"Router center (lat, lon) = ({center_lat}, {center_lon})"))

        router_bbox = None
        if isinstance(polygon, dict) and polygon.get("coordinates"):
            try:
                coords = polygon["coordinates"][0]
                lons = [c[0] for c in coords]
                lats = [c[1] for c in coords]
                router_bbox = (min(lons), min(lats), max(lons), max(lats))
                feedback.pushInfo(self.tr(
                    f"Router polygon bbox (lat, lon): "
                    f"({min(lats):.4f}, {min(lons):.4f}) .. "
                    f"({max(lats):.4f}, {max(lons):.4f})"
                ))
            except (KeyError, TypeError, ValueError):
                pass
        for flag in ("hasBikeSharing", "hasParkRide", "hasBikeRental"):
            if flag in info:
                feedback.pushInfo(self.tr(f"{flag} = {info[flag]}"))
        feedback.pushInfo(self.tr("-----------------------------"))
        return router_bbox

    def _warn_gtfs_date(self, gtfs_files: list, analysis_date, feedback) -> None:
        """Warn if analysis_date is outside GTFS service range or falls on a weekend.

        Uses stdlib zipfile + csv — no pip install required.
        Logs a warning (not an exception) so the pipeline still continues.
        analysis_date is a QDate.
        """
        import csv
        import io
        import zipfile as _zf

        date_str = analysis_date.toString("yyyyMMdd")  # YYYYMMDD as in GTFS
        date_int = int(date_str)
        day_of_week = analysis_date.dayOfWeek()  # 1=Mon … 7=Sun (Qt convention)

        if day_of_week >= 6:
            day_name = "Saturday" if day_of_week == 6 else "Sunday"
            feedback.pushWarning(self.tr(
                f"ANALYSIS_DATE is a {day_name} ({date_str}). Weekend transit "
                "schedules may differ significantly from weekday analyses."
            ))

        for gtfs_path in gtfs_files:
            try:
                with _zf.ZipFile(str(gtfs_path)) as z:
                    cal_name = next(
                        (n for n in z.namelist() if n.split("/")[-1] == "calendar.txt"),
                        None,
                    )
                    if cal_name is None:
                        feedback.pushWarning(self.tr(
                            f"No calendar.txt in {gtfs_path.name} — cannot validate "
                            "analysis date against GTFS service range."
                        ))
                        continue
                    with z.open(cal_name) as raw:
                        reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
                        active = 0
                        for row in reader:
                            try:
                                if int(row["start_date"]) <= date_int <= int(row["end_date"]):
                                    active += 1
                            except (KeyError, ValueError, TypeError):
                                pass
                if active == 0:
                    feedback.pushWarning(self.tr(
                        f"{gtfs_path.name}: no services active on {date_str}. "
                        "OTP may return all-unreachable surfaces for this date."
                    ))
                else:
                    feedback.pushInfo(self.tr(
                        f"{gtfs_path.name}: {active} service(s) active on {date_str}."
                    ))
            except Exception as exc:  # noqa: BLE001
                feedback.pushWarning(self.tr(
                    f"Could not read {gtfs_path.name} for date validation: {exc}"
                ))

    def _require_file(
        self, parameters, context, key: str, label: str, fix_hint: str = ""
    ) -> Path:
        raw = self.parameterAsFile(parameters, key, context)
        if not raw:
            raise QgsProcessingException(self.tr(
                f"{label} is required (parameter {key})."
                + (f" {fix_hint}" if fix_hint else "")
            ))
        path = Path(raw)
        if not path.is_file():
            raise QgsProcessingException(self.tr(
                f"{label} not found at: {path} (parameter {key})."
                + (f" {fix_hint}" if fix_hint else "")
            ))
        return path

    @staticmethod
    def _exc_info():
        import sys
        return sys.exc_info()
