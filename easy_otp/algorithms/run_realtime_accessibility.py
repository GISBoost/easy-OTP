"""Realtime algorithm: temporal-accessibility pipeline driven by live GTFS-RT.

Same surface pipeline as ``RunTemporalAccessibility`` (build graph → start server
→ one surface per minute → count raster → hex + classification), with one
difference: before the OTP server starts, a GTFS-RT ``stop-time-updater`` is
written into ``router-config.json`` so OTP polls the live feed and each surface
reflects the network state — including real delays — at that minute.

Realtime and Analysis are separate departments (CLAUDE.md): this is a standalone
algorithm, not a subclass of the Analysis pipeline. Its results are
non-reproducible and the output layer is tagged ``analysis_type = "realtime"``.
"""

from datetime import datetime
from pathlib import Path

from qgis.PyQt.QtCore import QCoreApplication, QDate, QSettings, QVariant
from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsFeatureSink,
    QgsField,
    QgsMessageLog,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterDefinition,
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

from ..core.gtfsrt_config import (
    count_rt_polls,
    suggest_feed_id,
    summarize_trip_update_log,
    validate_rt_url,
    write_router_config,
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
    wait_until_ready,
    write_meta,
)
from ..core.raster_processing import build_surface_vrt, count_below_threshold
from ..core.surface_runner import SurfaceJobParams, run_surface_loop
from ..core.time_utils import build_time_list, forward_window
from ..core.zonal import classify_service_time, log_summary_stats, run_zonal_stats
from .generate_hex_grid import build_hex_grid, extent_of_count_nonzero

LOG_TAG = "easy-OTP"


