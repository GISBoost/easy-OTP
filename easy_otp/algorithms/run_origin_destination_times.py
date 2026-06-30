"""N-4 Analysis algorithm: travel times from many origins to one destination.

Port of the gisboost "travel time from many places" R workflow (otpr::otp_get_times loop):
each origin centroid queries OTP /plan to the single destination, recording full trip
statistics (duration, transit time, walk time, waiting time, transfers).

Reference: docs/gisboostgithub/pop_results2.csv — ground-truth output fixture.
"""

from __future__ import annotations

import csv
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from qgis.PyQt.QtCore import QCoreApplication, QDate, QDateTime, QSettings, QTime, QVariant
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureSink,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsPointXY,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterDateTime,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFile,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterNumber,
    QgsProcessingParameterPoint,
    QgsProcessingParameterString,
    QgsProcessingParameterVectorLayer,
)

from ..core.otp_client import OtpClient, OtpClientError
from ..core.otp_server import (
    OtpServer,
    build_graph,
    check_java_version,
    compute_router_id,
    discover_gtfs_files,
    ensure_pointsets_dir,
    ensure_router_dir,
    graph_build_complete,
    port_is_listening,
    probe_otp,
    wait_until_ready,
    write_meta,
)
from ..core.plan_client import PlanClient

_MODE_OPTIONS = ["TRANSIT", "WALK", "CAR", "BICYCLE", "BUS", "RAIL", "TRAM", "SUBWAY"]
_TRANSIT_MODES = {"TRANSIT", "BUS", "RAIL", "TRAM", "SUBWAY"}
_DIRECTION_OPTIONS = ["TO_DESTINATION", "FROM_DESTINATION"]


