"""N-5 Analysis algorithm: travel-time OD matrix (N × M).

Generalises N-4 (RunOriginDestinationTimes) from one fixed destination to a full
N×M Cartesian product between an origins layer and a destinations layer.
"""

from __future__ import annotations

import csv
import itertools
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
    QgsProcessingParameterString,
    QgsWkbTypes,
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
_METRIC_OPTIONS = ["duration", "transfers", "walktime", "waittime"]
_FORMAT_OPTIONS = ["LONG_CSV", "WIDE_CSV", "BOTH"]
_COMPLEXITY_WARN = 5_000

# Maps METRICS option names → plan_client result dict keys.
_METRIC_KEY_MAP: dict[str, str] = {
    "duration": "duration",
    "transfers": "transfers",
    "walktime": "walktime",
    "waittime": "waitingtime",  # plan_client stores this as "waitingtime"
}


def _build_long_rows(
    results: dict,
    origins: list,
    destinations: list,
    metrics: list[str],
) -> list[dict]:
    """Return LONG-format rows (one per origin–destination pair).

    Args:
        results: {(o_fid, d_fid): trip_dict} from the thread-pool loop.
        origins: [(fid, lat, lon)] in iteration order.
        destinations: [(fid, lat, lon)] in iteration order.
        metrics: names from _METRIC_OPTIONS to include as columns.
    """
    rows: list[dict] = []
    for o_fid, _, _ in origins:
        for d_fid, _, _ in destinations:
            trip = results.get((o_fid, d_fid), {"status": "MISSING"})
            row: dict = {
                "origin_id": o_fid,
                "dest_id": d_fid,
                "status": trip.get("status"),
            }
            for m in metrics:
                row[m] = trip.get(_METRIC_KEY_MAP.get(m, m))
            rows.append(row)
    return rows


def _build_wide_rows(
    results: dict,
    origins: list,
    destinations: list,
) -> list[dict]:
    """Return WIDE-format rows: origins as rows, destination FIDs as columns (duration).

    Args:
        results: {(o_fid, d_fid): trip_dict} from the thread-pool loop.
        origins: [(fid, lat, lon)] in iteration order.
        destinations: [(fid, lat, lon)] in iteration order.
    """
    dest_fids = [d_fid for d_fid, _, _ in destinations]
    rows: list[dict] = []
    for o_fid, _, _ in origins:
        row: dict = {"origin_id": o_fid}
        for d_fid in dest_fids:
            trip = results.get((o_fid, d_fid), {})
            row[str(d_fid)] = trip.get("duration")
        rows.append(row)
    return rows


