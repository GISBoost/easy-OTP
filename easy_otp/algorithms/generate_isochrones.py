"""GenerateIsochrones: travel-time isochrone polygons from N origin points.

Automates the manual "Izochrony" R workflow from gisboost/OpenTripPlanner.md.
For each origin point sends one GET /otp/routers/{router}/isochrone request with
a list of cutoff thresholds and merges all results into one polygon output layer.
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
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFile,
    QgsProcessingParameterNumber,
    QgsProcessingParameterString,
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

_MODE_OPTIONS = ["TRANSIT", "BUS", "RAIL", "TRAM", "SUBWAY", "WALK", "CAR", "BICYCLE"]
_DIRECTION_OPTIONS = ["FROM", "TO"]


def _geom_to_multipolygon(geom: QgsGeometry) -> "QgsGeometry | None":
    """Normalise any OTP isochrone geometry to MultiPolygon.

    makeValid() in GEOS 3.13+ can return GeometryCollection when the input
    polygon is self-intersecting (splits the ring into polygon parts +
    degenerate linestrings). Extract only the polygon parts and rebuild.
    """
    if geom is None or geom.isEmpty():
        return None
    geom = geom.makeValid()
    if geom is None or geom.isEmpty():
        return None
    geom_type = QgsWkbTypes.geometryType(geom.wkbType())
    if geom_type == QgsWkbTypes.PolygonGeometry:
        geom.convertToMultiType()
        return geom
    # GeometryCollection: keep polygon parts only, discard linestrings/points
    parts = []
    for g in geom.asGeometryCollection():
        if QgsWkbTypes.geometryType(g.wkbType()) == QgsWkbTypes.PolygonGeometry:
            if g.isMultipart():
                parts.extend(g.asMultiPolygon())
            else:
                parts.append(g.asPolygon())
    return QgsGeometry.fromMultiPolygonXY(parts) if parts else None


class GenerateIsochrones(QgsProcessingAlgorithm):
    ORIGIN_POINTS = "ORIGIN_POINTS"
    OSM_PBF = "OSM_PBF"
    GTFS_FILES = "GTFS_FILES"
    MODE = "MODE"
    DIRECTION = "DIRECTION"
    CUTOFFS_MIN = "CUTOFFS_MIN"
    ANALYSIS_DATE = "ANALYSIS_DATE"
    TIME = "TIME"
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

    def tr(self, string: str) -> str:
        return QCoreApplication.translate(type(self).__name__, string)

    def name(self) -> str:
        return "generateisochrones"

    def displayName(self) -> str:  # noqa: N802
        return self.tr("Generate isochrones")

    def group(self) -> str:
        return self.tr("3 · Analysis")

    def groupId(self) -> str:  # noqa: N802
        return "analysis"

    def createInstance(self):  # noqa: N802
        return GenerateIsochrones()

    def shortHelpString(self) -> str:  # noqa: N802
        return self.tr(
            "Generates travel-time isochrone polygons from one or many origin "
            "points using OpenTripPlanner 1.5.0.\n\n"
            "For each origin point one GET /isochrone request is sent with the "
            "configured cutoff thresholds. All resulting polygons are merged into "
            "a single output layer with attributes: point_id, name, cutoff_min, "
            "mode, date, time, direction.\n\n"
            "DIRECTION=FROM: catchment reachable from the point.\n"
            "DIRECTION=TO: catchment that can reach the point.\n\n"
            "For non-transit modes (WALK/CAR/BICYCLE) GTFS is optional — OTP "
            "will build a street-only graph from the OSM extract.\n\n"
            "Requires user-provided Java 8 and otp-1.5.0-shaded.jar."
        )

    def initAlgorithm(self, config=None):  # noqa: N802
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.ORIGIN_POINTS,
                self.tr("Origin points (1..N)"),
                [QgsProcessing.TypeVectorPoint],
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
                self.tr("Cutoff thresholds (minutes, comma-separated)"),
                defaultValue="15,30,45",
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
            QgsProcessingParameterDateTime(
                self.TIME,
                self.tr("Departure time"),
                type=QgsProcessingParameterDateTime.Time,
                defaultValue=QTime(8, 0, 0),
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
        feedback.pushInfo(self.tr(
            f"Mode={mode_str}, Direction={direction_str}, "
            f"Cutoffs={cutoffs_min} min ({cutoffs_sec} s)"
        ))

        # ── GTFS ─────────────────────────────────────────────────────────
        gtfs_dir_str = self.parameterAsFile(parameters, self.GTFS_FILES, context)
        gtfs_files: list[Path] = []
        if gtfs_dir_str:
            gtfs_dir = Path(gtfs_dir_str)
            if gtfs_dir.is_dir():
                gtfs_files = sorted(gtfs_dir.glob("*.zip"))
                feedback.pushInfo(self.tr(
                    f"Discovered {len(gtfs_files)} GTFS feed(s): "
                    f"{', '.join(p.name for p in gtfs_files)}"
                ))
        if is_transit and not gtfs_files:
            raise QgsProcessingException(self.tr(
                "GTFS_FILES folder is required for transit mode '{}'. "
                "Supply a folder containing one or more GTFS .zip archives, "
                "or choose a non-transit mode (WALK/CAR/BICYCLE) for street-only routing."
            ).format(mode_str))
        if not is_transit and not gtfs_files:
            feedback.pushInfo(self.tr(
                f"No GTFS supplied — building street-only graph for mode '{mode_str}'."
            ))

        # ── Date / time ───────────────────────────────────────────────────
        qdt_date = self.parameterAsDateTime(parameters, self.ANALYSIS_DATE, context)
        date_s = qdt_date.date().toString("MM-dd-yyyy")
        # QgsProcessingParameterDateTime(type=Time) stores values as QTime in
        # QGIS 3.40. parameterAsDateTime() on a raw QTime returns an invalid
        # QDateTime. Read the raw value directly (same workaround as RunTA).
        raw_time = parameters.get(self.TIME)
        time_t = (
            raw_time if isinstance(raw_time, QTime)
            else self.parameterAsDateTime(parameters, self.TIME, context).time()
        )
        time_s = time_t.toString("HH:mm:ss") if time_t.isValid() else "08:00:00"

        if is_transit and gtfs_files:
            self._warn_gtfs_date(gtfs_files, qdt_date.date(), feedback)

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
                f"Using existing graph: {router_dir} (router_id={router_id}); skipping build."
            ))
            ensure_router_config(router_dir, None, feedback)
        else:
            server_work_dir = work_dir
            router_id = compute_router_id(pbf, gtfs_files)
            feedback.pushInfo(self.tr(f"Router ID: {router_id}"))
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
            ("point_id",   QVariant.Int),
            ("name",       QVariant.String),
            ("cutoff_min", QVariant.Int),
            ("mode",       QVariant.String),
            ("date",       QVariant.String),
            ("time",       QVariant.String),
            ("direction",  QVariant.String),
        ]:
            out_fields.append(QgsField(fname, ftype))

        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        sink, dest_id = self.parameterAsSink(
            parameters, self.OUTPUT_ISOCHRONES, context,
            out_fields, QgsWkbTypes.MultiPolygon, wgs84,
        )
        if sink is None:
            raise QgsProcessingException(self.tr("Could not create output layer."))

        # ── OTP server lifecycle ──────────────────────────────────────────
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
                if port_is_listening(port):
                    raise QgsProcessingException(self.tr(
                        f"Port {port} is held by a non-OTP process. Pick a "
                        f"different OTP_PORT or stop the conflicting service."
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

            # ── Per-point loop ────────────────────────────────────────────
            isochrone_client = IsochroneClient(port=port, router=router_id)
            source = self.parameterAsSource(parameters, self.ORIGIN_POINTS, context)
            features = list(source.getFeatures())
            n_points = len(features)
            if n_points == 0:
                raise QgsProcessingException(self.tr("ORIGIN_POINTS layer has no features."))
            feedback.pushInfo(self.tr(f"Processing {n_points} origin point(s)…"))

            source_crs = source.sourceCrs()
            transform = (
                QgsCoordinateTransform(source_crs, wgs84, context.transformContext())
                if source_crs != wgs84
                else None
            )

            name_idx = source.fields().lookupField("name")
            if name_idx < 0:
                name_idx = source.fields().lookupField("nazwa")

            max_walk_distance = self.parameterAsInt(parameters, self.MAX_WALK_DISTANCE, context)
            walk_reluctance = self.parameterAsDouble(parameters, self.WALK_RELUCTANCE, context)
            wait_reluctance = self.parameterAsDouble(parameters, self.WAIT_RELUCTANCE, context)
            transfer_penalty = self.parameterAsInt(parameters, self.TRANSFER_PENALTY, context)
            min_transfer_time = self.parameterAsInt(parameters, self.MIN_TRANSFER_TIME, context)

            ok_count = 0
            failed_count = 0
            total_polygons = 0

            for i, feat in enumerate(features):
                if feedback.isCanceled():
                    break
                feedback.setProgress(int(100 * i / n_points))

                pt = feat.geometry().centroid().asPoint()
                if transform is not None:
                    pt = transform.transform(pt)
                from_lat, from_lon = pt.y(), pt.x()

                point_id = feat.id()
                name_val = (
                    str(feat.attribute(name_idx))
                    if name_idx >= 0 and feat.attribute(name_idx) is not None
                    else str(point_id)
                )

                feedback.pushInfo(self.tr(
                    f"[{i + 1}/{n_points}] point_id={point_id} name={name_val!r} "
                    f"lat={from_lat:.6f} lon={from_lon:.6f}"
                ))
                try:
                    geojson_str = isochrone_client.get_isochrone(
                        from_lat=from_lat,
                        from_lon=from_lon,
                        cutoffs_sec=cutoffs_sec,
                        mode=mode_str,
                        date_mmddyyyy=date_s,
                        time_hhmmss=time_s,
                        direction=direction_str,
                        arrive_by=direction_str == "TO",
                        max_walk_distance=max_walk_distance,
                        walk_reluctance=walk_reluctance,
                        wait_reluctance=wait_reluctance,
                        transfer_penalty=transfer_penalty,
                        min_transfer_time=min_transfer_time,
                    )
                    parsed = IsochroneClient.parse_isochrones(geojson_str)
                except OtpClientError as e:
                    feedback.pushWarning(self.tr(
                        f"Point {point_id} ({name_val!r}) failed: {e}. Skipping."
                    ))
                    failed_count += 1
                    continue

                for item in parsed:
                    cutoff_min = round(item["cutoff_sec"] / 60)
                    raw_geom = QgsJsonUtils.geometryFromGeoJson(json.dumps(item["geometry"]))
                    geom = _geom_to_multipolygon(raw_geom)
                    if geom is None or geom.isEmpty():
                        feedback.pushWarning(self.tr(
                            f"Point {point_id}: no polygon parts for cutoff {cutoff_min} min "
                            f"(raw type={QgsWkbTypes.displayString(raw_geom.wkbType()) if raw_geom else 'null'})."  # noqa: E501
                        ))
                        continue
                    out_feat = QgsFeature(out_fields)
                    out_feat.setGeometry(geom)
                    out_feat.setAttributes([
                        point_id,
                        name_val,
                        cutoff_min,
                        mode_str,
                        date_s,
                        time_s,
                        direction_str,
                    ])
                    if sink.addFeature(out_feat, QgsFeatureSink.FastInsert):
                        total_polygons += 1
                    else:
                        feedback.pushWarning(self.tr(
                            f"Point {point_id}: sink rejected cutoff {cutoff_min} min polygon "
                            f"(type={QgsWkbTypes.displayString(geom.wkbType())})."
                        ))

                ok_count += 1

            feedback.setProgress(100)
            feedback.pushInfo(self.tr(
                f"Done: {ok_count} points OK, {failed_count} failed, "
                f"{total_polygons} polygons written."
            ))

            if server_ctx is not None:
                server_ctx.__exit__(None, None, None)
                server_ctx = None

            return {self.OUTPUT_ISOCHRONES: dest_id}

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

        feedback.pushInfo(self.tr("--- OTP router diagnostic ---"))
        feedback.pushInfo(self.tr(
            f"hasTransit = {info.get('hasTransit')}; "
            f"transitServiceStarts = {_epoch_to_iso(info.get('transitServiceStarts'))}; "
            f"transitServiceEnds = {_epoch_to_iso(info.get('transitServiceEnds'))}"
        ))
        feedback.pushInfo(self.tr("-----------------------------"))

    def _warn_gtfs_date(self, gtfs_files: list, analysis_date, feedback) -> None:
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
                        "OTP may return all-unreachable isochrones for this date."
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
