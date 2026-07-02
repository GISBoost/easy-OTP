"""N-6 Analysis algorithm: walking route through ordered via-points (0.6.1).

User supplies a START point, an END point, and an optional layer of via-points
in any order. The plugin orders them with nearest-neighbour + 2-opt, then makes
sequential OTP /plan queries (one per segment). Output: one LineString feature per
leg, attributed with time/distance/order.
"""

from __future__ import annotations

import sys
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
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFile,
    QgsProcessingParameterNumber,
    QgsProcessingParameterPoint,
    QgsProcessingParameterString,
    QgsWkbTypes,
)

from ..core.otp_client import OtpClient, OtpClientError
from ..core.otp_server import (
    OtpServer,
    build_graph,
    check_java_version,
    compute_router_id,
    ensure_pointsets_dir,
    ensure_router_dir,
    graph_build_complete,
    port_is_listening,
    probe_otp,
    wait_until_ready,
    write_meta,
)
from ..core.plan_client import PlanClient
from ..core.route_ordering import order_via_points

_VIA_POINTS_WARN = 20


class RouteViaPoints(QgsProcessingAlgorithm):
    START_POINT = "START_POINT"
    END_POINT = "END_POINT"
    VIA_POINTS = "VIA_POINTS"
    ANALYSIS_DATE = "ANALYSIS_DATE"
    TIME = "TIME"
    OSM_PBF = "OSM_PBF"
    WORK_DIR = "WORK_DIR"
    OUTPUT_ROUTE = "OUTPUT_ROUTE"

    MAX_WALK_DISTANCE = "MAX_WALK_DISTANCE"
    WALK_RELUCTANCE = "WALK_RELUCTANCE"

    USE_SAVED_JAVA = "USE_SAVED_JAVA"
    JAVA_PATH = "JAVA_PATH"
    OTP_JAR_PATH = "OTP_JAR_PATH"
    OTP_XMX_BUILD = "OTP_XMX_BUILD"
    OTP_XMX_SERVE = "OTP_XMX_SERVE"
    OTP_PORT = "OTP_PORT"
    EXISTING_GRAPH_DIR = "EXISTING_GRAPH_DIR"
    KEEP_SERVER_ALIVE = "KEEP_SERVER_ALIVE"

    def tr(self, string: str) -> str:
        return QCoreApplication.translate(type(self).__name__, string)

    def name(self) -> str:
        return "routeviapoints"

    def displayName(self) -> str:  # noqa: N802
        return self.tr("Route via points (city trip planner)")

    def group(self) -> str:
        return self.tr("3 · Analysis")

    def groupId(self) -> str:  # noqa: N802
        return "analysis"

    def createInstance(self):  # noqa: N802
        return RouteViaPoints()

    def shortHelpString(self) -> str:  # noqa: N802
        return self.tr(
            "Plan a walking city tour: supply a START point, an END point, and an "
            "optional layer of via-points (landmarks) in any order. The plugin "
            "automatically computes a sensible visit order (nearest-neighbour + 2-opt) "
            "and makes one OTP /plan query per segment (N+1 queries). Output: one "
            "line feature per route segment, attributed with duration_min, distance_m, "
            "and visit order.\n\n"
            "A soft warning is shown when more than {0} via-points are supplied."
        ).format(_VIA_POINTS_WARN)

    def initAlgorithm(self, config=None):  # noqa: N802
        self.addParameter(
            QgsProcessingParameterPoint(
                self.START_POINT,
                self.tr("Start point"),
            )
        )
        self.addParameter(
            QgsProcessingParameterPoint(
                self.END_POINT,
                self.tr("End point"),
            )
        )
        _via_param = QgsProcessingParameterFeatureSource(
            self.VIA_POINTS,
            self.tr("Via-points layer (optional; 0 features = direct A→B walk)"),
            types=[QgsProcessing.TypeVectorPoint],
            optional=True,
        )
        _via_param.setFlags(
            _via_param.flags() | QgsProcessingParameterDefinition.FlagOptional
        )
        self.addParameter(_via_param)
        self.addParameter(
            QgsProcessingParameterDateTime(
                self.ANALYSIS_DATE,
                self.tr("Analysis date (routing-irrelevant for WALK; required by OTP endpoint)"),
                type=QgsProcessingParameterDateTime.Date,
                defaultValue=QDateTime(QDate.currentDate(), QTime(0, 0)),
            )
        )
        self.addParameter(
            QgsProcessingParameterDateTime(
                self.TIME,
                self.tr("Departure time (routing-irrelevant for WALK; required by OTP endpoint)"),
                type=QgsProcessingParameterDateTime.Time,
                defaultValue=QTime(8, 30),
            )
        )
        self.addParameter(
            QgsProcessingParameterFile(
                self.OSM_PBF,
                self.tr("OSM extract (.osm.pbf) — street network for WALK routing"),
                behavior=QgsProcessingParameterFile.File,
                extension="pbf",
            )
        )
        self.addParameter(
            QgsProcessingParameterFile(
                self.WORK_DIR,
                self.tr("Working directory (graph cache)"),
                behavior=QgsProcessingParameterFile.Folder,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT_ROUTE,
                self.tr("Output route (one feature per segment/leg)"),
                type=QgsProcessing.TypeVectorLine,
            )
        )

        # --- Routing (advanced) ---
        self._add_advanced(
            QgsProcessingParameterNumber(
                self.MAX_WALK_DISTANCE,
                self.tr(
                    "Maximum walk distance (m) — city tour default is 50 000; "
                    "OTP will not route legs longer than this value"
                ),
                type=QgsProcessingParameterNumber.Double,
                defaultValue=50000.0,
                minValue=0.0,
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
                "Download otp-1.5.0-shaded.jar from Maven Central "
                "and set the OTP jar parameter."
            ),
        )
        pbf = self._require_file(parameters, context, self.OSM_PBF, "OSM .pbf extract")

        work_dir_str = self.parameterAsFile(parameters, self.WORK_DIR, context)
        if not work_dir_str:
            raise QgsProcessingException(self.tr("Working directory is required."))
        work_dir = Path(work_dir_str)
        work_dir.mkdir(parents=True, exist_ok=True)

        port = self.parameterAsInt(parameters, self.OTP_PORT, context)
        xmx_build = self.parameterAsString(parameters, self.OTP_XMX_BUILD, context) or "2G"
        xmx_serve = self.parameterAsString(parameters, self.OTP_XMX_SERVE, context) or "4G"
        keep_alive = self.parameterAsBool(parameters, self.KEEP_SERVER_ALIVE, context)
        max_walk_distance = self.parameterAsDouble(parameters, self.MAX_WALK_DISTANCE, context)
        walk_reluctance = self.parameterAsDouble(parameters, self.WALK_RELUCTANCE, context)

        # --- Date / time ---
        qdt_date = self.parameterAsDateTime(parameters, self.ANALYSIS_DATE, context)
        date_s = qdt_date.date().toString("MM-dd-yyyy")
        raw_time = parameters.get(self.TIME)
        time_t = (
            raw_time if isinstance(raw_time, QTime)
            else self.parameterAsDateTime(parameters, self.TIME, context).time()
        )
        time_s = time_t.toString("HH:mm:ss")

        # --- Reproject START / END to EPSG:4326 ---
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        start_pt = self.parameterAsPoint(parameters, self.START_POINT, context, wgs84)
        start = (start_pt.y(), start_pt.x())  # (lat, lon)
        end_pt = self.parameterAsPoint(parameters, self.END_POINT, context, wgs84)
        end = (end_pt.y(), end_pt.x())

        # --- Extract via-points → (fid, lat, lon) ---
        vias_raw: list[tuple[int, float, float]] = []
        via_source = self.parameterAsSource(parameters, self.VIA_POINTS, context)
        if via_source is not None:
            xform = QgsCoordinateTransform(
                via_source.sourceCrs(), wgs84, context.transformContext()
            )
            for feat in via_source.getFeatures():
                geom = feat.geometry()
                if geom.isNull() or geom.isEmpty():
                    feedback.pushWarning(
                        self.tr(
                            "Via-point feature {0} has null/empty geometry — skipped."
                        ).format(feat.id())
                    )
                    continue
                pt = xform.transform(geom.centroid().asPoint())
                vias_raw.append((feat.id(), pt.y(), pt.x()))

        if len(vias_raw) > _VIA_POINTS_WARN:
            feedback.pushWarning(
                self.tr(
                    "{0} via-points supplied (>{1}). The route may be slow to compute "
                    "and visually complex. Consider splitting the tour into smaller runs."
                ).format(len(vias_raw), _VIA_POINTS_WARN)
            )

        # --- Compute visit order (NN + 2-opt, pure Python) ---
        coords_only = [(lat, lon) for _, lat, lon in vias_raw]
        ordered_indices = order_via_points(start, coords_only, end)
        ordered_vias = [vias_raw[i] for i in ordered_indices]
        feedback.pushInfo(
            self.tr("Visit order (via-point feature ids): {0}").format(
                [fid for fid, _, _ in ordered_vias] if ordered_vias else "(none)"
            )
        )

        # --- Output sink ---
        fields = QgsFields()
        fields.append(QgsField("segment_order", QVariant.Int))
        fields.append(QgsField("from_label",    QVariant.String))
        fields.append(QgsField("to_label",      QVariant.String))
        fields.append(QgsField("duration_min",  QVariant.Double))
        fields.append(QgsField("distance_m",    QVariant.Double))
        fields.append(QgsField("mode",          QVariant.String))
        fields.append(QgsField("via_point_id",  QVariant.LongLong))
        sink, dest_id = self.parameterAsSink(
            parameters, self.OUTPUT_ROUTE, context,
            fields, QgsWkbTypes.LineString, wgs84,
        )
        if sink is None:
            raise QgsProcessingException(self.tr("Could not create output feature sink."))

        # --- Graph / server lifecycle ---
        existing_graph_dir_str = self.parameterAsFile(
            parameters, self.EXISTING_GRAPH_DIR, context
        )
        if existing_graph_dir_str:
            existing_dir = Path(existing_graph_dir_str)
            if not (existing_dir / "Graph.obj").exists():
                raise QgsProcessingException(
                    self.tr(
                        "EXISTING_GRAPH_DIR does not contain Graph.obj: {0}"
                    ).format(existing_dir)
                )
            router_id = existing_dir.name
            server_work_dir = existing_dir.parent.parent
            feedback.pushInfo(
                self.tr(
                    "Using existing graph: {0} (router_id={1}); skipping build."
                ).format(existing_dir, router_id)
            )
        else:
            server_work_dir = work_dir
            router_id = compute_router_id(pbf, [])
            feedback.pushInfo(self.tr("Router ID: {0}").format(router_id))
            ensure_router_dir(work_dir, router_id, pbf, [])
            if graph_build_complete(work_dir, router_id):
                feedback.pushInfo(self.tr("Graph cache hit — skipping build."))
            else:
                feedback.pushInfo(
                    self.tr("Building street-only OTP graph (no GTFS required for WALK)...")
                )
                try:
                    build_graph(java, jar, xmx_build, work_dir, router_id, feedback)
                except RuntimeError as e:
                    raise QgsProcessingException(str(e)) from e
                router_dir = server_work_dir / "graphs" / router_id
                write_meta(router_dir, jar, [pbf])

        if feedback.isCanceled():
            return {}

        pointsets = ensure_pointsets_dir(server_work_dir)
        server_ctx = None
        try:
            existing = probe_otp(port)
            if existing:
                ver = existing.get("serverVersion", {})
                ver_str = ver.get("version") if isinstance(ver, dict) else str(ver)
                feedback.pushInfo(
                    self.tr(
                        "Reusing OTP already running on port {0} (version {1})."
                    ).format(port, ver_str)
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

            if feedback.isCanceled():
                return {}

            # --- Sequential /plan queries (OTP 1.5 intermediatePlaces has NPE for off-network points) ---
            plan_client = PlanClient(
                hostname="localhost",
                port=port,
                router=router_id,
                timeout_s=60.0,
            )
            chain = [start] + [(lat, lon) for _, lat, lon in ordered_vias] + [end]
            labels = ["START"] + [f"P{i + 1}" for i in range(len(ordered_vias))] + ["END"]
            n_vias = len(ordered_vias)

            feedback.pushInfo(
                self.tr(
                    "Querying OTP: {0} segment(s), mode=WALK, date={1}, time={2}..."
                ).format(len(chain) - 1, date_s, time_s)
            )

            features_written = 0
            total_dur = 0.0
            total_dist = 0.0

            for i in range(len(chain) - 1):
                if feedback.isCanceled():
                    return {}
                try:
                    seg = plan_client.get_trip_via(
                        from_lat=chain[i][0],
                        from_lon=chain[i][1],
                        to_lat=chain[i + 1][0],
                        to_lon=chain[i + 1][1],
                        intermediate_places=[],
                        mode="WALK",
                        date_mmddyyyy=date_s,
                        time_hhmmss=time_s,
                        max_walk_distance=max_walk_distance,
                        walk_reluctance=walk_reluctance,
                    )
                except OtpClientError as e:
                    raise QgsProcessingException(
                        self.tr("Network error querying {0}→{1}: {2}").format(
                            labels[i], labels[i + 1], e
                        )
                    ) from e

                if seg["status"] != "OK":
                    via_fid: int | None = None
                    if 0 < i + 1 <= n_vias:
                        via_fid = ordered_vias[i][0]
                    raise QgsProcessingException(
                        self.tr(
                            "OTP cannot route {0}→{1} on foot "
                            "(status {2}, via-point feature id: {3}). "
                            "Check that the point is accessible by pedestrian network "
                            "and not isolated (e.g. inside a building or unreachable field)."
                        ).format(labels[i], labels[i + 1], seg["status"], via_fid)
                    )

                legs = seg.get("legs") or []
                if not legs:
                    feedback.pushWarning(
                        self.tr("Segment {0}→{1} returned no legs — skipped.").format(
                            labels[i], labels[i + 1]
                        )
                    )
                    continue

                total_dur += seg.get("duration") or 0.0
                total_dist += seg.get("walk_distance_m") or 0.0

                # Each WALK A→B query returns exactly 1 leg; take legs[0] for geometry.
                leg = legs[0]
                from_label = labels[i]
                to_label = labels[i + 1]
                via_fid_out = ordered_vias[i][0] if i < n_vias else None

                pts = [QgsPointXY(lon, lat) for lat, lon in leg["geometry"]]
                if len(pts) < 2:
                    feedback.pushWarning(
                        self.tr(
                            "Segment {0} ({1}→{2}) has fewer than 2 geometry points "
                            "— skipped."
                        ).format(i + 1, from_label, to_label)
                    )
                    continue

                feat = QgsFeature(fields)
                feat.setGeometry(QgsGeometry.fromPolylineXY(pts))
                feat.setAttributes([
                    i + 1,
                    from_label,
                    to_label,
                    leg["duration_min"],
                    leg["distance_m"],
                    leg["mode"],
                    via_fid_out,
                ])
                sink.addFeature(feat, QgsFeatureSink.FastInsert)
                features_written += 1

            # --- Summary ---
            feedback.pushInfo(
                self.tr(
                    "Route complete: {0} segment(s), {1} min total, {2} m total. "
                    "Via-point count: {3}. Visit order (fids): {4}"
                ).format(
                    features_written,
                    round(total_dur, 2),
                    round(total_dist, 1),
                    n_vias,
                    [fid for fid, _, _ in ordered_vias] if ordered_vias else "(none)",
                )
            )

            if server_ctx is not None:
                server_ctx.__exit__(None, None, None)
                server_ctx = None

            return {self.OUTPUT_ROUTE: dest_id}

        except BaseException:
            if server_ctx is not None:
                server_ctx.__exit__(*sys.exc_info())
            raise

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _add_advanced(self, param) -> None:
        param.setFlags(param.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(param)

    def _require_file(
        self, parameters, context, key: str, label: str, fix_hint: str = ""
    ) -> Path:
        raw = self.parameterAsFile(parameters, key, context)
        if not raw:
            msg = self.tr("{0} is required (parameter {1}).").format(label, key)
            if fix_hint:
                msg += " " + fix_hint
            raise QgsProcessingException(msg)
        path = Path(raw)
        if not path.is_file():
            msg = self.tr("{0} not found at: {1} (parameter {2}).").format(
                label, path, key
            )
            if fix_hint:
                msg += " " + fix_hint
            raise QgsProcessingException(msg)
        return path
