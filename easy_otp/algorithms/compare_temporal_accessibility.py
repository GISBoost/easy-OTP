"""Compare temporal accessibility between two GTFS scenarios (A-2)."""

from pathlib import Path

from qgis.PyQt.QtCore import QCoreApplication, QDate, QDateTime, QSettings, QTime
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFeatureSink,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterDateTime,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFile,
    QgsProcessingParameterNumber,
    QgsProcessingParameterPoint,
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
    ensure_pointsets_dir,
    ensure_router_config,
    ensure_router_dir,
    graph_build_complete,
    port_is_listening,
    probe_otp,
    wait_until_ready,
    write_meta,
)
from ..core.raster_processing import (
    build_surface_vrt,
    compute_delta_raster,
    count_below_threshold,
)
from ..core.surface_runner import SurfaceJobParams, run_surface_loop
from ..core.time_utils import build_time_list
from ..core.zonal import (
    classify_delta,
    classify_service_time,
    log_summary_stats,
    run_zonal_stats,
)
from .generate_hex_grid import build_hex_grid, extent_of_count_nonzero


class CompareTemporalAccessibility(QgsProcessingAlgorithm):
    OSM_PBF = "OSM_PBF"
    GTFS_A = "GTFS_A"
    GTFS_B = "GTFS_B"
    ORIGIN_POINT = "ORIGIN_POINT"
    HEX_GRID = "HEX_GRID"
    GENERATE_GRID = "GENERATE_GRID"
    GRID_CELL_SIZE = "GRID_CELL_SIZE"

    ANALYSIS_DATE_A = "ANALYSIS_DATE_A"
    ANALYSIS_DATE_B = "ANALYSIS_DATE_B"
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
    EXISTING_GRAPH_DIR_A = "EXISTING_GRAPH_DIR_A"
    EXISTING_GRAPH_DIR_B = "EXISTING_GRAPH_DIR_B"
    KEEP_SERVER_ALIVE = "KEEP_SERVER_ALIVE"

    WORK_DIR = "WORK_DIR"
    DELTA_POSITIVE_MIN = "DELTA_POSITIVE_MIN"
    DELTA_NEGATIVE_MAX = "DELTA_NEGATIVE_MAX"

    OUTPUT_HEX_A = "OUTPUT_HEX_A"
    OUTPUT_HEX_B = "OUTPUT_HEX_B"
    OUTPUT_HEX_DELTA = "OUTPUT_HEX_DELTA"

    def tr(self, string: str) -> str:
        return QCoreApplication.translate(type(self).__name__, string)

    def name(self) -> str:
        return "comparetemporalaccessibility"

    def displayName(self) -> str:  # noqa: N802
        return self.tr("Compare temporal accessibility")

    def group(self) -> str:
        return self.tr("3 · Analysis")

    def groupId(self) -> str:  # noqa: N802
        return "analysis"

    def createInstance(self):  # noqa: N802
        return CompareTemporalAccessibility()

    def shortHelpString(self) -> str:  # noqa: N802
        return self.tr(
            "Runs the full temporal-accessibility pipeline twice — once for "
            "GTFS scenario A and once for scenario B — using a shared OSM "
            "extract, origin point, and time window.  Each scenario has its "
            "own analysis date, enabling date-vs-date comparisons (e.g. "
            "summer vs. winter timetable) in addition to feed-vs-feed "
            "comparisons.\n\n"
            "After both pipelines complete, subtracts the two count rasters "
            "(delta = count_B − count_A) and aggregates the result onto a "
            "shared hex grid.  Outputs three hex layers:\n"
            "  • OUTPUT_HEX_A — service-time classification for scenario A\n"
            "  • OUTPUT_HEX_B — service-time classification for scenario B\n"
            "  • OUTPUT_HEX_DELTA — delta_mean (minutes) and delta_class\n"
            "    (improved / unchanged / degraded)\n\n"
            "Intermediate rasters (count_A.tif, count_B.tif, delta.tif) are "
            "saved to the working directory for inspection.\n\n"
            "Requires Java 8 and otp-1.5.0-shaded.jar.  Runs two OTP server "
            "instances sequentially on the same port."
        )

    def initAlgorithm(self, config=None):  # noqa: N802
        # --- Data inputs ---
        self.addParameter(
            QgsProcessingParameterFile(
                self.OSM_PBF,
                self.tr("OSM extract (.osm.pbf) — shared by both scenarios"),
                behavior=QgsProcessingParameterFile.File,
                extension="pbf",
            )
        )
        self.addParameter(
            QgsProcessingParameterFile(
                self.GTFS_A,
                self.tr("GTFS feed A (.zip) — scenario A (baseline)"),
                behavior=QgsProcessingParameterFile.File,
                extension="zip",
            )
        )
        self.addParameter(
            QgsProcessingParameterFile(
                self.GTFS_B,
                self.tr("GTFS feed B (.zip) — scenario B (comparison)"),
                behavior=QgsProcessingParameterFile.File,
                extension="zip",
            )
        )
        self.addParameter(
            QgsProcessingParameterPoint(
                self.ORIGIN_POINT,
                self.tr("Origin point — shared by both scenarios (OTP fromPlace)"),
            )
        )
        _hex_grid_param = QgsProcessingParameterVectorLayer(
            self.HEX_GRID,
            self.tr(
                "Hexagonal grid (polygon layer; leave blank when "
                "'Generate hex grid' is checked)"
            ),
            types=[QgsProcessing.TypeVectorPolygon],
        )
        _hex_grid_param.setFlags(
            _hex_grid_param.flags() | QgsProcessingParameterDefinition.FlagOptional
        )
        self.addParameter(_hex_grid_param)
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.GENERATE_GRID,
                self.tr("Generate hex grid from scenario A extent"),
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
                self.ANALYSIS_DATE_A,
                self.tr("Analysis date — scenario A"),
                type=QgsProcessingParameterDateTime.Date,
                defaultValue=QDateTime(QDate.currentDate(), QTime(0, 0)),
            )
        )
        self.addParameter(
            QgsProcessingParameterDateTime(
                self.ANALYSIS_DATE_B,
                self.tr("Analysis date — scenario B (leave same as A for GTFS comparison)"),
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
            QgsProcessingParameterNumber(
                self.INTERVAL,
                self.tr("Sampling interval (minutes)"),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=1,
                minValue=1,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.TRAVEL_TIME_THRESHOLD,
                self.tr("Travel-time threshold (min) — shared by both scenarios"),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=30,
                minValue=1,
                maxValue=120,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.ARRIVE_BY,
                self.tr(
                    "Arrive by (reverse routing — measure latest departure to "
                    "arrive at destination by T)"
                ),
                defaultValue=False,
            )
        )

        # --- Delta classification thresholds ---
        self.addParameter(
            QgsProcessingParameterNumber(
                self.DELTA_POSITIVE_MIN,
                self.tr(
                    "Minimum delta for 'improved' class (min) — "
                    "delta_mean ≥ this value → improved"
                ),
                type=QgsProcessingParameterNumber.Double,
                defaultValue=60.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.DELTA_NEGATIVE_MAX,
                self.tr(
                    "Maximum delta for 'degraded' class (min) — "
                    "delta_mean ≤ this value → degraded"
                ),
                type=QgsProcessingParameterNumber.Double,
                defaultValue=-60.0,
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
                    "Use Java path saved by 'Download Java Runtime Environment' "
                    "(QSettings)"
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
                self.tr("OTP server port — reused sequentially for A then B"),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=8801,
                minValue=1,
                maxValue=65535,
            )
        )
        self._add_advanced(
            QgsProcessingParameterFile(
                self.EXISTING_GRAPH_DIR_A,
                self.tr("Existing graph router directory for scenario A (skip build)"),
                behavior=QgsProcessingParameterFile.Folder,
                optional=True,
            )
        )
        self._add_advanced(
            QgsProcessingParameterFile(
                self.EXISTING_GRAPH_DIR_B,
                self.tr("Existing graph router directory for scenario B (skip build)"),
                behavior=QgsProcessingParameterFile.Folder,
                optional=True,
            )
        )
        self._add_advanced(
            QgsProcessingParameterBoolean(
                self.KEEP_SERVER_ALIVE,
                self.tr(
                    "Keep OTP server alive after run (applies to scenario B's server; "
                    "scenario A's server is always stopped to free the port)"
                ),
                defaultValue=True,
            )
        )

        # --- Working directory and outputs ---
        self.addParameter(
            QgsProcessingParameterFile(
                self.WORK_DIR,
                self.tr(
                    "Working directory (intermediate surfaces, graphs, count rasters)"
                ),
                behavior=QgsProcessingParameterFile.Folder,
            )
        )

        _hex_a = QgsProcessingParameterFeatureSink(
            self.OUTPUT_HEX_A,
            self.tr("Output hex grid — scenario A (service-time classification)"),
            type=QgsProcessing.TypeVectorPolygon,
        )
        _hex_a.setFlags(_hex_a.flags() | QgsProcessingParameterDefinition.FlagOptional)
        self.addParameter(_hex_a)

        _hex_b = QgsProcessingParameterFeatureSink(
            self.OUTPUT_HEX_B,
            self.tr("Output hex grid — scenario B (service-time classification)"),
            type=QgsProcessing.TypeVectorPolygon,
        )
        _hex_b.setFlags(_hex_b.flags() | QgsProcessingParameterDefinition.FlagOptional)
        self.addParameter(_hex_b)

        _hex_delta = QgsProcessingParameterFeatureSink(
            self.OUTPUT_HEX_DELTA,
            self.tr(
                "Output hex grid — delta (delta_mean in minutes, delta_class)"
            ),
            type=QgsProcessing.TypeVectorPolygon,
        )
        self.addParameter(_hex_delta)

    def _add_advanced(self, param) -> None:
        param.setFlags(param.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(param)

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        self._output_hex_a_dest_id = None
        self._output_hex_b_dest_id = None
        self._output_hex_delta_dest_id = None

        # --- Java and OTP jar ---
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
        gtfs_a = self._require_file(parameters, context, self.GTFS_A, "GTFS A feed")
        gtfs_b = self._require_file(parameters, context, self.GTFS_B, "GTFS B feed")

        # --- Working directory ---
        work_dir_str = self.parameterAsFile(parameters, self.WORK_DIR, context)
        if not work_dir_str:
            raise QgsProcessingException(self.tr("Working directory is required."))
        work_dir = Path(work_dir_str)
        work_dir.mkdir(parents=True, exist_ok=True)

        # --- Early hex-grid validation (fail fast, before any OTP work) ---
        generate_grid = self.parameterAsBool(parameters, self.GENERATE_GRID, context)
        if not generate_grid:
            _hex_grid_early = self.parameterAsVectorLayer(parameters, self.HEX_GRID, context)
            if _hex_grid_early is None:
                raise QgsProcessingException(self.tr(
                    "HEX_GRID is required when 'Generate hex grid' is unchecked. "
                    "Supply a polygon layer or enable the 'Generate hex grid' option."
                ))

        # --- OTP config ---
        port = self.parameterAsInt(parameters, self.OTP_PORT, context)
        xmx_build = self.parameterAsString(parameters, self.OTP_XMX_BUILD, context)
        xmx_serve = self.parameterAsString(parameters, self.OTP_XMX_SERVE, context)
        keep_alive = self.parameterAsBool(parameters, self.KEEP_SERVER_ALIVE, context)

        # --- Origin point ---
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        pt = self.parameterAsPoint(parameters, self.ORIGIN_POINT, context, wgs84)
        from_place_lat_lon = (pt.y(), pt.x())
        feedback.pushInfo(self.tr(
            f"Origin (lat, lon) sent to OTP: "
            f"({from_place_lat_lon[0]:.6f}, {from_place_lat_lon[1]:.6f})"
        ))

        # --- Analysis dates (per scenario) and shared time window ---
        qdt_date_a = self.parameterAsDateTime(parameters, self.ANALYSIS_DATE_A, context)
        qdt_date_b = self.parameterAsDateTime(parameters, self.ANALYSIS_DATE_B, context)
        date_s_a = qdt_date_a.date().toString("MM-dd-yyyy")
        date_s_b = qdt_date_b.date().toString("MM-dd-yyyy")
        feedback.pushInfo(self.tr(
            f"Analysis dates: A = {date_s_a}, B = {date_s_b}"
        ))

        raw_start = parameters.get(self.TIME_START)
        raw_end = parameters.get(self.TIME_END)
        start_t = (
            raw_start if isinstance(raw_start, QTime)
            else self.parameterAsDateTime(parameters, self.TIME_START, context).time()
        )
        end_t = (
            raw_end if isinstance(raw_end, QTime)
            else self.parameterAsDateTime(parameters, self.TIME_END, context).time()
        )
        interval_min = self.parameterAsInt(parameters, self.INTERVAL, context)
        window_min = (
            (end_t.hour() * 60 + end_t.minute())
            - (start_t.hour() * 60 + start_t.minute())
        )
        if interval_min > window_min:
            raise QgsProcessingException(self.tr(
                f"Sampling interval ({interval_min} min) is longer than the analysis window."
            ))
        try:
            time_list = build_time_list(
                start_t.hour(), start_t.minute(),
                end_t.hour(), end_t.minute(),
                interval_min,
            )
        except ValueError as e:
            raise QgsProcessingException(self.tr(f"Invalid time window: {e}")) from e
        feedback.pushInfo(self.tr(
            f"Sampling {len(time_list)} surfaces at {interval_min}-min interval "
            f"({time_list[0]}–{time_list[-1]})."
        ))

        # --- Routing params (shared routing config, per-scenario date) ---
        threshold_min = self.parameterAsInt(parameters, self.TRAVEL_TIME_THRESHOLD, context)
        arrive_by = self.parameterAsBool(parameters, self.ARRIVE_BY, context)
        _routing_kwargs = dict(
            from_place_lat_lon=from_place_lat_lon,
            max_walk_distance=self.parameterAsInt(parameters, self.MAX_WALK_DISTANCE, context),
            walk_reluctance=self.parameterAsDouble(parameters, self.WALK_RELUCTANCE, context),
            wait_reluctance=self.parameterAsDouble(parameters, self.WAIT_RELUCTANCE, context),
            transfer_penalty=self.parameterAsInt(parameters, self.TRANSFER_PENALTY, context),
            min_transfer_time=self.parameterAsInt(parameters, self.MIN_TRANSFER_TIME, context),
            walk_speed=self.parameterAsDouble(parameters, self.WALK_SPEED, context),
            arrive_by=arrive_by,
        )
        job_a = SurfaceJobParams(date_mmddyyyy=date_s_a, **_routing_kwargs)
        job_b = SurfaceJobParams(date_mmddyyyy=date_s_b, **_routing_kwargs)

        # --- Delta thresholds ---
        delta_positive_min = self.parameterAsDouble(
            parameters, self.DELTA_POSITIVE_MIN, context
        )
        delta_negative_max = self.parameterAsDouble(
            parameters, self.DELTA_NEGATIVE_MAX, context
        )

        # --- Existing graph directories ---
        existing_graph_a = self.parameterAsFile(
            parameters, self.EXISTING_GRAPH_DIR_A, context
        )
        existing_graph_b = self.parameterAsFile(
            parameters, self.EXISTING_GRAPH_DIR_B, context
        )

        # --- GTFS / date warnings (each feed checked against its own date) ---
        self._warn_gtfs_date([gtfs_a], qdt_date_a.date(), feedback)
        self._warn_gtfs_date([gtfs_b], qdt_date_b.date(), feedback)

        # --- Intermediate file paths ---
        count_a_path = work_dir / "count_A.tif"
        count_b_path = work_dir / "count_B.tif"
        delta_path = work_dir / "delta.tif"

        # ======= Pipeline A =======
        feedback.pushInfo(self.tr("=== Pipeline A: building graph and generating surfaces ==="))
        self._run_single_pipeline(
            java=java, jar=jar, pbf=pbf,
            gtfs_file=gtfs_a,
            existing_graph_dir_str=existing_graph_a,
            work_dir=work_dir,
            port=port, xmx_build=xmx_build, xmx_serve=xmx_serve,
            keep_alive=False,  # always stop A so port is free for B
            time_list=time_list, interval_min=interval_min, threshold_min=threshold_min,
            job=job_a,
            count_path=count_a_path,
            vrt_path=work_dir / "surfaces_stack_A.vrt",
            feedback=feedback,
            label="A",
        )

        if feedback.isCanceled():
            raise QgsProcessingException(self.tr("Run cancelled by user."))

        # ======= Pipeline B =======
        feedback.pushInfo(self.tr("=== Pipeline B: building graph and generating surfaces ==="))
        self._run_single_pipeline(
            java=java, jar=jar, pbf=pbf,
            gtfs_file=gtfs_b,
            existing_graph_dir_str=existing_graph_b,
            work_dir=work_dir,
            port=port, xmx_build=xmx_build, xmx_serve=xmx_serve,
            keep_alive=keep_alive,
            time_list=time_list, interval_min=interval_min, threshold_min=threshold_min,
            job=job_b,
            count_path=count_b_path,
            vrt_path=work_dir / "surfaces_stack_B.vrt",
            feedback=feedback,
            label="B",
        )

        if feedback.isCanceled():
            raise QgsProcessingException(self.tr("Run cancelled by user."))

        # ======= Delta raster =======
        feedback.pushInfo(self.tr("Computing delta raster (count_B − count_A)…"))
        try:
            compute_delta_raster(count_a_path, count_b_path, delta_path, feedback)
        except RuntimeError as e:
            raise QgsProcessingException(str(e)) from e

        # ======= Hex grid =======
        if generate_grid:
            cell_size = self.parameterAsDouble(parameters, self.GRID_CELL_SIZE, context)
            feedback.pushInfo(self.tr(
                f"Generating hex grid from scenario A count raster extent "
                f"(cell size {cell_size} m)…"
            ))
            _extent_result = extent_of_count_nonzero(count_a_path)
            if _extent_result is None:
                raise QgsProcessingException(self.tr(
                    "No pixels were accessible in scenario A within the travel-time "
                    "threshold. Check ORIGIN_POINT and TRAVEL_TIME_THRESHOLD, or supply "
                    "a HEX_GRID layer manually."
                ))
            _extent, _extent_crs = _extent_result
            hex_grid = build_hex_grid(
                _extent, _extent_crs, cell_size, context, feedback,
                buffer_m=cell_size * 3,
            )
        else:
            hex_grid = self.parameterAsVectorLayer(parameters, self.HEX_GRID, context)

        # ======= Zonal stats → classification → output sinks =======
        feedback.pushInfo(self.tr("Running zonal statistics for scenario A…"))
        try:
            zonal_a = run_zonal_stats(count_a_path, hex_grid, context, feedback)
        except RuntimeError as e:
            raise QgsProcessingException(str(e)) from e
        classified_a = classify_service_time(
            zonal_a, feedback, interval_min=interval_min, n_surfaces=len(time_list)
        )

        feedback.pushInfo(self.tr("Running zonal statistics for scenario B…"))
        try:
            zonal_b = run_zonal_stats(count_b_path, hex_grid, context, feedback)
        except RuntimeError as e:
            raise QgsProcessingException(str(e)) from e
        classified_b = classify_service_time(
            zonal_b, feedback, interval_min=interval_min, n_surfaces=len(time_list)
        )

        feedback.pushInfo(self.tr("Running zonal statistics for delta raster…"))
        try:
            zonal_delta = run_zonal_stats(delta_path, hex_grid, context, feedback)
        except RuntimeError as e:
            raise QgsProcessingException(str(e)) from e
        classified_delta = classify_delta(
            zonal_delta, feedback,
            interval_min=interval_min,
            positive_min=delta_positive_min,
            negative_max=delta_negative_max,
        )

        # Write OUTPUT_HEX_A
        sink_a, dest_id_a = self.parameterAsSink(
            parameters, self.OUTPUT_HEX_A, context,
            classified_a.fields(),
            classified_a.wkbType(),
            classified_a.sourceCrs(),
        )
        for feat in classified_a.getFeatures():
            sink_a.addFeature(feat, QgsFeatureSink.FastInsert)
        self._output_hex_a_dest_id = dest_id_a

        # Write OUTPUT_HEX_B
        sink_b, dest_id_b = self.parameterAsSink(
            parameters, self.OUTPUT_HEX_B, context,
            classified_b.fields(),
            classified_b.wkbType(),
            classified_b.sourceCrs(),
        )
        for feat in classified_b.getFeatures():
            sink_b.addFeature(feat, QgsFeatureSink.FastInsert)
        self._output_hex_b_dest_id = dest_id_b

        # Write OUTPUT_HEX_DELTA
        sink_delta, dest_id_delta = self.parameterAsSink(
            parameters, self.OUTPUT_HEX_DELTA, context,
            classified_delta.fields(),
            classified_delta.wkbType(),
            classified_delta.sourceCrs(),
        )
        for feat in classified_delta.getFeatures():
            sink_delta.addFeature(feat, QgsFeatureSink.FastInsert)
        self._output_hex_delta_dest_id = dest_id_delta

        log_summary_stats(classified_a, feedback)
        log_summary_stats(classified_b, feedback)
        self._log_delta_summary(classified_delta, feedback)

        feedback.pushInfo(self.tr(
            "Comparison pipeline complete. Three hex layers written."
        ))
        return {
            self.OUTPUT_HEX_A: dest_id_a,
            self.OUTPUT_HEX_B: dest_id_b,
            self.OUTPUT_HEX_DELTA: dest_id_delta,
        }

    def postProcessAlgorithm(self, context, feedback):  # noqa: N802
        qml_st = Path(__file__).parent.parent / "styles" / "service_time.qml"
        for dest_id_attr in ("_output_hex_a_dest_id", "_output_hex_b_dest_id"):
            dest_id = getattr(self, dest_id_attr, None)
            if dest_id:
                layer = QgsProcessingUtils.mapLayerFromString(dest_id, context)
                if layer and qml_st.exists():
                    layer.loadNamedStyle(str(qml_st))
                    layer.triggerRepaint()
        dest_id_delta = getattr(self, "_output_hex_delta_dest_id", None)
        if dest_id_delta:
            layer = QgsProcessingUtils.mapLayerFromString(dest_id_delta, context)
            qml_delta = Path(__file__).parent.parent / "styles" / "delta_class.qml"
            if layer and qml_delta.exists():
                layer.loadNamedStyle(str(qml_delta))
                layer.triggerRepaint()
        return {}

    # ------------------------------------------------------------------ helpers

    def _run_single_pipeline(
        self,
        java: Path,
        jar: Path,
        pbf: Path,
        gtfs_file: Path,
        existing_graph_dir_str: str,
        work_dir: Path,
        port: int,
        xmx_build: str,
        xmx_serve: str,
        keep_alive: bool,
        time_list: list,
        interval_min: int,
        threshold_min: int,
        job: SurfaceJobParams,
        count_path: Path,
        vrt_path: Path,
        feedback,
        label: str,
    ) -> None:
        """Run build → server → surfaces → count_raster for one GTFS scenario.

        On success the count raster is written to count_path and the OTP server
        is stopped (unless keep_alive=True and the server was started by this
        call).  On any exception the server is guaranteed to be stopped before
        re-raising.
        """
        if existing_graph_dir_str:
            existing_dir = Path(existing_graph_dir_str)
            if not (existing_dir / "Graph.obj").exists():
                raise QgsProcessingException(self.tr(
                    f"EXISTING_GRAPH_DIR_{label} does not contain Graph.obj: "
                    f"{existing_dir}. Point to the router directory "
                    f"(e.g. …/graphs/abc123/)."
                ))
            router_id = existing_dir.name
            router_dir = existing_dir
            server_work_dir = existing_dir.parent.parent
            feedback.pushInfo(self.tr(
                f"[{label}] Using existing graph: {router_dir} "
                f"(router_id={router_id}); skipping build."
            ))
            ensure_router_config(router_dir, gtfs_file.parent, feedback)
        else:
            server_work_dir = work_dir
            router_id = compute_router_id(pbf, [gtfs_file])
            feedback.pushInfo(self.tr(f"[{label}] Router ID: {router_id}"))
            router_dir = ensure_router_dir(work_dir, router_id, pbf, [gtfs_file])
            ensure_router_config(router_dir, gtfs_file.parent, feedback)
            if graph_build_complete(work_dir, router_id):
                feedback.pushInfo(self.tr(
                    f"[{label}] Graph cache hit — skipping build."
                ))
            else:
                feedback.pushInfo(self.tr(
                    f"[{label}] Building OTP graph (this can take minutes)…"
                ))
                try:
                    build_graph(java, jar, xmx_build, work_dir, router_id, feedback)
                except RuntimeError as e:
                    raise QgsProcessingException(str(e)) from e
                write_meta(router_dir, jar, [pbf, gtfs_file])

        pointsets = ensure_pointsets_dir(server_work_dir)

        existing = probe_otp(port)
        server_ctx = None
        try:
            if existing:
                ver = existing.get("serverVersion", {})
                ver_str = ver.get("version") if isinstance(ver, dict) else str(ver)
                feedback.pushInfo(self.tr(
                    f"[{label}] Reusing OTP already running on port {port} "
                    f"(version {ver_str}). Ensure its loaded router matches "
                    f"router_id={router_id}; mismatch will cause surface errors."
                ))
            else:
                if port_is_listening(port):
                    raise QgsProcessingException(self.tr(
                        f"Port {port} is held by a non-OTP process. Pick a different "
                        f"OTP_PORT or stop the conflicting service."
                    ))
                feedback.pushInfo(self.tr(
                    f"[{label}] Starting OTP server on port {port}…"
                ))
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

            self._log_router_diagnostic(client, feedback)

            surfaces_dir = (
                work_dir / "surfaces" / f"scenario_{label.lower()}_{router_id[:8]}"
            )
            feedback.pushInfo(self.tr(
                f"[{label}] Generating {len(time_list)} surface(s)…"
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
                        f"[{label}] OTP could not snap the origin point to any "
                        f"vertex in the graph.\n"
                        f"Common causes: ORIGIN_POINT is outside the OSM coverage "
                        f"area, or coordinates are swapped (lat/lon).\n"
                        f"Original error: {err_text}"
                    )) from e
                raise

            if len(surfaces) != len(time_list):
                raise QgsProcessingException(self.tr(
                    f"[{label}] Surface count mismatch: expected {len(time_list)}, "
                    f"got {len(surfaces)}."
                ))
            feedback.pushInfo(self.tr(
                f"[{label}] Generated {len(surfaces)} surface(s) in {surfaces_dir}."
            ))

            try:
                build_surface_vrt(surfaces, vrt_path)
                feedback.pushInfo(self.tr(
                    f"[{label}] Debug VRT written: {vrt_path} (visual inspection only)."
                ))
            except RuntimeError as e:
                feedback.pushWarning(self.tr(
                    f"[{label}] VRT build failed (debug artifact only, pipeline "
                    f"continues): {e}"
                ))

            feedback.pushInfo(self.tr(
                f"[{label}] Counting pixels ≤ {threshold_min} min across "
                f"{len(surfaces)} surface(s) → {count_path}"
            ))
            try:
                count_below_threshold(surfaces, threshold_min, count_path, feedback)
            except RuntimeError as e:
                raise QgsProcessingException(str(e)) from e

            feedback.pushInfo(self.tr(f"[{label}] Count raster written: {count_path}"))

            if server_ctx is not None:
                server_ctx.__exit__(None, None, None)
                server_ctx = None

        except BaseException:
            if server_ctx is not None:
                server_ctx.__exit__(*self._exc_info())
            raise

    def _log_router_diagnostic(self, client: OtpClient, feedback) -> None:
        try:
            info = client.get_router_info()
        except OtpClientError as e:
            feedback.pushWarning(self.tr(f"Could not fetch router diagnostic: {e}"))
            return

        from datetime import datetime, timezone

        def _epoch_to_iso(value) -> str:
            try:
                return datetime.fromtimestamp(int(value), tz=timezone.utc).date().isoformat()
            except (TypeError, ValueError, OSError):
                return str(value)

        transit_starts = info.get("transitServiceStarts")
        transit_ends = info.get("transitServiceEnds")
        has_transit = info.get("hasTransit")
        feedback.pushInfo(self.tr(
            f"--- OTP router diagnostic ---\n"
            f"hasTransit = {has_transit}; "
            f"transitServiceStarts = {_epoch_to_iso(transit_starts)}; "
            f"transitServiceEnds = {_epoch_to_iso(transit_ends)}"
        ))
        polygon = info.get("polygon")
        if isinstance(polygon, dict) and polygon.get("coordinates"):
            try:
                coords = polygon["coordinates"][0]
                lons = [c[0] for c in coords]
                lats = [c[1] for c in coords]
                feedback.pushInfo(self.tr(
                    f"Router polygon bbox (lat, lon): "
                    f"({min(lats):.4f}, {min(lons):.4f}) .. "
                    f"({max(lats):.4f}, {max(lons):.4f})"
                ))
            except (KeyError, TypeError, ValueError):
                pass
        feedback.pushInfo(self.tr("-----------------------------"))

    def _log_delta_summary(self, layer, feedback) -> None:
        dclass_idx = layer.fields().indexOf("delta_class")
        counts: dict[str, int] = {}
        total = 0
        for feature in layer.getFeatures():
            total += 1
            val = feature[dclass_idx]
            key = val if isinstance(val, str) else ""
            counts[key] = counts.get(key, 0) + 1
        feedback.pushInfo(self.tr("=== Delta classification summary ==="))
        for cat in ("improved", "unchanged", "degraded", ""):
            count = counts.get(cat, 0)
            pct = count / total * 100 if total > 0 else 0.0
            label = cat if cat else "no data"
            feedback.pushInfo(self.tr(f"  {label}: {count} cells ({pct:.1f}%)"))
        feedback.pushInfo(self.tr(f"  Total: {total} cells"))

    def _warn_gtfs_date(self, gtfs_files: list, analysis_date, feedback) -> None:
        import csv
        import io
        import zipfile as _zf

        date_str = analysis_date.toString("yyyyMMdd")
        date_int = int(date_str)
        day_of_week = analysis_date.dayOfWeek()
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
                f"{label} is required (parameter {key})." +
                (f" {fix_hint}" if fix_hint else "")
            ))
        path = Path(raw)
        if not path.is_file():
            raise QgsProcessingException(self.tr(
                f"{label} not found at: {path} (parameter {key})." +
                (f" {fix_hint}" if fix_hint else "")
            ))
        return path

    @staticmethod
    def _exc_info():
        import sys
        return sys.exc_info()