class RunTravelTimeMatrix(QgsProcessingAlgorithm):
    ORIGINS = "ORIGINS"
    DESTINATIONS = "DESTINATIONS"
    MODE = "MODE"
    ANALYSIS_DATE = "ANALYSIS_DATE"
    TIME = "TIME"
    METRICS = "METRICS"
    OUTPUT_FORMAT = "OUTPUT_FORMAT"
    MAKE_OD_LINES = "MAKE_OD_LINES"

    MAX_WALK_DISTANCE = "MAX_WALK_DISTANCE"
    WALK_RELUCTANCE = "WALK_RELUCTANCE"
    WAIT_RELUCTANCE = "WAIT_RELUCTANCE"
    TRANSFER_PENALTY = "TRANSFER_PENALTY"
    MIN_TRANSFER_TIME = "MIN_TRANSFER_TIME"
    MAX_WORKERS = "MAX_WORKERS"

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

    OUTPUT_MATRIX = "OUTPUT_MATRIX"
    OUTPUT_LINES = "OUTPUT_LINES"

    def tr(self, string: str) -> str:
        return QCoreApplication.translate(type(self).__name__, string)

    def name(self) -> str:
        return "runtraveltimematrix"

    def displayName(self) -> str:  # noqa: N802
        return self.tr("Run travel time matrix")

    def group(self) -> str:
        return self.tr("3 · Analysis")

    def groupId(self) -> str:  # noqa: N802
        return "analysis"

    def createInstance(self):  # noqa: N802
        return RunTravelTimeMatrix()

    def shortHelpString(self) -> str:  # noqa: N802
        return self.tr(
            "Generates a full N×M travel-time matrix between an origins layer (N) and "
            "a destinations layer (M). For each pair (origin_i, destination_j) one OTP "
            "/plan query returns the selected trip metrics.\n\n"
            "Output: LONG CSV (one row per pair), WIDE CSV (origins as rows, "
            "destinations as columns of duration), or BOTH. BOTH writes two files with "
            "_long / _wide suffixes inserted before the file extension.\n\n"
            "An optional OD line layer draws straight origin→destination segments "
            "attributed with duration_min and status.\n\n"
            "Complexity: N×M queries. A warning is shown above {0} pairs; for large "
            "matrices consider RunServiceCoverage (surface method) instead."
        ).format(_COMPLEXITY_WARN)

    def initAlgorithm(self, config=None):  # noqa: N802
        # --- Primary inputs ---
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.ORIGINS,
                self.tr("Origins layer (N; centroids used as OTP fromPlace)"),
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.DESTINATIONS,
                self.tr("Destinations layer (M; centroids used as OTP toPlace)"),
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
            QgsProcessingParameterEnum(
                self.METRICS,
                self.tr("Metrics to include in LONG output"),
                options=_METRIC_OPTIONS,
                allowMultiple=True,
                defaultValue=[0],
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.OUTPUT_FORMAT,
                self.tr("Output format"),
                options=_FORMAT_OPTIONS,
                defaultValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.MAKE_OD_LINES,
                self.tr(
                    "Create OD line layer (straight origin→destination segments "
                    "attributed with duration_min and status)"
                ),
                defaultValue=False,
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
            QgsProcessingParameterFileDestination(
                self.OUTPUT_MATRIX,
                self.tr(
                    "Output matrix (.csv or .xlsx). For BOTH format two files are "
                    "written with _long / _wide suffixes before the extension."
                ),
                fileFilter=self.tr("CSV files (*.csv);;Excel files (*.xlsx)"),
            )
        )
        _lines_param = QgsProcessingParameterFeatureSink(
            self.OUTPUT_LINES,
            self.tr("Output OD line layer (only used when MAKE_OD_LINES is enabled)"),
            optional=True,
            createByDefault=False,
        )
        _lines_param.setFlags(
            _lines_param.flags() | QgsProcessingParameterDefinition.FlagOptional
        )
        self.addParameter(_lines_param)

        # --- Routing (advanced) ---
        self._add_advanced(
            QgsProcessingParameterNumber(
                self.MAX_WALK_DISTANCE,
                self.tr(
                    "Maximum walk distance (m) — primary 404 lever: raise to 1500–9999 "
                    "to reduce PATH_NOT_FOUND errors"
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
                    "Concurrent workers (I/O-bound; safe to set above core count). "
                    "Default 4 is safe for most setups."
                ),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=4,
                minValue=1,
                maxValue=16,
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
                    "Environment' first, or uncheck 'Use saved Java path'."
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
        otp_mode = f"{mode},WALK" if need_gtfs else mode

        gtfs_files: list = []
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
        metrics_indices = self.parameterAsEnums(parameters, self.METRICS, context)
        metrics = [_METRIC_OPTIONS[i] for i in metrics_indices] if metrics_indices else ["duration"]
        fmt_idx = self.parameterAsEnum(parameters, self.OUTPUT_FORMAT, context)
        output_fmt = _FORMAT_OPTIONS[fmt_idx]
        make_lines = self.parameterAsBool(parameters, self.MAKE_OD_LINES, context)

        max_walk_distance = self.parameterAsInt(parameters, self.MAX_WALK_DISTANCE, context)
        walk_reluctance = self.parameterAsDouble(parameters, self.WALK_RELUCTANCE, context)
        wait_reluctance = self.parameterAsDouble(parameters, self.WAIT_RELUCTANCE, context)
        transfer_penalty = self.parameterAsInt(parameters, self.TRANSFER_PENALTY, context)
        min_transfer_time = self.parameterAsInt(parameters, self.MIN_TRANSFER_TIME, context)
        max_workers = self.parameterAsInt(parameters, self.MAX_WORKERS, context)

        # --- Date / time ---
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

        # --- Sources ---
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        origins_source = self.parameterAsSource(parameters, self.ORIGINS, context)
        if origins_source is None:
            raise QgsProcessingException(self.tr("Invalid ORIGINS layer."))
        destinations_source = self.parameterAsSource(parameters, self.DESTINATIONS, context)
        if destinations_source is None:
            raise QgsProcessingException(self.tr("Invalid DESTINATIONS layer."))

        matrix_path_str = self.parameterAsFileOutput(parameters, self.OUTPUT_MATRIX, context)

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

            # --- Extract origins / destinations → EPSG:4326 ---
            feedback.pushInfo(self.tr("Extracting origin centroids..."))
            o_transform = QgsCoordinateTransform(
                origins_source.sourceCrs(), wgs84, context.transformContext()
            )
            origins: list = []
            for feat in origins_source.getFeatures():
                geom = feat.geometry()
                if geom.isNull() or geom.isEmpty():
                    feedback.pushWarning(
                        self.tr("Origin feature {0} has null/empty geometry — skipped.").format(
                            feat.id()
                        )
                    )
                    continue
                centroid = geom.centroid().asPoint()
                pt = o_transform.transform(centroid)
                origins.append((feat.id(), pt.y(), pt.x()))

            feedback.pushInfo(self.tr("Extracting destination centroids..."))
            d_transform = QgsCoordinateTransform(
                destinations_source.sourceCrs(), wgs84, context.transformContext()
            )
            destinations: list = []
            for feat in destinations_source.getFeatures():
                geom = feat.geometry()
                if geom.isNull() or geom.isEmpty():
                    feedback.pushWarning(
                        self.tr(
                            "Destination feature {0} has null/empty geometry — skipped."
                        ).format(feat.id())
                    )
                    continue
                centroid = geom.centroid().asPoint()
                pt = d_transform.transform(centroid)
                destinations.append((feat.id(), pt.y(), pt.x()))

            feedback.pushInfo(
                self.tr("{0} origins × {1} destinations.").format(
                    len(origins), len(destinations)
                )
            )

            # --- Complexity guard ---
            n_pairs = len(origins) * len(destinations)
            if n_pairs > _COMPLEXITY_WARN:
                feedback.pushWarning(
                    self.tr(
                        "Large matrix: {0} pairs (≈{1} min at ~2 req/s per worker). "
                        "Consider RunServiceCoverage for large M. Continuing."
                    ).format(n_pairs, n_pairs * 2 // 60)
                )

            # --- Concurrent /plan queries (Cartesian product) ---
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
                    "Running {0} /plan queries (mode={1}, date={2}, time={3}, workers={4})..."
                ).format(n_pairs, otp_mode, date_s, time_s, max_workers)
            )

            results: dict = {}  # (o_fid, d_fid) → trip dict

            def _query(o_fid, o_lat, o_lon, d_fid, d_lat, d_lon):
                return (o_fid, d_fid), plan_client.get_trip(
                    from_lat=o_lat, from_lon=o_lon,
                    to_lat=d_lat, to_lon=d_lon,
                    **query_kwargs,
                )

            completed_count = [0]
            executor = ThreadPoolExecutor(max_workers=max_workers)
            futures_map: dict = {}
            cancelled = False
            try:
                for (o_fid, o_lat, o_lon), (d_fid, d_lat, d_lon) in itertools.product(
                    origins, destinations
                ):
                    if feedback.isCanceled():
                        cancelled = True
                        break
                    f = executor.submit(_query, o_fid, o_lat, o_lon, d_fid, d_lat, d_lon)
                    futures_map[f] = (o_fid, d_fid)

                for future in as_completed(futures_map):
                    if feedback.isCanceled():
                        cancelled = True
                        for f in futures_map:
                            f.cancel()
                        break
                    pair_key = futures_map[future]
                    try:
                        key, trip = future.result()
                    except OtpClientError as e:
                        key = pair_key
                        trip = {
                            "status": "ERROR",
                            "duration": None, "transittime": None,
                            "walktime": None, "waitingtime": None, "transfers": None,
                        }
                        feedback.pushWarning(
                            self.tr("OTP error for pair {0}: {1}").format(pair_key, e)
                        )
                    except Exception as e:  # noqa: BLE001
                        key = pair_key
                        trip = {
                            "status": "ERROR_UNEXPECTED",
                            "duration": None, "transittime": None,
                            "walktime": None, "waitingtime": None, "transfers": None,
                        }
                        feedback.pushWarning(
                            self.tr("Unexpected error for pair {0}: {1}").format(pair_key, e)
                        )
                    results[key] = trip
                    completed_count[0] += 1
                    if n_pairs > 0:
                        feedback.setProgress(int(completed_count[0] / n_pairs * 100))
            finally:
                executor.shutdown(wait=False)

            if cancelled:
                raise QgsProcessingException(self.tr("Run cancelled by user."))

            # --- Assemble output tables ---
            long_rows = _build_long_rows(results, origins, destinations, metrics)
            wide_rows = _build_wide_rows(results, origins, destinations)
            dest_col_names = [str(d_fid) for d_fid, _, _ in destinations]
            primary_matrix_path = self._write_matrix(
                long_rows, wide_rows, metrics, dest_col_names,
                matrix_path_str, output_fmt, feedback,
            )

            # --- Optional OD line layer ---
            lines_dest_id = ""
            if make_lines:
                lines_fields = QgsFields()
                lines_fields.append(QgsField("origin_id", QVariant.LongLong))
                lines_fields.append(QgsField("dest_id", QVariant.LongLong))
                lines_fields.append(QgsField("duration_min", QVariant.Double))
                lines_fields.append(QgsField("status", QVariant.String))
                lines_sink, lines_dest_id = self.parameterAsSink(
                    parameters, self.OUTPUT_LINES, context,
                    lines_fields,
                    QgsWkbTypes.LineString,
                    wgs84,
                )
                if lines_sink is None:
                    feedback.pushWarning(self.tr(
                        "MAKE_OD_LINES is enabled but no OUTPUT_LINES destination "
                        "was provided. No line layer will be created."
                    ))
                else:
                    origins_by_fid = {fid: (lat, lon) for fid, lat, lon in origins}
                    dests_by_fid = {fid: (lat, lon) for fid, lat, lon in destinations}
                    for (o_fid, d_fid), trip in results.items():
                        o_coords = origins_by_fid.get(o_fid)
                        d_coords = dests_by_fid.get(d_fid)
                        if o_coords is None or d_coords is None:
                            continue
                        o_lat, o_lon_val = o_coords
                        d_lat, d_lon_val = d_coords
                        line_feat = QgsFeature(lines_fields)
                        line_feat.setGeometry(
                            QgsGeometry.fromPolylineXY([
                                QgsPointXY(o_lon_val, o_lat),
                                QgsPointXY(d_lon_val, d_lat),
                            ])
                        )
                        line_feat.setAttributes([
                            o_fid, d_fid,
                            trip.get("duration"),
                            trip.get("status"),
                        ])
                        lines_sink.addFeature(line_feat, QgsFeatureSink.FastInsert)
                    feedback.pushInfo(
                        self.tr("OD line layer: {0} features written.").format(len(results))
                    )

            # --- Summary ---
            self._log_summary(results, feedback)
            feedback.pushInfo(self.tr("Run complete."))

            if server_ctx is not None:
                server_ctx.__exit__(None, None, None)
                server_ctx = None

            out: dict = {self.OUTPUT_MATRIX: primary_matrix_path}
            if lines_dest_id:
                out[self.OUTPUT_LINES] = lines_dest_id
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

    def _log_summary(self, results: dict, feedback) -> None:
        total = len(results)
        if total == 0:
            return
        ok_count = sum(1 for t in results.values() if t.get("status") == "OK")
        pct_ok = round(ok_count / total * 100, 1)
        feedback.pushInfo(
            self.tr("Summary: {0}/{1} pairs OK ({2}%), {3} unreachable.").format(
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
                self.tr("  status={0}: {1} pair(s)").format(code, count)
            )

    def _write_matrix(
        self,
        long_rows: list[dict],
        wide_rows: list[dict],
        metrics: list[str],
        dest_col_names: list[str],
        matrix_path_str: str,
        output_fmt: str,
        feedback,
    ) -> str:
        """Write LONG and/or WIDE tables; return path of the primary output file.

        For BOTH: writes _long and _wide suffixed files; returns the long path.
        For LONG_CSV / WIDE_CSV: writes the given path; returns it unchanged.
        """
        from ..core.dependencies import ensure_openpyxl  # noqa: PLC0415

        path = Path(matrix_path_str)
        use_xlsx = ensure_openpyxl() and path.suffix.lower() == ".xlsx"

        long_fieldnames = ["origin_id", "dest_id", "status"] + metrics
        wide_fieldnames = ["origin_id"] + dest_col_names
        primary: str = matrix_path_str

        if output_fmt in ("LONG_CSV", "BOTH"):
            long_path = (
                path if output_fmt == "LONG_CSV"
                else path.with_name(path.stem + "_long" + path.suffix)
            )
            self._write_rows(long_rows, long_fieldnames, long_path, use_xlsx, "OD long")
            feedback.pushInfo(self.tr("LONG table saved to: {0}").format(long_path))
            primary = str(long_path)

        if output_fmt in ("WIDE_CSV", "BOTH"):
            wide_path = (
                path if output_fmt == "WIDE_CSV"
                else path.with_name(path.stem + "_wide" + path.suffix)
            )
            self._write_rows(wide_rows, wide_fieldnames, wide_path, use_xlsx, "OD wide")
            feedback.pushInfo(self.tr("WIDE table saved to: {0}").format(wide_path))
            if output_fmt == "WIDE_CSV":
                primary = str(wide_path)

        return primary

    def _write_rows(
        self,
        rows: list[dict],
        fieldnames: list[str],
        path: Path,
        use_xlsx: bool,
        sheet_name: str,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if use_xlsx:
            import openpyxl  # noqa: PLC0415
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = sheet_name
            ws.append(fieldnames)
            for row in rows:
                ws.append([row.get(c) for c in fieldnames])
            wb.save(path)
        else:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
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