class RunOriginDestinationTimes(QgsProcessingAlgorithm):
    ORIGINS = "ORIGINS"
    DESTINATION = "DESTINATION"
    DIRECTION = "DIRECTION"
    MODE = "MODE"
    ANALYSIS_DATE = "ANALYSIS_DATE"
    TIME = "TIME"
    DETAIL = "DETAIL"

    MAX_WALK_DISTANCE = "MAX_WALK_DISTANCE"
    WALK_RELUCTANCE = "WALK_RELUCTANCE"
    WAIT_RELUCTANCE = "WAIT_RELUCTANCE"
    TRANSFER_PENALTY = "TRANSFER_PENALTY"
    MIN_TRANSFER_TIME = "MIN_TRANSFER_TIME"
    MAX_WORKERS = "MAX_WORKERS"
    SNAP_ORIGINS_TO_NETWORK = "SNAP_ORIGINS_TO_NETWORK"
    ROADS_LAYER = "ROADS_LAYER"
    DIAGNOSE_UNREACHABLE = "DIAGNOSE_UNREACHABLE"

    OSM_PBF = "OSM_PBF"
    GTFS_FILES = "GTFS_FILES"
    USE_SAVED_JAVA = "USE_SAVED_JAVA"
    JAVA_PATH = "JAVA_PATH"
    OTP_JAR_PATH = "OTP_JAR_PATH"
    OTP_XMX_BUILD = "OTP_XMX_BUILD"
    OTP_XMX_SERVE = "OTP_XMX_SERVE"
    OTP_PORT = "OTP_PORT"
    EXISTING_GRAPH_DIR = "EXISTING_GRAPH_DIR"
    KEEP_SERVER_ALIVE = "KEEP_SERVER_ALIVE"
    WORK_DIR = "WORK_DIR"

    OUTPUT_LAYER = "OUTPUT_LAYER"
    OUTPUT_TABLE = "OUTPUT_TABLE"

    def tr(self, string: str) -> str:
        return QCoreApplication.translate(type(self).__name__, string)

    def name(self) -> str:
        return "runorigindestinationtimes"

    def displayName(self) -> str:  # noqa: N802
        return self.tr("Run origin-destination times")

    def group(self) -> str:
        return self.tr("3 · Analysis")

    def groupId(self) -> str:  # noqa: N802
        return "analysis"

    def createInstance(self):  # noqa: N802
        return RunOriginDestinationTimes()

    def shortHelpString(self) -> str:  # noqa: N802
        return self.tr(
            "Queries OTP /plan from each origin centroid to a single destination "
            "and records full trip statistics: total duration, transit time, walk "
            "time, waiting time, and number of transfers (decimal minutes).\n\n"
            "Port of the gisboost 'travel time from many places' R workflow "
            "(otpr::otp_get_times loop). Output schema matches "
            "docs/gisboostgithub/pop_results2.csv.\n\n"
            "Primary 404 lever: raise MAX_WALK_DISTANCE (e.g. to 1500-9999) "
            "to reduce or eliminate PATH_NOT_FOUND errors, at the cost of allowing "
            "unrealistically long walks. For total-travel-time-only analysis without "
            "statistics, consider RunServiceCoverage (surface method, faster)."
        )

    def initAlgorithm(self, config=None):  # noqa: N802
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.ORIGINS,
                self.tr("Origins layer (grid or points; centroids used as OTP fromPlace)"),
            )
        )
        self.addParameter(
            QgsProcessingParameterPoint(
                self.DESTINATION,
                self.tr("Destination point (OTP toPlace)"),
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.DIRECTION,
                self.tr("Direction"),
                options=_DIRECTION_OPTIONS,
                defaultValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.MODE,
                self.tr("Transport mode"),
                options=_MODE_OPTIONS,
                defaultValue=0,
            )
        )
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
                self.TIME,
                self.tr("Departure time"),
                type=QgsProcessingParameterDateTime.Time,
                defaultValue=QTime(8, 30),
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.DETAIL,
                self.tr(
                    "Detailed output (transit/walk/waiting time + transfers); "
                    "uncheck for duration only"
                ),
                defaultValue=True,
            )
        )

        # --- Data inputs ---
        self.addParameter(
            QgsProcessingParameterFile(
                self.OSM_PBF,
                self.tr("OSM extract (.osm.pbf)"),
                behavior=QgsProcessingParameterFile.File,
                extension="pbf",
            )
        )
        _gtfs_param = QgsProcessingParameterFile(
            self.GTFS_FILES,
            self.tr("GTFS folder (required for transit modes)"),
            behavior=QgsProcessingParameterFile.Folder,
            optional=True,
        )
        self.addParameter(_gtfs_param)
        self.addParameter(
            QgsProcessingParameterFile(
                self.WORK_DIR,
                self.tr("Working directory (graph, cache)"),
                behavior=QgsProcessingParameterFile.Folder,
            )
        )

        # --- Outputs ---
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT_LAYER,
                self.tr("Output layer (origins + trip statistics)"),
                type=QgsProcessing.TypeVectorAnyGeometry,
            )
        )
        _table_param = QgsProcessingParameterFileDestination(
            self.OUTPUT_TABLE,
            self.tr("Output table (.csv or .xlsx)"),
            fileFilter=self.tr("CSV files (*.csv);;Excel files (*.xlsx)"),
            optional=True,
            createByDefault=False,
        )
        _table_param.setFlags(
            _table_param.flags() | QgsProcessingParameterDefinition.FlagOptional
        )
        self.addParameter(_table_param)

        # --- Routing (advanced) ---
        self._add_advanced(
            QgsProcessingParameterNumber(
                self.MAX_WALK_DISTANCE,
                self.tr(
                    "Maximum walk distance (m) - primary 404 lever: raise to 1500-9999 "
                    "to reduce PATH_NOT_FOUND errors; trades off realism of walk legs"
                ),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=800,
                minValue=0,
            )
        )
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
                defaultValue=600,
                minValue=0,
            )
        )
        self._add_advanced(
            QgsProcessingParameterNumber(
                self.MAX_WORKERS,
                self.tr(
                    "Concurrent workers sending parallel /plan requests to OTP "
                    "(I/O-bound, not CPU threads — safe to set above physical core count). "
                    "More workers speed up large grids but stress OTP RAM and response time. "
                    "Default 4 is safe for most setups."
                ),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=4,
                minValue=1,
                maxValue=16,
            )
        )
        self._add_advanced(
            QgsProcessingParameterBoolean(
                self.SNAP_ORIGINS_TO_NETWORK,
                self.tr(
                    "Snap origin centroids to nearest road vertex before querying "
                    "(mitigates snap-related 404 errors; requires a roads layer below)"
                ),
                defaultValue=False,
            )
        )
        _roads_param = QgsProcessingParameterVectorLayer(
            self.ROADS_LAYER,
            self.tr("Roads layer for snapping (e.g. OSM lines; required when snap is enabled)"),
            types=[QgsProcessing.TypeVectorLine],
            optional=True,
        )
        _roads_param.setFlags(
            _roads_param.flags() | QgsProcessingParameterDefinition.FlagAdvanced
        )
        self.addParameter(_roads_param)
        self._add_advanced(
            QgsProcessingParameterBoolean(
                self.DIAGNOSE_UNREACHABLE,
                self.tr(
                    "Diagnose unreachable cells (walk-fallback for 404 in transit mode): "
                    "adds 'diag' field with off_network / no_transit; doubles requests for 404 cells"  # noqa: E501
                ),
                defaultValue=False,
            )
        )

        # --- OTP server (advanced) ---
        self._add_advanced(
            QgsProcessingParameterBoolean(
                self.USE_SAVED_JAVA,
                self.tr("Use Java path saved by 'Download Java Runtime Environment' (QSettings)"),
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
                self.tr("OTP heap for server (e.g. 4G)"),
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

    # ------------------------------------------------------------------
    # Processing
    # ------------------------------------------------------------------

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        # --- Java / OTP setup ---
        use_saved = self.parameterAsBool(parameters, self.USE_SAVED_JAVA, context)
        if use_saved:
            saved = QSettings().value("easy_otp/java_path", "")
            if not saved:
                raise QgsProcessingException(self.tr(
                    "No Java path saved in QSettings. Run 'Download Java Runtime "
                    "Environment' first, or uncheck 'Use saved Java path' and supply "
                    "the path manually."
                ))
            java = Path(saved)
        else:
            java = self._require_file(parameters, context, self.JAVA_PATH, "Java 8 binary")
        is_java8, java_ver, java_err = check_java_version(java)
        if not is_java8:
            raise QgsProcessingException(self.tr(java_err))
        feedback.pushInfo(self.tr("Java OK: version {0}").format(java_ver))

        jar = self._require_file(
            parameters, context, self.OTP_JAR_PATH, "OTP 1.5.0 jar",
            fix_hint=self.tr(
                "Download otp-1.5.0-shaded.jar from Maven Central and set the OTP jar parameter."
            ),
        )
        pbf = self._require_file(parameters, context, self.OSM_PBF, "OSM .pbf extract")

        work_dir_str = self.parameterAsFile(parameters, self.WORK_DIR, context)
        if not work_dir_str:
            raise QgsProcessingException(self.tr("Working directory is required."))
        work_dir = Path(work_dir_str)
        work_dir.mkdir(parents=True, exist_ok=True)

        mode_idx = self.parameterAsEnum(parameters, self.MODE, context)
        mode = _MODE_OPTIONS[mode_idx]
        need_gtfs = mode in _TRANSIT_MODES
        # OTP 1.5: pure mode=TRANSIT blocks all walking, including to/from stops.
        # Must append WALK so OTP can reach transit stops on foot.
        otp_mode = f"{mode},WALK" if need_gtfs else mode

        gtfs_files = []
        if need_gtfs:
            gtfs_dir_str = self.parameterAsFile(parameters, self.GTFS_FILES, context)
            if not gtfs_dir_str:
                raise QgsProcessingException(self.tr(
                    "GTFS folder is required for transit modes."
                ))
            try:
                gtfs_files = discover_gtfs_files(Path(gtfs_dir_str))
            except FileNotFoundError as e:
                raise QgsProcessingException(str(e)) from e
            feedback.pushInfo(
                self.tr("Discovered {0} GTFS feed(s): {1}").format(
                    len(gtfs_files),
                    ", ".join(p.name for p in gtfs_files),
                )
            )

        port = self.parameterAsInt(parameters, self.OTP_PORT, context)
        xmx_build = self.parameterAsString(parameters, self.OTP_XMX_BUILD, context) or "2G"
        xmx_serve = self.parameterAsString(parameters, self.OTP_XMX_SERVE, context) or "4G"
        keep_alive = self.parameterAsBool(parameters, self.KEEP_SERVER_ALIVE, context)

        # --- Analysis parameters ---
        direction_idx = self.parameterAsEnum(parameters, self.DIRECTION, context)
        direction = _DIRECTION_OPTIONS[direction_idx]
        detail = self.parameterAsBool(parameters, self.DETAIL, context)
        max_walk_distance = self.parameterAsInt(parameters, self.MAX_WALK_DISTANCE, context)
        walk_reluctance = self.parameterAsDouble(parameters, self.WALK_RELUCTANCE, context)
        wait_reluctance = self.parameterAsDouble(parameters, self.WAIT_RELUCTANCE, context)
        transfer_penalty = self.parameterAsInt(parameters, self.TRANSFER_PENALTY, context)
        min_transfer_time = self.parameterAsInt(parameters, self.MIN_TRANSFER_TIME, context)
        max_workers = self.parameterAsInt(parameters, self.MAX_WORKERS, context)
        snap = self.parameterAsBool(parameters, self.SNAP_ORIGINS_TO_NETWORK, context)
        diagnose = self.parameterAsBool(parameters, self.DIAGNOSE_UNREACHABLE, context)

        if snap:
            roads_layer = self.parameterAsVectorLayer(parameters, self.ROADS_LAYER, context)
            if roads_layer is None:
                raise QgsProcessingException(self.tr(
                    "SNAP_ORIGINS_TO_NETWORK is enabled but no ROADS_LAYER was supplied. "
                    "Please provide an OSM lines layer (or similar road network) to snap to."
                ))
        else:
            roads_layer = None

        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        dest_pt = self.parameterAsPoint(parameters, self.DESTINATION, context, wgs84)
        dest_lat, dest_lon = dest_pt.y(), dest_pt.x()
        feedback.pushInfo(
            self.tr("Destination (lat, lon): ({0:.6f}, {1:.6f})").format(dest_lat, dest_lon)
        )

        qdt_date = self.parameterAsDateTime(parameters, self.ANALYSIS_DATE, context)
        date_s = qdt_date.date().toString("MM-dd-yyyy")

        raw_time = parameters.get(self.TIME)
        time_t = (
            raw_time if isinstance(raw_time, QTime)
            else self.parameterAsDateTime(parameters, self.TIME, context).time()
        )
        time_s = time_t.toString("HH:mm:ss")

        if need_gtfs:
            self._warn_gtfs_date(gtfs_files, qdt_date.date(), feedback)

        # --- Origins layer ---
        origins_source = self.parameterAsSource(parameters, self.ORIGINS, context)
        if origins_source is None:
            raise QgsProcessingException(self.tr("Invalid ORIGINS layer."))

        # --- Build output fields ---
        origin_fields = origins_source.fields()
        out_fields = QgsFields(origin_fields)
        out_fields.append(QgsField("lat", QVariant.Double))
        out_fields.append(QgsField("lon", QVariant.Double))
        out_fields.append(QgsField("direction", QVariant.String))
        out_fields.append(QgsField("mode", QVariant.String))
        out_fields.append(QgsField("status", QVariant.String))
        out_fields.append(QgsField("duration", QVariant.Double))
        if detail:
            out_fields.append(QgsField("transittime", QVariant.Double))
            out_fields.append(QgsField("walktime", QVariant.Double))
            out_fields.append(QgsField("waitingtime", QVariant.Double))
            out_fields.append(QgsField("transfers", QVariant.Int))
        if diagnose:
            out_fields.append(QgsField("diag", QVariant.String))

        sink, layer_dest_id = self.parameterAsSink(
            parameters, self.OUTPUT_LAYER, context,
            out_fields,
            origins_source.wkbType(),
            origins_source.sourceCrs(),
        )

        # Resolve OUTPUT_TABLE path early so it can be returned in results dict.
        table_path = self.parameterAsFileOutput(parameters, self.OUTPUT_TABLE, context)

        # --- Graph / server lifecycle ---
        existing_graph_dir_str = self.parameterAsFile(
            parameters, self.EXISTING_GRAPH_DIR, context
        )
        if existing_graph_dir_str:
            existing_dir = Path(existing_graph_dir_str)
            if not (existing_dir / "Graph.obj").exists():
                raise QgsProcessingException(
                    self.tr("EXISTING_GRAPH_DIR does not contain Graph.obj: {0}").format(
                        existing_dir
                    )
                )
            router_id = existing_dir.name
            router_dir = existing_dir
            server_work_dir = existing_dir.parent.parent
            feedback.pushInfo(
                self.tr("Using existing graph: {0} (router_id={1}); skipping build.").format(
                    router_dir, router_id
                )
            )
        else:
            server_work_dir = work_dir
            router_id = compute_router_id(pbf, gtfs_files)
            feedback.pushInfo(self.tr("Router ID: {0}").format(router_id))
            router_dir = ensure_router_dir(work_dir, router_id, pbf, gtfs_files)
            if graph_build_complete(work_dir, router_id):
                feedback.pushInfo(self.tr("Graph cache hit - skipping build."))
            else:
                feedback.pushInfo(self.tr("Building OTP graph (this can take minutes)..."))
                try:
                    build_graph(java, jar, xmx_build, work_dir, router_id, feedback)
                except RuntimeError as e:
                    raise QgsProcessingException(str(e)) from e
                write_meta(router_dir, jar, [pbf, *gtfs_files])

        pointsets = ensure_pointsets_dir(server_work_dir)

        server_ctx = None
        try:
            existing = probe_otp(port)
            if existing:
                ver = existing.get("serverVersion", {})
                ver_str = ver.get("version") if isinstance(ver, dict) else str(ver)
                feedback.pushInfo(
                    self.tr("Reusing OTP already running on port {0} (version {1}).").format(
                        port, ver_str
                    )
                )
            else:
                if port_is_listening(port):
                    raise QgsProcessingException(
                        self.tr(
                            "Port {0} is held by a non-OTP process. Pick a different "
                            "OTP_PORT or stop the conflicting service."
                        ).format(port)
                    )
                feedback.pushInfo(self.tr("Starting OTP server on port {0}...").format(port))
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

            client_probe = OtpClient(port=port, router=router_id)
            try:
                wait_until_ready(
                    client_probe,
                    feedback,
                    timeout_s=300.0,
                    log_path=server_ctx.log_path if server_ctx else None,
                    proc=server_ctx.proc if server_ctx else None,
                )
            except RuntimeError as e:
                raise QgsProcessingException(str(e)) from e

            # --- Extract centroids → EPSG:4326 ---
            feedback.pushInfo(self.tr("Extracting centroids from origins layer..."))
            transform = QgsCoordinateTransform(
                origins_source.sourceCrs(), wgs84, context.transformContext()
            )
            origins = []  # list of (feature, lat, lon)
            for feat in origins_source.getFeatures():
                centroid = feat.geometry().centroid().asPoint()
                pt_wgs84 = transform.transform(centroid)
                origins.append((feat, pt_wgs84.y(), pt_wgs84.x()))

            feedback.pushInfo(self.tr("{0} origins loaded.").format(len(origins)))

            # --- Optional snap to network ---
            if snap and roads_layer is not None:
                feedback.pushInfo(self.tr("Snapping centroids to road network..."))
                origins = self._snap_to_network(
                    origins, roads_layer, origins_source.sourceCrs(),
                    wgs84, context, feedback,
                )

            # --- Concurrent /plan queries ---
            plan_client = PlanClient(
                hostname="localhost",
                port=port,
                router=router_id,
                timeout_s=30.0,
            )
            query_kwargs = dict(
                mode=otp_mode,
                date_mmddyyyy=date_s,
                time_hhmmss=time_s,
                max_walk_distance=float(max_walk_distance),
                walk_reluctance=walk_reluctance,
                wait_reluctance=wait_reluctance,
                transfer_penalty=transfer_penalty,
                min_transfer_time=min_transfer_time,
            )

            feedback.pushInfo(
                self.tr(
                    "Running {0} /plan queries (mode={1}, date={2}, time={3}, "
                    "maxWalkDistance={4}, workers={5})..."
                ).format(len(origins), otp_mode, date_s, time_s, max_walk_distance, max_workers)
            )

            results = {}  # fid → trip dict

            def _query(feat_id, from_lat, from_lon):
                if direction == "TO_DESTINATION":
                    f_lat, f_lon = from_lat, from_lon
                    t_lat, t_lon = dest_lat, dest_lon
                else:  # FROM_DESTINATION
                    f_lat, f_lon = dest_lat, dest_lon
                    t_lat, t_lon = from_lat, from_lon
                return feat_id, plan_client.get_trip(
                    from_lat=f_lat, from_lon=f_lon,
                    to_lat=t_lat, to_lon=t_lon,
                    **query_kwargs,
                )

            completed_count = [0]  # mutable for closure; only written from main thread

            executor = ThreadPoolExecutor(max_workers=max_workers)
            futures_map = {}
            cancelled = False
            try:
                for feat, lat, lon in origins:
                    if feedback.isCanceled():
                        cancelled = True
                        break
                    f = executor.submit(_query, feat.id(), lat, lon)
                    futures_map[f] = feat

                for future in as_completed(futures_map):
                    if feedback.isCanceled():
                        cancelled = True
                        for f in futures_map:
                            f.cancel()
                        break
                    feat = futures_map[future]
                    try:
                        fid, trip = future.result()
                    except OtpClientError as e:
                        trip = {
                            "status": "ERROR",
                            "duration": None, "transittime": None,
                            "walktime": None, "waitingtime": None, "transfers": None,
                        }
                        feedback.pushWarning(
                            self.tr("OTP error for feature {0}: {1}").format(feat.id(), e)
                        )
                    results[feat.id()] = trip
                    completed_count[0] += 1
                    feedback.setProgress(int(completed_count[0] / len(origins) * 100))
            finally:
                executor.shutdown(wait=False)

            if cancelled:
                raise QgsProcessingException(self.tr("Run cancelled by user."))

            # --- Optional walk-fallback diagnosis ---
            if diagnose and need_gtfs:
                walk_kwargs = {**query_kwargs, "mode": "WALK"}
                feedback.pushInfo(self.tr("Diagnosing unreachable cells (walk fallback)..."))
                for feat, lat, lon in origins:
                    fid = feat.id()
                    if results.get(fid, {}).get("status") == "404":
                        if direction == "TO_DESTINATION":
                            f_lat, f_lon, t_lat, t_lon = lat, lon, dest_lat, dest_lon
                        else:
                            f_lat, f_lon, t_lat, t_lon = dest_lat, dest_lon, lat, lon
                        try:
                            walk_trip = plan_client.get_trip(
                                from_lat=f_lat, from_lon=f_lon,
                                to_lat=t_lat, to_lon=t_lon,
                                **walk_kwargs,
                            )
                            diag = "no_transit" if walk_trip["status"] == "OK" else "off_network"
                        except OtpClientError:
                            diag = "off_network"
                        results[fid]["diag"] = diag

            # --- Build output layer ---
            for feat, lat, lon in origins:
                fid = feat.id()
                trip = results.get(fid, {
                    "status": "MISSING", "duration": None,
                    "transittime": None, "walktime": None,
                    "waitingtime": None, "transfers": None,
                })
                out_feat = QgsFeature(out_fields)
                out_feat.setGeometry(feat.geometry())
                attrs = list(feat.attributes())
                attrs.append(round(lat, 6))
                attrs.append(round(lon, 6))
                attrs.append(direction)
                attrs.append(mode)
                attrs.append(trip.get("status"))
                attrs.append(trip.get("duration"))
                if detail:
                    attrs.append(trip.get("transittime"))
                    attrs.append(trip.get("walktime"))
                    attrs.append(trip.get("waitingtime"))
                    attrs.append(trip.get("transfers"))
                if diagnose:
                    attrs.append(trip.get("diag"))
                out_feat.setAttributes(attrs)
                sink.addFeature(out_feat, QgsFeatureSink.FastInsert)

            # --- Summary log ---
            self._log_summary(results, feedback)

            # --- Optional table export ---
            if table_path:
                self._write_table(
                    origins, origin_fields, results, table_path, detail, diagnose,
                    direction, mode,
                )
                feedback.pushInfo(self.tr("Table saved to: {0}").format(table_path))

            feedback.pushInfo(self.tr("Run complete."))

            if server_ctx is not None:
                server_ctx.__exit__(None, None, None)
                server_ctx = None

            out = {self.OUTPUT_LAYER: layer_dest_id}
            if table_path:
                out[self.OUTPUT_TABLE] = table_path
            return out

        except BaseException:
            if server_ctx is not None:
                server_ctx.__exit__(*sys.exc_info())
            raise

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _add_advanced(self, param) -> None:
        param.setFlags(param.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(param)

    def _require_file(self, parameters, context, key: str, label: str, fix_hint: str = "") -> Path:
        raw = self.parameterAsFile(parameters, key, context)
        if not raw:
            msg = self.tr("{0} is required (parameter {1}).").format(label, key)
            if fix_hint:
                msg += " " + fix_hint
            raise QgsProcessingException(msg)
        path = Path(raw)
        if not path.is_file():
            msg = self.tr("{0} not found at: {1} (parameter {2}).").format(label, path, key)
            if fix_hint:
                msg += " " + fix_hint
            raise QgsProcessingException(msg)
        return path

    def _snap_to_network(self, origins, roads_layer, origins_crs, wgs84, context, feedback):
        """Snap centroid points to nearest vertex/edge of roads_layer.

        Builds a temporary in-memory point layer from centroids in the ORIGINS CRS,
        runs native:snapgeometries with a 500-unit tolerance (meters for projected CRS),
        then reads back snapped coordinates in WGS84.
        """
        import processing
        from qgis.core import QgsVectorLayer  # noqa: PLC0415

        if origins_crs.isGeographic():
            feedback.pushWarning(
                self.tr(
                    "SNAP_ORIGINS_TO_NETWORK: origins layer CRS is geographic ({0}). "
                    "Snap tolerance of 500 units means 500 degrees — consider using a "
                    "projected CRS for the origins layer."
                ).format(origins_crs.authid())
            )

        crs_str = origins_crs.authid()
        mem_layer = QgsVectorLayer("Point?crs={0}".format(crs_str), "centroids_snap", "memory")
        provider = mem_layer.dataProvider()
        provider.addAttributes([QgsField("_fid", QVariant.LongLong)])
        mem_layer.updateFields()

        transform_to_origin = QgsCoordinateTransform(wgs84, origins_crs, context.transformContext())
        transform_to_wgs84 = QgsCoordinateTransform(origins_crs, wgs84, context.transformContext())

        for feat, lat, lon in origins:
            pt_origin = transform_to_origin.transform(QgsPointXY(lon, lat))
            snap_feat = QgsFeature()
            snap_feat.setGeometry(QgsGeometry.fromPointXY(pt_origin))
            snap_feat.setAttributes([feat.id()])
            provider.addFeature(snap_feat)

        result = processing.run(
            "native:snapgeometries",
            {
                "INPUT": mem_layer,
                "REFERENCE_LAYER": roads_layer,
                "TOLERANCE": 500,
                "BEHAVIOR": 1,
            },
            context=context,
            feedback=feedback,
        )
        snapped_layer = result["OUTPUT"]

        snapped_coords = {}
        for sfeat in snapped_layer.getFeatures():
            orig_fid = sfeat["_fid"]
            pt_wgs84 = transform_to_wgs84.transform(sfeat.geometry().asPoint())
            snapped_coords[orig_fid] = (pt_wgs84.y(), pt_wgs84.x())

        new_origins = []
        for feat, lat, lon in origins:
            if feat.id() in snapped_coords:
                new_lat, new_lon = snapped_coords[feat.id()]
                new_origins.append((feat, new_lat, new_lon))
            else:
                new_origins.append((feat, lat, lon))

        feedback.pushInfo(
            self.tr("Snapped {0} of {1} centroids.").format(len(snapped_coords), len(origins))
        )
        return new_origins

    def _log_summary(self, results: dict, feedback) -> None:
        total = len(results)
        if total == 0:
            return
        ok_count = sum(1 for t in results.values() if t.get("status") == "OK")
        pct_ok = round(ok_count / total * 100, 1)
        feedback.pushInfo(
            self.tr("Summary: {0}/{1} OK ({2}%), {3} unreachable.").format(
                ok_count, total, pct_ok, total - ok_count
            )
        )
        error_counts: dict[str, int] = {}
        for t in results.values():
            s = t.get("status", "")
            if s != "OK":
                error_counts[s] = error_counts.get(s, 0) + 1
        for code, count in sorted(error_counts.items()):
            feedback.pushInfo(
                self.tr("  status={0}: {1} cell(s)").format(code, count)
            )

    def _write_table(
        self, origins, origin_fields, results, path_str, detail, diagnose,
        direction: str, mode: str,
    ) -> None:
        from ..core.dependencies import ensure_openpyxl  # noqa: PLC0415

        path = Path(path_str)
        field_names = [f.name() for f in origin_fields]
        meta_cols = ["lat", "lon", "direction", "mode"]
        stat_cols = ["status", "duration"]
        if detail:
            stat_cols += ["transittime", "walktime", "waitingtime", "transfers"]
        if diagnose:
            stat_cols.append("diag")
        all_cols = field_names + meta_cols + stat_cols

        rows = []
        for feat, lat, lon in origins:
            fid = feat.id()
            trip = results.get(fid, {})
            row = {name: val for name, val in zip(field_names, feat.attributes())}
            row["lat"] = round(lat, 6)
            row["lon"] = round(lon, 6)
            row["direction"] = direction
            row["mode"] = mode
            for col in stat_cols:
                row[col] = trip.get(col)
            rows.append(row)

        use_xlsx = ensure_openpyxl() and path.suffix.lower() == ".xlsx"
        if not use_xlsx and path.suffix.lower() == ".xlsx":
            path = path.with_suffix(".csv")
        path.parent.mkdir(parents=True, exist_ok=True)

        if use_xlsx:
            import openpyxl  # noqa: PLC0415
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "OD times"
            ws.append(all_cols)
            for row in rows:
                ws.append([row.get(c) for c in all_cols])
            wb.save(path)
        else:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=all_cols)
                writer.writeheader()
                writer.writerows(rows)

    def _warn_gtfs_date(self, gtfs_files: list, analysis_date, feedback) -> None:
        """Warn if analysis_date is outside GTFS service range or is a weekend."""
        import csv as _csv
        import io
        import zipfile as _zf

        date_str = analysis_date.toString("yyyyMMdd")
        date_int = int(date_str)
        day_of_week = analysis_date.dayOfWeek()

        if day_of_week >= 6:
            day_name = "Saturday" if day_of_week == 6 else "Sunday"
            feedback.pushWarning(
                self.tr(
                    "ANALYSIS_DATE is a {0} ({1}). Weekend transit schedules may differ "
                    "significantly from weekday analyses."
                ).format(day_name, date_str)
            )

        for gtfs_path in gtfs_files:
            try:
                with _zf.ZipFile(str(gtfs_path)) as z:
                    cal_name = next(
                        (n for n in z.namelist() if n.split("/")[-1] == "calendar.txt"),
                        None,
                    )
                    if cal_name is None:
                        feedback.pushWarning(
                            self.tr(
                                "No calendar.txt in {0} - cannot validate analysis date."
                            ).format(gtfs_path.name)
                        )
                        continue
                    with z.open(cal_name) as raw:
                        reader = _csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
                        active = 0
                        for row in reader:
                            try:
                                if int(row["start_date"]) <= date_int <= int(row["end_date"]):
                                    active += 1
                            except (KeyError, ValueError, TypeError):
                                pass
                if active == 0:
                    feedback.pushWarning(
                        self.tr(
                            "{0}: no services active on {1}. OTP may return all-unreachable "
                            "results."
                        ).format(gtfs_path.name, date_str)
                    )
                else:
                    feedback.pushInfo(
                        self.tr("{0}: {1} service(s) active on {2}.").format(
                            gtfs_path.name, active, date_str
                        )
                    )
            except Exception as exc:  # noqa: BLE001
                feedback.pushWarning(
                    self.tr("Could not read {0} for date validation: {1}").format(
                        gtfs_path.name, exc
                    )
                )