class RunRealtimeAccessibility(QgsProcessingAlgorithm):
    OSM_PBF = "OSM_PBF"
    GTFS_FILES = "GTFS_FILES"
    ORIGIN_POINT = "ORIGIN_POINT"
    HEX_GRID = "HEX_GRID"
    GENERATE_GRID = "GENERATE_GRID"
    GRID_CELL_SIZE = "GRID_CELL_SIZE"

    RT_HORIZON_MIN = "RT_HORIZON_MIN"
    INTERVAL = "INTERVAL"
    TRAVEL_TIME_THRESHOLD = "TRAVEL_TIME_THRESHOLD"

    ARRIVE_BY = "ARRIVE_BY"

    # --- Real-time data feed ---
    GTFS_RT_URL = "GTFS_RT_URL"
    GTFS_RT_FEED_ID = "GTFS_RT_FEED_ID"
    AUTO_DETECT_FEED_ID = "AUTO_DETECT_FEED_ID"
    GTFS_RT_POLLING_SEC = "GTFS_RT_POLLING_SEC"
    GTFS_RT_FUZZY_MATCHING = "GTFS_RT_FUZZY_MATCHING"

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

    def tr(self, string: str) -> str:
        return QCoreApplication.translate("Processing", string)

    def name(self) -> str:
        return "runrealtimeaccessibility"

    def displayName(self) -> str:  # noqa: N802 — Qt API name
        return self.tr("Run realtime accessibility")

    def group(self) -> str:
        return self.tr("4 · Realtime")

    def groupId(self) -> str:  # noqa: N802 — Qt API name
        return "realtime"

    def createInstance(self):  # noqa: N802 — Qt API name
        return RunRealtimeAccessibility()

    def shortHelpString(self) -> str:  # noqa: N802 — Qt API name
        return self.tr(
            "Runs the temporal-accessibility pipeline against an "
            "OpenTripPlanner 1.5.0 instance fed with live GTFS-RT TripUpdates: "
            "before the server starts, a stop-time-updater is written into "
            "router-config.json so OTP polls the real-time feed and each "
            "per-minute surface reflects the actual delays in the network at "
            "that moment.\n\n"
            "The analysis window is anchored to the system clock: it starts at "
            "the current time and runs forward over the chosen horizon (live "
            "GTFS-RT only carries predictions near the present — there is no "
            "date/start picker). For whole-day, reproducible realtime analysis "
            "use RecordGtfsRt + BuildRealizedGtfs (v0.5) instead.\n\n"
            "Requires user-provided Java 8 and otp-1.5.0-shaded.jar.\n\n"
            "Important limitations:\n"
            "- Must be run live, today, on a day the GTFS actually covers (the "
            "run fails fast otherwise).\n"
            "- Results are NOT reproducible: they depend on the live RT state "
            "at the moment of the run. The output layer is tagged "
            "analysis_type = \"realtime\".\n"
            "- The feedId must match the feed_id OTP assigns to the static GTFS "
            "(from feed_info.txt, or an OTP-generated id when that column is "
            "absent), or OTP silently ignores the RT feed.\n"
            "- The static GTFS MUST be the official agency edition covering today, "
            "from the SAME source as the live feed, downloaded close in time to it. "
            "For ZTM Poznań that is getGTFSFile "
            "(https://www.ztm.poznan.pl/pl/dla-deweloperow/getGTFSFile) paired with "
            "getGtfsRtFile. Third-party mirrors (transitfeeds, mobilitydatabase, "
            "mkuran.pl) regenerate trip_ids and will NEVER match the live feed, so "
            "OTP applies 0 updates and the result is silently static. Use "
            "tools/rt_diagnose/compare_rt_vs_static.py to confirm a pairing.\n"
            "- Cities without a TripUpdates feed (e.g. Wrocław, Warszawa) will "
            "produce a warning and fall back to static-like results."
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

        # --- Real-time data feed ---
        self.addParameter(
            QgsProcessingParameterString(
                self.GTFS_RT_URL,
                self.tr("GTFS-RT TripUpdates URL (.pb feed)"),
            )
        )
        _feed_id_param = QgsProcessingParameterString(
            self.GTFS_RT_FEED_ID,
            self.tr(
                "GTFS-RT feedId (must match the feed_id OTP assigns to the "
                "static GTFS; leave blank to try Auto-detect)"
            ),
            optional=True,
        )
        self.addParameter(_feed_id_param)
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.AUTO_DETECT_FEED_ID,
                self.tr(
                    "Auto-detect feedId from feed_info.txt (best-effort; many "
                    "feeds omit feed_id — then enter the id OTP assigns manually)"
                ),
                defaultValue=False,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.GTFS_RT_POLLING_SEC,
                self.tr("RT polling interval (s)"),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=60,
                minValue=15,
                maxValue=300,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.GTFS_RT_FUZZY_MATCHING,
                self.tr(
                    "Fuzzy trip matching (match RT by route/direction/start-time "
                    "when trip_ids differ). A fallback only: with the official "
                    "static edition that matches the live feed, exact trip_id "
                    "matching should just work and fuzzy is unnecessary. Fuzzy also "
                    "requires the live .pb to carry route_id + start_time."
                ),
                defaultValue=True,
            )
        )

        # --- Time / analysis ---
        # Realtime is a now-anchored snapshot: the analysis date and window start
        # come from the system clock at run time (live GTFS-RT only carries
        # predictions near the present), so there is no date/start/end picker —
        # only how far ahead of "now" to measure.
        self.addParameter(
            QgsProcessingParameterNumber(
                self.RT_HORIZON_MIN,
                self.tr("Measurement horizon ahead of now (min)"),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=60,
                minValue=5,
                maxValue=180,
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
                    "auto-generated default routingDefaults — note the RT "
                    "updater is always (re)written on top before the server starts)"
                ),
                behavior=QgsProcessingParameterFile.File,
                extension="json",
                optional=True,
            )
        )
        # RT default: do NOT keep the server alive, so the per-run RT
        # router-config.json is torn down with the server it was written for.
        self._add_advanced(
            QgsProcessingParameterBoolean(
                self.KEEP_SERVER_ALIVE,
                self.tr("Keep OTP server alive after run"),
                defaultValue=False,
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
        self._rt_effective = 1  # assume effective until _check_rt_applied says otherwise
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

        # --- Resolve the GTFS-RT feed parameters ---
        rt_url = (self.parameterAsString(parameters, self.GTFS_RT_URL, context) or "").strip()
        if not rt_url:
            raise QgsProcessingException(self.tr("GTFS-RT TripUpdates URL is required."))
        feed_id = (self.parameterAsString(parameters, self.GTFS_RT_FEED_ID, context) or "").strip()
        polling_sec = self.parameterAsInt(parameters, self.GTFS_RT_POLLING_SEC, context)
        feed_id = self._resolve_feed_id(parameters, context, gtfs_files, feed_id, feedback)

        # Non-fatal probe of the RT URL (e.g. Wrocław/Warszawa have no TripUpdates).
        ok, msg = validate_rt_url(rt_url)
        if ok:
            feedback.pushInfo(self.tr(f"GTFS-RT URL reachable (HTTP 200): {rt_url}"))
        else:
            feedback.pushWarning(self.tr(
                f"GTFS-RT URL probe failed: {msg}. The run will continue, but if "
                f"the feed has no TripUpdates the result will be static-like. "
                f"URL: {rt_url}"
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

        # Anchor the window at the system clock: realtime is a forward-looking
        # snapshot, not a fixed historical window. date and start come from "now".
        now = datetime.now()
        captured_at_iso = now.isoformat(timespec="seconds")
        date_s = now.strftime("%m-%d-%Y")
        qdate = QDate(now.year, now.month, now.day)
        horizon_min = self.parameterAsInt(parameters, self.RT_HORIZON_MIN, context)

        interval_min = self.parameterAsInt(parameters, self.INTERVAL, context)
        if interval_min > horizon_min:
            raise QgsProcessingException(self.tr(
                f"Sampling interval ({interval_min} min) is longer than the RT horizon ({horizon_min} min)."
            ))
        try:
            sh, sm, eh, em, truncated = forward_window(now, horizon_min)
            time_list = build_time_list(sh, sm, eh, em, interval_min)
        except ValueError as e:
            raise QgsProcessingException(self.tr(
                f"Invalid measurement window: {e}"
            )) from e
        if truncated:
            feedback.pushWarning(self.tr(
                "Measurement horizon was clamped to 23:59 — RunRealtimeAccessibility "
                "does not span calendar days."
            ))
        feedback.pushInfo(self.tr(
            f"Realtime snapshot anchored at {now.strftime('%Y-%m-%d %H:%M')}; "
            f"window {sh:02d}:{sm:02d}–{eh:02d}:{em:02d} "
            f"({len(time_list)} surface(s), horizon {horizon_min} min, "
            f"every {interval_min} min)."
        ))

        # Fail fast: a now-anchored RT run is pointless if the GTFS has no service
        # today — otherwise OTP only fails (HTTP 500) on the first surface query,
        # after a multi-minute graph build.
        self._assert_service_active(gtfs_files, qdate, feedback)

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

        # RT requires a freshly started server so OTP loads the updater at boot.
        # A server already listening on the port was NOT started with this run's
        # RT config, so reusing it would silently drop all RT data. Check the port
        # BEFORE writing the RT router-config, so a busy-port abort cannot leave a
        # stray RT updater config behind for a later *static* run to boot with (C-1).
        if port_is_listening(port):
            raise QgsProcessingException(self.tr(
                f"Port {port} is already in use. RunRealtimeAccessibility needs a "
                f"freshly started OTP server so the GTFS-RT updater is loaded — it "
                f"will not reuse a running server. Stop the OTP instance on port "
                f"{port} (e.g. rerun the previous job once with KEEP_SERVER_ALIVE="
                f"False), or pick a different OTP_PORT, then run again."
            ))

        # --- Inject the GTFS-RT updater before the server starts ---
        # write_router_config overwrites router-config.json to add the
        # stop-time-updater on top of the shared analyst routingDefaults.
        fuzzy_matching = self.parameterAsBool(parameters, self.GTFS_RT_FUZZY_MATCHING, context)
        write_router_config(router_dir, rt_url, feed_id, polling_sec, fuzzy_matching=fuzzy_matching)
        rt_config_path = router_dir / "router-config.json"
        feedback.pushInfo(self.tr(
            f"Wrote GTFS-RT router-config.json to {router_dir} "
            f"(feedId={feed_id}, frequencySec={polling_sec}, "
            f"fuzzyTripMatching={fuzzy_matching})."
        ))

        server_ctx = None
        try:
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
                rt_config_cleanup=True,
            )
            server_ctx.__enter__()

            client = OtpClient(port=port, router=router_id)
            try:
                wait_until_ready(
                    client,
                    feedback,
                    timeout_s=300.0,
                    log_path=server_ctx.log_path,
                    proc=server_ctx.proc,
                )
            except RuntimeError as e:
                raise QgsProcessingException(str(e)) from e

            router_info = self._log_router_diagnostic(
                client, feedback, expected_feed_id=feed_id
            )
            # Validate the *served graph* covers now — the GTFS-zip service check
            # can pass while a stale EXISTING_GRAPH_DIR / cached router does not.
            # Reuse the router info already fetched above (one GET, not two — C-4).
            self._assert_graph_covers_now(router_info, now, eh, em, feedback)
            feedback.pushInfo(self.tr(
                f"RT updater active — polling {rt_url} every {polling_sec}s"
            ))

            # Best-effort pre-flight: if the first RT poll matches nothing, abort
            # now instead of generating every surface for a static-in-disguise run.
            self._preflight_rt_check(
                server_ctx.log_path if server_ctx else None,
                polling_sec, fuzzy_matching, feedback,
            )

            date_slug = date_s.replace("-", "")  # "MM-DD-YYYY" → "MMDDYYYY"
            time_slug = f"{sh:02d}{sm:02d}-{eh:02d}{em:02d}"
            arrive_slug = "_arriveBy" if arrive_by else ""
            surfaces_dir = work_dir / "surfaces" / f"{router_id}_{date_slug}_{interval_min}min_{time_slug}{arrive_slug}_rt"
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

            # Did OTP actually apply any RT updates? If 0 were applied while the
            # feed was fetched, the surfaces are effectively static (see method).
            rt_applied = self._check_rt_applied(
                server_ctx.log_path if server_ctx else None, fuzzy_matching, feedback
            )
            # rt_effective is the unambiguous "did RT actually take effect" flag:
            # 1 only when at least one update applied. Any other outcome (0 applied,
            # or an unreadable log) is treated as not-effective — better to under-
            # claim RT than to let a static-in-disguise result pass as realtime.
            rt_effective = 1 if rt_applied > 0 else 0
            self._rt_effective = rt_effective

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

            # Tag the output so realtime results are distinguishable from static
            # analysis (non-reproducible — CLAUDE.md Realtime contract).
            out_fields = classified_layer.fields()
            out_fields.append(QgsField("arrive_by", QVariant.Bool))
            out_fields.append(QgsField("analysis_type", QVariant.String))
            out_fields.append(QgsField("rt_feed", QVariant.String))
            out_fields.append(QgsField("rt_captured_at", QVariant.String))
            out_fields.append(QgsField("rt_applied", QVariant.Int))
            out_fields.append(QgsField("rt_effective", QVariant.Int))
            sink, dest_id = self.parameterAsSink(
                parameters, self.OUTPUT_HEX, context,
                out_fields,
                classified_layer.wkbType(),
                classified_layer.sourceCrs(),
            )
            for feat in classified_layer.getFeatures():
                out_feat = QgsFeature(out_fields)
                out_feat.setGeometry(feat.geometry())
                out_feat.setAttributes(
                    feat.attributes()
                    + [arrive_by, "realtime", rt_url, captured_at_iso,
                       rt_applied, rt_effective]
                )
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
                            "analysis_date": qdate.toString("yyyy-MM-dd"),
                            "destination_lat": round(from_place_lat_lon[0], 6),
                            "destination_lon": round(from_place_lat_lon[1], 6),
                            "threshold_min": threshold_min,
                            "window_start": f"{sh:02d}:{sm:02d}",
                            "window_end": f"{eh:02d}:{em:02d}",
                            "interval_min": interval_min,
                        },
                        report_path,
                    )
                    feedback.pushInfo(self.tr(
                        f"Statistics report saved to: {actual_path}"
                    ))

            self._output_hex_dest_id = dest_id
            feedback.pushInfo(self.tr(
                "Realtime pipeline complete: hex grid with service-time "
                "classification ready (analysis_type = realtime)."
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
        finally:
            # C-1 safety net: guarantee the per-run RT router-config.json is removed
            # on every path OtpServer cleanup cannot reach — e.g. a start failure
            # where proc stays None makes OtpServer.__exit__ a no-op, leaving the RT
            # updater on disk for a later static run to silently boot with. Always
            # remove it (OtpServer already removes it on its own teardown paths, so
            # this is normally a no-op FileNotFoundError); a leftover RT config must
            # never poison a static analysis on the same router_id.
            try:
                rt_config_path.unlink()
            except FileNotFoundError:
                pass  # already removed by OtpServer teardown — the normal path
            except OSError as e:
                feedback.pushWarning(self.tr(
                    f"Could not remove RT router-config.json at "
                    f"{rt_config_path}: {e}. Remove it manually before running a "
                    f"static analysis on this graph, or the live RT updater will "
                    f"silently apply."
                ))

    def postProcessAlgorithm(self, context, feedback):  # noqa: N802 — Qt API name
        dest_id = getattr(self, "_output_hex_dest_id", None)
        if dest_id:
            layer = QgsProcessingUtils.mapLayerFromString(dest_id, context)
            if layer:
                # When no RT update took effect, prefix the layer name so it is
                # unmistakable in the QGIS layer tree that this "realtime" output is
                # effectively static (see _check_rt_applied / rt_effective field).
                if getattr(self, "_rt_effective", 1) == 0 and not layer.name().startswith(
                    "RT-NOT-APPLIED_"
                ):
                    layer.setName("RT-NOT-APPLIED_" + layer.name())
                qml_path = Path(__file__).parent.parent / "styles" / "service_time.qml"
                if qml_path.exists():
                    layer.loadNamedStyle(str(qml_path))
                    layer.triggerRepaint()
        return {}

    def _resolve_feed_id(
        self, parameters, context, gtfs_files: list, feed_id: str, feedback
    ) -> str:
        """Return the effective GTFS-RT feedId, with optional auto-detect.

        If AUTO_DETECT_FEED_ID is on and no feedId was typed, try reading it from
        each feed's feed_info.txt. Whatever the final value, warn if a feed's
        feed_info.txt declares a different feed_id (a likely mismatch that makes
        OTP silently ignore the RT feed).
        """
        auto = self.parameterAsBool(parameters, self.AUTO_DETECT_FEED_ID, context)
        detected: "list[str]" = []
        for gtfs in gtfs_files:
            try:
                value = suggest_feed_id(str(gtfs))
            except Exception as e:  # noqa: BLE001 — bad/auxiliary zip must not abort
                # This read only feeds auto-detect and an advisory mismatch warning,
                # so a corrupt or non-GTFS auxiliary zip in the folder must not kill
                # a run — especially when the user already supplied GTFS_RT_FEED_ID
                # (C-2). If nothing is detected and no id was given, the required-id
                # check below still raises an actionable error.
                feedback.pushWarning(self.tr(
                    f"Could not read feed_info.txt from {gtfs.name}: {e}. "
                    f"Skipping it for feedId detection."
                ))
                continue
            if value:
                detected.append(value)

        if auto and not feed_id:
            if detected:
                feed_id = detected[0]
                feedback.pushInfo(self.tr(
                    f"Auto-detected feedId '{feed_id}' from feed_info.txt."
                ))
            else:
                feedback.pushInfo(self.tr(
                    "Auto-detect found no feed_id in feed_info.txt (the column is "
                    "often absent). Enter the feedId OTP assigns manually."
                ))

        if not feed_id:
            raise QgsProcessingException(self.tr(
                "GTFS-RT feedId is required. This GTFS has no feed_id in "
                "feed_info.txt, so OTP generates one at graph load. To discover it: "
                "enter any placeholder value and run once — the log line "
                "'OTP loaded feed IDs: [...]' (and a mismatch warning) will show the "
                "real id — then rerun with that value. An unmatched feedId makes OTP "
                "silently ignore the RT feed."
            ))

        for value in detected:
            if value != feed_id:
                warn = (
                    f"GTFS-RT feedId mismatch: feed_info.txt declares '{value}' but "
                    f"'{feed_id}' was supplied. If this does not match the feedId OTP "
                    f"actually loaded, OTP will silently ignore the RT feed."
                )
                QgsMessageLog.logMessage(warn, LOG_TAG, Qgis.Warning)
                feedback.pushWarning(self.tr(warn))
        return feed_id

    def _log_router_diagnostic(
        self, client: OtpClient, feedback, expected_feed_id: str = ""
    ) -> "dict | None":
        """Fetch and pretty-print the router info so we can see what OTP loaded.

        Returns the raw router-info dict (so the caller can reuse it for the
        graph-covers-now check without a second GET — C-4), or None if it could not
        be fetched. Helps diagnose empty-surface bugs (e.g. today's date outside the
        transit service range).
        """
        try:
            info = client.get_router_info()
        except OtpClientError as e:
            feedback.pushWarning(self.tr(f"Could not fetch router diagnostic: {e}"))
            return None

        def _epoch_to_local(value) -> str:
            # Local time, with the clock — OTP service days hinge on the agency
            # timezone, and a UTC-only date hides the midnight boundary (e.g. a
            # feed starting 00:00 local shows as the previous day at 22:00 UTC).
            try:
                return datetime.fromtimestamp(int(value)).strftime("%Y-%m-%d %H:%M")
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
            f"transitServiceStarts = {_epoch_to_local(transit_starts)} (local); "
            f"transitServiceEnds = {_epoch_to_local(transit_ends)} (local)"
        ))
        if center_lat is not None and center_lon is not None:
            feedback.pushInfo(self.tr(f"Router center (lat, lon) = ({center_lat}, {center_lon})"))

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
        for flag in ("hasBikeSharing", "hasParkRide", "hasBikeRental"):
            if flag in info:
                feedback.pushInfo(self.tr(f"{flag} = {info[flag]}"))

        # The feedId in the RT updater must match one of these or OTP silently
        # ignores the GTFS-RT feed (often a generated id when feed_info.txt has
        # no feed_id column — e.g. the Poznań feed).
        try:
            feed_ids = client.get_feed_ids()
            feedback.pushInfo(self.tr(f"OTP loaded feed IDs: {feed_ids}"))
            if expected_feed_id and expected_feed_id not in feed_ids:
                warn = (
                    f"GTFS-RT feedId '{expected_feed_id}' is not among the feed IDs "
                    f"OTP actually loaded ({feed_ids}). OTP will silently ignore the "
                    f"RT feed — set GTFS_RT_FEED_ID to one of those values and rerun."
                )
                QgsMessageLog.logMessage(warn, LOG_TAG, Qgis.Warning)
                feedback.pushWarning(self.tr(warn))
        except OtpClientError as e:
            feedback.pushWarning(self.tr(f"Could not list OTP feed IDs: {e}"))

        feedback.pushInfo(self.tr("-----------------------------"))
        return info

    def _assert_graph_covers_now(self, info, now, end_h, end_m, feedback) -> None:
        """Fail fast if the served graph's transit period misses the RT window.

        The service check on the GTFS zip can pass while the *served* graph is
        stale — e.g. EXISTING_GRAPH_DIR (or a cached router_id) built from an older
        feed. OTP then only reports ``TransitTimesException`` (HTTP 500) on the
        first surface, after the whole server start. The router info (already
        fetched by ``_log_router_diagnostic`` — passed in to avoid a second GET)
        exposes the graph's real transit period, so we validate the *whole*
        measurement window (now … now+horizon) against it and give an actionable
        message up front.

        Compared on calendar service *dates* with one day of slack, not exact
        epochs: the host timezone may differ from the agency timezone, and OTP often
        pins ``transitServiceEnds`` at the last service day's 00:00 — an exact-epoch
        compare would false-block a legitimate same-day afternoon run. The slack
        absorbs that edge, so a remaining out-of-range result means a genuinely stale
        graph (off by more than a day): we fail fast there to save a doomed surface
        loop and give an actionable message, rather than letting OTP return empty
        surfaces minutes later. Only validates when the router info was available.
        """
        if not info:
            return  # cannot validate — do not block the run
        try:
            starts_e = int(info.get("transitServiceStarts"))
            ends_e = int(info.get("transitServiceEnds"))
        except (TypeError, ValueError):
            return
        try:
            svc_start = datetime.fromtimestamp(starts_e).date()
            svc_end = datetime.fromtimestamp(ends_e).date()
        except (OverflowError, OSError, ValueError):
            return
        from datetime import timedelta  # noqa: PLC0415 — local, stdlib
        slack = timedelta(days=1)
        # forward_window never spans calendar days (it clamps at midnight), so the
        # window end shares now's date; end_h/end_m only sharpen the message.
        lo, hi = svc_start - slack, svc_end + slack
        if lo <= now.date() <= hi:
            return
        svc = f"{svc_start:%Y-%m-%d} … {svc_end:%Y-%m-%d}"
        raise QgsProcessingException(self.tr(
            f"The loaded OTP graph's transit service ({svc}) does not cover the "
            f"measurement window {now:%Y-%m-%d %H:%M}–{end_h:02d}:{end_m:02d}. This "
            f"is almost always a stale graph — EXISTING_GRAPH_DIR or the cached "
            f"router was built from an older GTFS that does not cover today. Rebuild "
            f"from the current GTFS (clear EXISTING_GRAPH_DIR, or use a fresh "
            f"WORK_DIR so a new router_id is built) and rerun."
        ))

    def _check_rt_applied(self, log_path, fuzzy_matching: bool, feedback) -> int:
        """Report how many GTFS-RT updates OTP applied; warn if none.

        Reads the OTP server log and counts applied / skipped TripUpdates. Returns
        the applied total, or -1 when the log can't be read. A result of 0 applied
        with skipped > 0 means the live feed was fetched but matched nothing in the
        static graph — the surfaces are effectively static despite the realtime
        tag, almost always a static↔RT GTFS edition mismatch (trip_ids differ).
        """
        if log_path is None:
            return -1
        try:
            text = Path(log_path).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            feedback.pushWarning(self.tr(f"Could not read OTP log to verify RT: {e}"))
            return -1

        # A2 diagnostic: surface whether OTP's fuzzy matcher left any trace in the
        # log. Absence is not proof it never ran (OTP 1.5 is quiet about it), but a
        # mention helps distinguish "fuzzy engaged and still failed" from "fuzzy
        # never engaged" when chasing a future 0-applied.
        if fuzzy_matching:
            feedback.pushInfo(self.tr(
                "Fuzzy trip matching was requested; OTP log "
                + ("mentions fuzzy matching."
                   if "fuzzy" in text.lower() else
                   "shows no explicit fuzzy-matcher line (absence is not proof it "
                   "did not run).")
            ))

        applied, skipped = summarize_trip_update_log(text)
        if applied > 0:
            feedback.pushInfo(self.tr(
                f"GTFS-RT applied: {applied} trip update(s) took effect "
                f"({skipped} skipped). Surfaces reflect live conditions."
            ))
        elif skipped > 0:
            extra = (
                "Fuzzy matching is already on, so the editions are too far apart — "
                "use a static GTFS matched to the live feed."
                if fuzzy_matching else
                "Try enabling 'Fuzzy trip matching', or use a static GTFS matched "
                "to the live feed."
            )
            warn = (
                f"GTFS-RT was fetched but 0 updates were applied ({skipped} "
                f"TripUpdates skipped — trip_id not found in the static GTFS). The "
                f"static feed and the live RT feed are out of sync, so these "
                f"surfaces are effectively STATIC despite analysis_type='realtime'. "
                f"The output layer is flagged rt_effective=0 and renamed "
                f"'RT-NOT-APPLIED_…' so it cannot be mistaken for a real RT result. "
                f"{extra}"
            )
            QgsMessageLog.logMessage(warn, LOG_TAG, Qgis.Warning)
            # reportError (non-fatal) so the failure is unmistakable in the QGIS log
            # without cancelling the run — the user still gets the geometry.
            feedback.reportError(self.tr(warn), fatalError=False)
        else:
            feedback.pushInfo(self.tr(
                "No GTFS-RT trip updates were seen in the OTP log yet (feed may be "
                "empty right now, or no poll completed during sampling)."
            ))
        return applied

    def _preflight_rt_check(
        self, log_path, polling_sec: int, fuzzy_matching: bool, feedback
    ) -> None:
        """Abort early if the first completed RT poll applied nothing (best-effort).

        Waits up to one polling cycle for OTP's first GTFS-RT poll to finish, then
        reads the server log. If a poll has *completed* (an ``Applied N`` summary
        line exists) with 0 applied and trips skipped, the static and live feeds are
        out of sync — generating every surface would just yield a static result, so
        we abort with the same guidance the post-run check gives.

        Purely an optimisation: a missing log, an unreadable log, cancellation, or
        an inconclusive window all let the run proceed. The post-run
        ``_check_rt_applied`` remains the authoritative flag (this guard is never the
        sole correctness mechanism).
        """
        if log_path is None:
            return
        import time  # noqa: PLC0415 — local stdlib import, mirrors otp_server usage

        # One poll cycle plus a margin for OTP to flush the summary line.
        deadline = time.monotonic() + polling_sec + 10
        while time.monotonic() < deadline:
            if feedback.isCanceled():
                return
            try:
                text = Path(log_path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                return  # cannot read — never block a run that could succeed
            applied, skipped = summarize_trip_update_log(text)
            if applied > 0:
                feedback.pushInfo(self.tr(
                    f"Pre-flight: first GTFS-RT poll applied {applied} update(s) — "
                    f"RT is live; generating surfaces."
                ))
                return
            if skipped > 0 and count_rt_polls(text) > 0:
                # A poll finished and resolved nothing → conclusive edition mismatch;
                # fuzzy (if on) already failed for these and later polls match the
                # same feed against the same graph, so the outcome will not change.
                extra = (
                    "fuzzy matching is already on, so the editions are too far apart"
                    if fuzzy_matching else
                    "enable 'Fuzzy trip matching' or supply a matched static GTFS"
                )
                raise QgsProcessingException(self.tr(
                    f"Pre-flight aborted before generating surfaces: OTP's first "
                    f"GTFS-RT poll applied 0 of {skipped} TripUpdates (trip_id not "
                    f"found in the static GTFS). The static feed and the live RT feed "
                    f"are different editions, so every surface would be static — "
                    f"{extra}. Use the official ZTM static edition covering today "
                    f"(getGTFSFile), downloaded close in time to the .pb, or run "
                    f"tools/rt_diagnose/compare_rt_vs_static.py to confirm the pairing."
                ))
            time.sleep(2.0)
        feedback.pushInfo(self.tr(
            "Pre-flight: no completed RT poll observed yet; proceeding (the post-run "
            "check will still verify whether RT took effect)."
        ))

    def _assert_service_active(self, gtfs_files: list, analysis_date, feedback) -> None:
        """Raise if no feed has any service today; warn on weekends.

        Realtime is a now-anchored snapshot, so a date with no scheduled service
        means OTP would only fail (HTTP 500, TransitTimesException) on the first
        surface query — after a multi-minute graph build. Detect it up front with
        stdlib zipfile + csv: calendar.txt weekday flags + date range, adjusted by
        calendar_dates.txt exceptions. Feeds we cannot parse are treated as
        indeterminate and never block the run. analysis_date is a QDate.
        """
        import csv
        import io
        import zipfile as _zf

        date_str = analysis_date.toString("yyyyMMdd")  # YYYYMMDD as in GTFS
        date_int = int(date_str)
        dow = analysis_date.dayOfWeek()  # 1=Mon … 7=Sun (Qt convention)
        weekday_col = [
            "monday", "tuesday", "wednesday", "thursday",
            "friday", "saturday", "sunday",
        ][dow - 1]

        if dow >= 6:
            day_name = "Saturday" if dow == 6 else "Sunday"
            feedback.pushWarning(self.tr(
                f"Today is {day_name} ({date_str}). Weekend transit schedules may "
                "differ significantly from weekday service."
            ))

        def _fmt(d: int) -> str:
            return f"{d // 10000:04d}-{d // 100 % 100:02d}-{d % 100:02d}"

        any_validatable = False
        total_active = 0
        ranges: "list[str]" = []

        for gtfs_path in gtfs_files:
            running: "set[str]" = set()
            validatable = False
            starts: "list[int]" = []
            ends: "list[int]" = []
            try:
                with _zf.ZipFile(str(gtfs_path)) as z:
                    names = {n.split("/")[-1]: n for n in z.namelist()}
                    if "calendar.txt" in names:
                        validatable = True
                        with z.open(names["calendar.txt"]) as raw:
                            reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
                            for row in reader:
                                try:
                                    sd = int(row["start_date"])
                                    ed = int(row["end_date"])
                                    starts.append(sd)
                                    ends.append(ed)
                                    if sd <= date_int <= ed and row.get(weekday_col, "0").strip() == "1":
                                        running.add(row["service_id"])
                                except (KeyError, ValueError, TypeError):
                                    pass
                    if "calendar_dates.txt" in names:
                        validatable = True
                        with z.open(names["calendar_dates.txt"]) as raw:
                            reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
                            for row in reader:
                                try:
                                    if int(row["date"]) != date_int:
                                        continue
                                    et = int(row["exception_type"])
                                    if et == 1:
                                        running.add(row["service_id"])
                                    elif et == 2:
                                        running.discard(row["service_id"])
                                except (KeyError, ValueError, TypeError):
                                    pass
            except Exception as exc:  # noqa: BLE001
                feedback.pushWarning(self.tr(
                    f"Could not read {gtfs_path.name} for service validation: {exc}"
                ))
                continue

            if not validatable:
                feedback.pushWarning(self.tr(
                    f"No calendar.txt/calendar_dates.txt in {gtfs_path.name} — cannot "
                    "validate today's service; proceeding without the check."
                ))
                continue

            any_validatable = True
            total_active += len(running)
            if starts and ends:
                ranges.append(
                    f"{gtfs_path.name} valid {_fmt(min(starts))}…{_fmt(max(ends))}"
                )
            if running:
                feedback.pushInfo(self.tr(
                    f"{gtfs_path.name}: {len(running)} service(s) active on {date_str}."
                ))

        if any_validatable and total_active == 0:
            range_hint = "; ".join(ranges) if ranges else "no calendar date range found"
            raise QgsProcessingException(self.tr(
                f"No transit service is scheduled on {date_str} in the supplied GTFS "
                f"({range_hint}). RunRealtimeAccessibility measures live service and "
                f"must run on a day the GTFS actually covers — and only makes sense "
                f"run live, today."
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
