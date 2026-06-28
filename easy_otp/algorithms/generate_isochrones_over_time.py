"""GenerateIsochronesOverTime: isochrone polygon evolution for one origin across the day.

Automates the time-axis slice of the OTP isochrone workflow: for a single origin
point iterates over a day-window timestamp list and requests one isochrone per
timestamp. Output is attributed with a QDateTime field for animation in the QGIS
Temporal Controller.

Companion to GenerateIsochrones (N-1: many points, one time).
"""

from __future__ import annotations

import csv
import io
import json
import zipfile as _zf
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
    QgsJsonUtils,
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
    QgsProcessingParameterString,
    QgsPointXY,
    QgsWkbTypes,
)

from ..core.isochrone_client import IsochroneClient, TRANSIT_MODES
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
from ..core.time_utils import build_time_list
from .generate_isochrones import _geom_to_multipolygon

_MODE_OPTIONS = ["TRANSIT", "BUS", "RAIL", "TRAM", "SUBWAY", "WALK", "CAR", "BICYCLE"]
_DIRECTION_OPTIONS = ["FROM", "TO"]


class GenerateIsochronesOverTime(QgsProcessingAlgorithm):
    ORIGIN_POINT = "ORIGIN_POINT"
    OSM_PBF = "OSM_PBF"
    GTFS_FILES = "GTFS_FILES"
    MODE = "MODE"
    DIRECTION = "DIRECTION"
    CUTOFFS_MIN = "CUTOFFS_MIN"
    TIME_START = "TIME_START"
    TIME_END = "TIME_END"
    INTERVAL = "INTERVAL"
    ANALYSIS_DATE = "ANALYSIS_DATE"
    MAX_WALK_DISTANCE = "MAX_WALK_DISTANCE"
    WALK_RELUCTANCE = "WALK_RELUCTANCE"
    WAIT_RELUCTANCE = "WAIT_RELUCTANCE"
    TRANSFER_PENALTY = "TRANSFER_PENALTY"
    MIN_TRANSFER_TIME = "MIN_TRANSFER_TIME"
    USE_SAVED_JAVA = "USE_SAVED_JAVA"
    JAVA_PATH = "JAVA_PATH"
    OTP_JAR_PATH = "OTP_JAR_PATH"
    OTP_XMX_BUILD = "OTP_XMX_BUILD"
    OTP_XMX_SERVE = "OTP_XMX_SERVE"
    OTP_PORT = "OTP_PORT"
    WORK_DIR = "WORK_DIR"
    EXISTING_GRAPH_DIR = "EXISTING_GRAPH_DIR"
    KEEP_SERVER_ALIVE = "KEEP_SERVER_ALIVE"
    OUTPUT_ISOCHRONES = "OUTPUT_ISOCHRONES"
    OUTPUT_AREA_CSV = "OUTPUT_AREA_CSV"
    OUTPUT_ORIGIN = "OUTPUT_ORIGIN"

    def tr(self, string: str) -> str:
        return QCoreApplication.translate(type(self).__name__, string)

    def name(self) -> str:
        return "generateisochronesovertime"

    def displayName(self) -> str:  # noqa: N802
        return self.tr("Generate isochrones over time")

    def group(self) -> str:
        return self.tr("3 · Analysis")

    def groupId(self) -> str:  # noqa: N802
        return "analysis"

    def createInstance(self):  # noqa: N802
        return GenerateIsochronesOverTime()

    def shortHelpString(self) -> str:  # noqa: N802
        return self.tr(
            "Generates travel-time isochrone polygons for one origin point across "
            "the day using OpenTripPlanner 1.5.0.\n\n"
            "For each timestamp in the configured window one GET /isochrone request "
            "is sent. All resulting polygons are merged into a single output layer "
            "with a 'time' field (QDateTime) compatible with the QGIS Temporal "
            "Controller — enabling day-long animation of the isochrone.\n\n"
            "Number of polygons = timestamps × cutoffs. Keep cutoffs to 1–2 "
            "to avoid very large output layers.\n\n"
            "DIRECTION=FROM: catchment reachable from the point.\n"
            "DIRECTION=TO: catchment that can reach the point.\n\n"
            "For non-transit modes (WALK/CAR/BICYCLE) GTFS is optional.\n"
            "Requires user-provided Java 8 and otp-1.5.0-shaded.jar.\n\n"
            "Complement to 'Generate isochrones' (N-1: many points, one time)."
        )

    def initAlgorithm(self, config=None):  # noqa: N802
        self.addParameter(
            QgsProcessingParameterPoint(
                self.ORIGIN_POINT,
                self.tr("Origin point"),
            )
        )
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
            self.tr("GTFS folder (required for transit modes; optional for WALK/CAR/BICYCLE)"),
            behavior=QgsProcessingParameterFile.Folder,
            optional=True,
        )
        _gtfs_param.setFlags(
            _gtfs_param.flags() | QgsProcessingParameterDefinition.FlagOptional
        )
        self.addParameter(_gtfs_param)
        self.addParameter(
            QgsProcessingParameterEnum(
                self.MODE,
                self.tr("Transport mode"),
                options=_MODE_OPTIONS,
                defaultValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.DIRECTION,
                self.tr("Direction (FROM: reachable from point; TO: can reach point)"),
                options=_DIRECTION_OPTIONS,
                defaultValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.CUTOFFS_MIN,
                self.tr(
                    "Cutoff thresholds (minutes, comma-separated). "
                    "Tip: use 1–2 cutoffs — polygons = timestamps × cutoffs."
                ),
                defaultValue="30",
            )
        )
        self.addParameter(
            QgsProcessingParameterDateTime(
                self.TIME_START,
                self.tr("Window start time"),
                type=QgsProcessingParameterDateTime.Time,
                defaultValue=QTime(6, 0, 0),
            )
        )
        self.addParameter(
            QgsProcessingParameterDateTime(
                self.TIME_END,
                self.tr("Window end time"),
                type=QgsProcessingParameterDateTime.Time,
                defaultValue=QTime(22, 0, 0),
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.INTERVAL,
                self.tr("Time interval between isochrones (minutes)"),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=15,
                minValue=1,
            )
        )
        self.addParameter(
            QgsProcessingParameterDateTime(
                self.ANALYSIS_DATE,
                self.tr("Analysis date"),
                type=QgsProcessingParameterDateTime.Date,
                defaultValue=QDateTime(QDate.currentDate()),
            )
        )
        self.addParameter(
            QgsProcessingParameterFile(
                self.WORK_DIR,
                self.tr("Working directory (graph, cache)"),
                behavior=QgsProcessingParameterFile.Folder,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT_ISOCHRONES,
                self.tr("Output isochrones (polygon layer)"),
                type=QgsProcessing.TypeVectorPolygon,
            )
        )
        _csv_param = QgsProcessingParameterFileDestination(
            self.OUTPUT_AREA_CSV,
            self.tr("Area-over-time CSV (optional)"),
            fileFilter="CSV files (*.csv)",
            optional=True,
            createByDefault=False,
        )
        _csv_param.setFlags(
            _csv_param.flags() | QgsProcessingParameterDefinition.FlagOptional
        )
        self.addParameter(_csv_param)
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT_ORIGIN,
                self.tr("Origin point (run metadata)"),
                type=QgsProcessing.TypeVectorPoint,
                optional=True,
                createByDefault=True,
            )
        )

        # --- Advanced routing ---
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

        # --- Advanced server ---
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

    def _add_advanced(self, param) -> None:
        param.setFlags(param.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(param)

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        # ── Java ────────────────────────────────────────────────────────────
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
            feedback.pushInfo(self.tr("Using Java path from QSettings: {}").format(java))
        else:
            java = self._require_file(parameters, context, self.JAVA_PATH, "Java 8 binary")
        is_java8, java_ver, java_err = check_java_version(java)
        if not is_java8:
            raise QgsProcessingException(self.tr(java_err))
        feedback.pushInfo(self.tr("Java OK: version {}").format(java_ver))

        jar = self._require_file(
            parameters, context, self.OTP_JAR_PATH, "OTP 1.5.0 jar",
            fix_hint=self.tr(
                "Download otp-1.5.0-shaded.jar from Maven Central "
                "(groupId=org.opentripplanner, artifactId=otp, version=1.5.0, "
                "classifier=shaded) and set the 'OpenTripPlanner 1.5.0 jar' parameter."
            ),
        )
        pbf = self._require_file(parameters, context, self.OSM_PBF, "OSM .pbf extract")

        # ── Mode / direction / cutoffs ────────────────────────────────────
        mode_idx = self.parameterAsEnum(parameters, self.MODE, context)
        mode_str = _MODE_OPTIONS[mode_idx]
        direction_idx = self.parameterAsEnum(parameters, self.DIRECTION, context)
        direction_str = _DIRECTION_OPTIONS[direction_idx]
        is_transit = mode_str.upper() in TRANSIT_MODES

        cutoffs_raw = self.parameterAsString(parameters, self.CUTOFFS_MIN, context).strip()
        try:
            cutoffs_min = sorted({int(x.strip()) for x in cutoffs_raw.split(",") if x.strip()})
        except ValueError:
            raise QgsProcessingException(self.tr(
                "CUTOFFS_MIN must be a comma-separated list of positive integers, got: {}"
            ).format(cutoffs_raw))
        if not cutoffs_min or any(c <= 0 for c in cutoffs_min):
            raise QgsProcessingException(self.tr(
                "CUTOFFS_MIN must contain at least one positive integer."
            ))
        cutoffs_sec = [c * 60 for c in cutoffs_min]

        # ── GTFS ─────────────────────────────────────────────────────────
        gtfs_dir_str = self.parameterAsFile(parameters, self.GTFS_FILES, context)
        gtfs_files: list[Path] = []
        if gtfs_dir_str:
            gtfs_dir = Path(gtfs_dir_str)
            if gtfs_dir.is_dir():
                gtfs_files = sorted(gtfs_dir.glob("*.zip"))
                feedback.pushInfo(self.tr(
                    "Discovered {} GTFS feed(s): {}"
                ).format(len(gtfs_files), ", ".join(p.name for p in gtfs_files)))
        if is_transit and not gtfs_files:
            raise QgsProcessingException(self.tr(
                "GTFS_FILES folder is required for transit mode '{}'. "
                "Supply a folder containing one or more GTFS .zip archives, "
                "or choose a non-transit mode (WALK/CAR/BICYCLE) for street-only routing."
            ).format(mode_str))
        if not is_transit and not gtfs_files:
            feedback.pushInfo(self.tr(
                "No GTFS supplied — building street-only graph for mode '{}'."
            ).format(mode_str))

        # ── Date / time window ────────────────────────────────────────────
        qdt_date = self.parameterAsDateTime(parameters, self.ANALYSIS_DATE, context)
        analysis_qdate = qdt_date.date()
        date_otp = analysis_qdate.toString("MM-dd-yyyy")
        date_iso = analysis_qdate.toString("yyyy-MM-dd")

        # QgsProcessingParameterDateTime(type=Time) on QGIS 3.40: same workaround as N-1.
        def _extract_time(key: str, fallback: QTime) -> QTime:
            raw = parameters.get(key)
            if isinstance(raw, QTime):
                return raw
            dt = self.parameterAsDateTime(parameters, key, context)
            t = dt.time()
            return t if t.isValid() else fallback

        t_start = _extract_time(self.TIME_START, QTime(6, 0, 0))
        t_end = _extract_time(self.TIME_END, QTime(22, 0, 0))

        if not t_start.isValid() or not t_end.isValid():
            raise QgsProcessingException(self.tr(
                "Invalid TIME_START or TIME_END value."
            ))
        if t_start >= t_end:
            raise QgsProcessingException(self.tr(
                "TIME_START must be before TIME_END."
            ))

        interval_min = self.parameterAsInt(parameters, self.INTERVAL, context)

        times = build_time_list(
            t_start.hour(), t_start.minute(),
            t_end.hour(), t_end.minute(),
            interval_min,
        )
        n_times = len(times)
        if n_times == 0:
            raise QgsProcessingException(self.tr(
                "No timestamps generated for the given window and interval."
            ))

        if is_transit and gtfs_files:
            self._warn_gtfs_date(gtfs_files, analysis_qdate, feedback)

        feedback.pushInfo(self.tr(
            "Mode={}, Direction={}, Cutoffs={} min, "
            "Window={}–{}, Interval={} min → {} timestamps, "
            "total requests={}."
        ).format(
            mode_str, direction_str, cutoffs_min,
            t_start.toString("HH:mm"), t_end.toString("HH:mm"),
            interval_min, n_times, n_times * len(cutoffs_min),
        ))

        # ── Origin point → WGS84 ──────────────────────────────────────────
        origin_pt = self.parameterAsPoint(parameters, self.ORIGIN_POINT, context)
        origin_crs = self.parameterAsPointCrs(parameters, self.ORIGIN_POINT, context)
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        if origin_crs.isValid() and origin_crs != wgs84:
            xform = QgsCoordinateTransform(origin_crs, wgs84, context.transformContext())
            origin_pt = xform.transform(origin_pt)
        from_lat, from_lon = origin_pt.y(), origin_pt.x()
        feedback.pushInfo(self.tr("Origin: lat={:.6f} lon={:.6f}").format(from_lat, from_lon))

        # ── Work dir ─────────────────────────────────────────────────────
        work_dir_str = self.parameterAsFile(parameters, self.WORK_DIR, context)
        if not work_dir_str:
            raise QgsProcessingException(self.tr("Working directory is required."))
        work_dir = Path(work_dir_str)
        work_dir.mkdir(parents=True, exist_ok=True)

        port = self.parameterAsInt(parameters, self.OTP_PORT, context)
        xmx_build = self.parameterAsString(parameters, self.OTP_XMX_BUILD, context) or "2G"
        xmx_serve = self.parameterAsString(parameters, self.OTP_XMX_SERVE, context) or "4G"
        keep_alive = self.parameterAsBool(parameters, self.KEEP_SERVER_ALIVE, context)

        # ── Optional CSV output path ───────────────────────────────────────
        csv_path = (self.parameterAsString(parameters, self.OUTPUT_AREA_CSV, context) or "").strip()

        # ── Graph build / cache ───────────────────────────────────────────
        existing_graph_dir_str = self.parameterAsFile(parameters, self.EXISTING_GRAPH_DIR, context)
        if existing_graph_dir_str:
            existing_dir = Path(existing_graph_dir_str)
            if not (existing_dir / "Graph.obj").exists():
                raise QgsProcessingException(self.tr(
                    "EXISTING_GRAPH_DIR does not contain Graph.obj: {}. "
                    "Point to the router directory (e.g. …/graphs/abc123/)."
                ).format(existing_dir))
            if existing_dir.parent.name != "graphs":
                raise QgsProcessingException(self.tr(
                    "EXISTING_GRAPH_DIR must be inside a 'graphs/' folder "
                    "(expected …/graphs/<router_id>/, got {})."
                ).format(existing_dir))
            router_id = existing_dir.name
            router_dir = existing_dir
            server_work_dir = existing_dir.parent.parent
            feedback.pushInfo(self.tr(
                "Using existing graph: {} (router_id={}); skipping build."
            ).format(router_dir, router_id))
            ensure_router_config(router_dir, None, feedback)
        else:
            server_work_dir = work_dir
            router_id = compute_router_id(pbf, gtfs_files)
            feedback.pushInfo(self.tr("Router ID: {}").format(router_id))
            router_dir = ensure_router_dir(work_dir, router_id, pbf, gtfs_files)
            gtfs_source_dir = Path(gtfs_dir_str) if gtfs_dir_str else None
            ensure_router_config(router_dir, gtfs_source_dir, feedback)
            if graph_build_complete(work_dir, router_id):
                feedback.pushInfo(self.tr("Graph cache hit — skipping build."))
            else:
                feedback.pushInfo(self.tr("Building OTP graph (this can take minutes)…"))
                try:
                    build_graph(java, jar, xmx_build, work_dir, router_id, feedback)
                except RuntimeError as e:
                    raise QgsProcessingException(str(e)) from e
                write_meta(router_dir, jar, [pbf, *gtfs_files])
        pointsets = ensure_pointsets_dir(server_work_dir)

        # ── Output sink ───────────────────────────────────────────────────
        out_fields = QgsFields()
        for fname, ftype in [
            ("time",       QVariant.DateTime),
            ("cutoff_min", QVariant.Int),
            ("mode",       QVariant.String),
            ("date",       QVariant.String),
            ("direction",  QVariant.String),
            ("area_km2",   QVariant.Double),
        ]:
            out_fields.append(QgsField(fname, ftype))

        sink, dest_id = self.parameterAsSink(
            parameters, self.OUTPUT_ISOCHRONES, context,
            out_fields, QgsWkbTypes.MultiPolygon, wgs84,
        )
        if sink is None:
            raise QgsProcessingException(self.tr("Could not create output layer."))

        # ── Origin point sink ─────────────────────────────────────────────
        origin_fields = QgsFields()
        for _fn, _ft in [
            ("lat",          QVariant.Double),
            ("lon",          QVariant.Double),
            ("mode",         QVariant.String),
            ("direction",    QVariant.String),
            ("date",         QVariant.String),
            ("time_start",   QVariant.String),
            ("time_end",     QVariant.String),
            ("interval_min", QVariant.Int),
            ("cutoffs_min",  QVariant.String),
            ("router_id",    QVariant.String),
        ]:
            origin_fields.append(QgsField(_fn, _ft))
        origin_sink, origin_dest_id = self.parameterAsSink(
            parameters, self.OUTPUT_ORIGIN, context,
            origin_fields, QgsWkbTypes.Point, wgs84,
        )

        # ── OTP server lifecycle ──────────────────────────────────────────
        existing = probe_otp(port)
        server_ctx = None
        try:
            if existing:
                ver = existing.get("serverVersion", {})
                ver_str = ver.get("version") if isinstance(ver, dict) else str(ver)
                feedback.pushInfo(self.tr(
                    "Reusing OTP already running on port {} (version {})."
                ).format(port, ver_str))
            else:
                if port_is_listening(port):
                    raise QgsProcessingException(self.tr(
                        "Port {} is held by a non-OTP process. Pick a "
                        "different OTP_PORT or stop the conflicting service."
                    ).format(port))
                feedback.pushInfo(self.tr("Starting OTP server on port {}…").format(port))
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

            # ── Origin point feature ───────────────────────────────────────
            if origin_sink is not None:
                origin_feat = QgsFeature(origin_fields)
                origin_feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(from_lon, from_lat)))
                origin_feat.setAttributes([
                    from_lat, from_lon, mode_str, direction_str, date_iso,
                    t_start.toString("HH:mm"), t_end.toString("HH:mm"),
                    interval_min, ",".join(str(c) for c in cutoffs_min), router_id,
                ])
                origin_sink.addFeature(origin_feat, QgsFeatureSink.FastInsert)

            # ── Timestamp loop ─────────────────────────────────────────────
            isochrone_client = IsochroneClient(port=port, router=router_id)
            arrive_by = (direction_str == "TO")

            area_rows: list[tuple] = []
            # EPSG:3035 Lambert Azimuthal Equal Area — standard for European statistics.
            metric_crs = QgsCoordinateReferenceSystem("EPSG:3035")
            to_metric = QgsCoordinateTransform(wgs84, metric_crs, context.transformContext())

            max_walk_distance = self.parameterAsInt(parameters, self.MAX_WALK_DISTANCE, context)
            walk_reluctance = self.parameterAsDouble(parameters, self.WALK_RELUCTANCE, context)
            wait_reluctance = self.parameterAsDouble(parameters, self.WAIT_RELUCTANCE, context)
            transfer_penalty = self.parameterAsInt(parameters, self.TRANSFER_PENALTY, context)
            min_transfer_time = self.parameterAsInt(parameters, self.MIN_TRANSFER_TIME, context)

            ok_count = 0
            failed_count = 0
            ok_polygons = 0

            for i, time_str in enumerate(times):
                if feedback.isCanceled():
                    break
                feedback.setProgress(int(100 * i / n_times))
                feedback.pushInfo(self.tr("[{}/{}] {}").format(i + 1, n_times, time_str))

                try:
                    geojson_str = isochrone_client.get_isochrone(
                        from_lat=from_lat,
                        from_lon=from_lon,
                        cutoffs_sec=cutoffs_sec,
                        mode=mode_str,
                        date_mmddyyyy=date_otp,
                        time_hhmmss=time_str,
                        direction=direction_str,
                        arrive_by=arrive_by,
                        max_walk_distance=max_walk_distance,
                        walk_reluctance=walk_reluctance,
                        wait_reluctance=wait_reluctance,
                        transfer_penalty=transfer_penalty,
                        min_transfer_time=min_transfer_time,
                    )
                except OtpClientError as exc:
                    feedback.pushWarning(self.tr("  {}: {}. Skipping.").format(time_str, exc))
                    failed_count += 1
                    continue

                parsed = IsochroneClient.parse_isochrones(geojson_str)
                dt = QDateTime(analysis_qdate, QTime.fromString(time_str, "HH:mm:ss"))

                for item in parsed:
                    cutoff_min = round(item["cutoff_sec"] / 60)
                    raw_geom = QgsJsonUtils.geometryFromGeoJson(json.dumps(item["geometry"]))
                    geom = _geom_to_multipolygon(raw_geom)
                    if geom is None or geom.isEmpty():
                        feedback.pushWarning(self.tr(
                            "  {} cutoff={} min: no polygon parts, skipped."
                        ).format(time_str, cutoff_min))
                        continue

                    metric_geom = QgsGeometry(geom)
                    metric_geom.transform(to_metric)
                    area_km2 = round(metric_geom.area() / 1e6, 4)

                    out_feat = QgsFeature(out_fields)
                    out_feat.setGeometry(geom)
                    out_feat.setAttributes([
                        dt, cutoff_min, mode_str, date_iso, direction_str, area_km2,
                    ])
                    if sink.addFeature(out_feat, QgsFeatureSink.FastInsert):
                        ok_polygons += 1
                    else:
                        feedback.pushWarning(self.tr(
                            "  {} cutoff={} min: sink rejected feature."
                        ).format(time_str, cutoff_min))

                    if csv_path:
                        area_rows.append((time_str, cutoff_min, area_km2))

                ok_count += 1

            feedback.setProgress(100)
            feedback.pushInfo(self.tr(
                "Done: {} timestamps OK, {} failed, {} polygons written."
            ).format(ok_count, failed_count, ok_polygons))

            if ok_polygons == 0 and ok_count > 0:
                feedback.pushWarning(self.tr(
                    "No polygons were written. OTP returned null geometry for every "
                    "timestamp — this typically means the origin point could not be "
                    "snapped to a '{mode}'-accessible road. Check that the point is on "
                    "or near a driveable road (not inside a pedestrian zone, private "
                    "area, or unmapped location) and retry."
                ).format(mode=mode_str))

            if csv_path:
                with open(csv_path, "w", newline="", encoding="utf-8") as f:
                    w = csv.writer(f)
                    w.writerow(["time", "cutoff_min", "area_km2"])
                    w.writerows(area_rows)
                feedback.pushInfo(self.tr("Area CSV written: {}").format(csv_path))

            if server_ctx is not None:
                server_ctx.__exit__(None, None, None)
                server_ctx = None

            result = {self.OUTPUT_ISOCHRONES: dest_id}
            if csv_path:
                result[self.OUTPUT_AREA_CSV] = csv_path
            if origin_sink is not None:
                result[self.OUTPUT_ORIGIN] = origin_dest_id
            return result

        except BaseException:
            if server_ctx is not None:
                server_ctx.__exit__(*self._exc_info())
            raise

    def _log_router_diagnostic(self, client: OtpClient, feedback) -> None:
        try:
            info = client.get_router_info()
        except OtpClientError as e:
            feedback.pushWarning(self.tr("Could not fetch router diagnostic: {}").format(e))
            return
        from datetime import datetime, timezone

        def _epoch_to_iso(value) -> str:
            try:
                return datetime.fromtimestamp(int(value), tz=timezone.utc).date().isoformat()
            except (TypeError, ValueError, OSError):
                return str(value)

        feedback.pushInfo(self.tr("--- OTP router diagnostic ---"))
        feedback.pushInfo(self.tr(
            "hasTransit = {}; transitServiceStarts = {}; transitServiceEnds = {}"
        ).format(
            info.get("hasTransit"),
            _epoch_to_iso(info.get("transitServiceStarts")),
            _epoch_to_iso(info.get("transitServiceEnds")),
        ))
        feedback.pushInfo(self.tr("-----------------------------"))

    def _warn_gtfs_date(self, gtfs_files: list, analysis_date, feedback) -> None:
        date_str = analysis_date.toString("yyyyMMdd")
        date_int = int(date_str)
        day_of_week = analysis_date.dayOfWeek()
        if day_of_week >= 6:
            day_name = "Saturday" if day_of_week == 6 else "Sunday"
            feedback.pushWarning(self.tr(
                "ANALYSIS_DATE is a {} ({}). Weekend transit "
                "schedules may differ significantly from weekday analyses."
            ).format(day_name, date_str))
        for gtfs_path in gtfs_files:
            try:
                with _zf.ZipFile(str(gtfs_path)) as z:
                    cal_name = next(
                        (n for n in z.namelist() if n.split("/")[-1] == "calendar.txt"),
                        None,
                    )
                    if cal_name is None:
                        feedback.pushWarning(self.tr(
                            "No calendar.txt in {} — cannot validate "
                            "analysis date against GTFS service range."
                        ).format(gtfs_path.name))
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
                        "{}: no services active on {}. "
                        "OTP may return all-unreachable isochrones for this date."
                    ).format(gtfs_path.name, date_str))
                else:
                    feedback.pushInfo(self.tr(
                        "{}: {} service(s) active on {}."
                    ).format(gtfs_path.name, active, date_str))
            except Exception as exc:  # noqa: BLE001
                feedback.pushWarning(self.tr(
                    "Could not read {} for date validation: {}"
                ).format(gtfs_path.name, exc))

    def _require_file(
        self, parameters, context, key: str, label: str, fix_hint: str = ""
    ) -> Path:
        raw = self.parameterAsFile(parameters, key, context)
        if not raw:
            msg = self.tr("{} is required (parameter {}).").format(label, key)
            if fix_hint:
                msg += " " + fix_hint
            raise QgsProcessingException(msg)
        path = Path(raw)
        if not path.is_file():
            msg = self.tr("{} not found at: {} (parameter {}).").format(label, path, key)
            if fix_hint:
                msg += " " + fix_hint
            raise QgsProcessingException(msg)
        return path

    @staticmethod
    def _exc_info():
        import sys
        return sys.exc_info()
