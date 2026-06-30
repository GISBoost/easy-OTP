"""RunServiceCoverage — multi-point service coverage at a single snapshot time (N-3).

For each grid cell, counts how many of N service points (shops, hospitals,
parcel lockers, etc.) are reachable within the travel-time threshold at one
chosen moment of the day.  Automates the 'Żabka' analysis from slide 9 of the
GISday 2024 workshop.

Pipeline:
  1. For each service point, generate one OTP travel-time surface at a fixed time.
  2. Stack surfaces and count per raster cell: how many points have travel_time <= T.
  3. Aggregate count raster to the user's grid (hex / square / custom).
"""

from __future__ import annotations

from pathlib import Path

from qgis.PyQt.QtCore import QCoreApplication, QDate, QDateTime, QSettings, QTime
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsCoordinateTransformContext,
    QgsFeatureSink,
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
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterString,
    QgsProcessingParameterVectorLayer,
    QgsProcessingUtils,
)

from ..core.otp_client import OtpClient
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
from ..core.raster_processing import count_below_threshold
from ..core.surface_runner import SurfaceJobParams, run_surface_loop_over_points
from ..core.zonal import run_zonal_stats
from .generate_hex_grid import build_hex_grid, extent_of_count_nonzero


_MODE_OPTIONS = ["TRANSIT", "BUS", "RAIL", "TRAM", "SUBWAY", "WALK", "CAR", "BICYCLE"]
_TRANSIT_MODES = {"TRANSIT", "BUS", "RAIL", "TRAM", "SUBWAY"}
_AGG_OPTIONS = ["NONE", "GRID_HEX", "GRID_SQUARE", "EXISTING_LAYER"]
_AGG_STAT_OPTIONS = ["max", "mean", "sum"]

_AGG_NONE = 0
_AGG_HEX = 1
_AGG_SQUARE = 2
_AGG_EXISTING = 3


def _otp_mode_str(mode_str: str) -> str:
    if mode_str in _TRANSIT_MODES:
        return f"{mode_str},WALK"
    return mode_str


def _build_square_grid(
    extent: "QgsRectangle",  # type: ignore[name-defined]  # noqa: F821
    extent_crs: QgsCoordinateReferenceSystem,
    cell_size_m: float,
    context,
    feedback,
    buffer_m: float = 0.0,
):
    """Generate a square polygon grid covering *extent*. Output is EPSG:3857."""
    import processing  # noqa: PLC0415

    crs_3857 = QgsCoordinateReferenceSystem("EPSG:3857")
    transform = QgsCoordinateTransform(extent_crs, crs_3857, QgsCoordinateTransformContext())
    extent_3857 = transform.transformBoundingBox(extent)
    if buffer_m > 0:
        extent_3857.grow(buffer_m)

    extent_str = (
        f"{extent_3857.xMinimum()},{extent_3857.xMaximum()},"
        f"{extent_3857.yMinimum()},{extent_3857.yMaximum()} [EPSG:3857]"
    )
    result = processing.run(
        "native:creategrid",
        {
            "TYPE": 2,  # Rectangle (polygon)
            "EXTENT": extent_str,
            "HSPACING": cell_size_m,
            "VSPACING": cell_size_m,
            "HOVERLAY": 0,
            "VOVERLAY": 0,
            "CRS": crs_3857,
            "OUTPUT": "TEMPORARY_OUTPUT",
        },
        context=context,
        feedback=feedback,
        is_child_algorithm=True,
    )
    return context.getMapLayer(result["OUTPUT"])


class RunServiceCoverage(QgsProcessingAlgorithm):
    SERVICE_POINTS = "SERVICE_POINTS"
    OSM_PBF = "OSM_PBF"
    GTFS_FILES = "GTFS_FILES"
    MODE = "MODE"
    THRESHOLD_MIN = "THRESHOLD_MIN"
    ANALYSIS_DATE = "ANALYSIS_DATE"
    TIME = "TIME"
    AGGREGATION = "AGGREGATION"
    GRID_CELL_SIZE = "GRID_CELL_SIZE"
    AGG_LAYER = "AGG_LAYER"
    AGG_STAT = "AGG_STAT"

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
    KEEP_SERVER_ALIVE = "KEEP_SERVER_ALIVE"

    WORK_DIR = "WORK_DIR"
    OUTPUT_COUNT_RASTER = "OUTPUT_COUNT_RASTER"
    OUTPUT_GRID = "OUTPUT_GRID"

    def tr(self, string: str) -> str:
        return QCoreApplication.translate(type(self).__name__, string)

    def name(self) -> str:
        return "runservicecoverage"

    def displayName(self) -> str:  # noqa: N802
        return self.tr("Run service coverage")

    def group(self) -> str:
        return self.tr("3 · Analysis")

    def groupId(self) -> str:  # noqa: N802
        return "analysis"

    def createInstance(self):  # noqa: N802
        return RunServiceCoverage()

    def shortHelpString(self) -> str:  # noqa: N802
        return self.tr(
            "For each grid cell, counts how many service points (shops, hospitals, "
            "parcel lockers, etc.) are reachable within the travel-time threshold at "
            "one snapshot moment of the day (the 'Żabka' analysis).\n\n"
            "For each of N service points one travel-time surface is generated at the "
            "chosen time. Surfaces are stacked and counted per raster cell "
            "(count = how many points have travel-time ≤ threshold). The count "
            "raster is then aggregated to a hex/square grid or a user-supplied layer.\n\n"
            "One time only — for multiple times, run the algorithm separately. "
            "Analysis time is O(N points); a typical run with 20 points takes minutes.\n\n"
            "For non-transit modes (WALK/CAR/BICYCLE) GTFS is optional.\n"
            "Requires user-provided Java 8 and otp-1.5.0-shaded.jar."
        )

    def initAlgorithm(self, config=None):  # noqa: N802
        # --- Service points ---
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.SERVICE_POINTS,
                self.tr("Service points (point layer: shops, hospitals, parcel lockers, etc.)"),
                types=[QgsProcessing.TypeVectorPoint],
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
            QgsProcessingParameterNumber(
                self.THRESHOLD_MIN,
                self.tr("Travel-time threshold (min) — 'reachable within T minutes'"),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=10,
                minValue=1,
                maxValue=120,
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
                self.tr("Analysis time (single snapshot — one moment only)"),
                type=QgsProcessingParameterDateTime.Time,
                defaultValue=QTime(8, 0, 0),
            )
        )
        # --- Aggregation ---
        self.addParameter(
            QgsProcessingParameterEnum(
                self.AGGREGATION,
                self.tr("Aggregation grid"),
                options=_AGG_OPTIONS,
                defaultValue=_AGG_HEX,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.GRID_CELL_SIZE,
                self.tr("Grid cell size (m) — for GRID_HEX and GRID_SQUARE"),
                type=QgsProcessingParameterNumber.Double,
                defaultValue=500.0,
                minValue=1.0,
            )
        )
        _agg_layer_param = QgsProcessingParameterVectorLayer(
            self.AGG_LAYER,
            self.tr("Existing polygon layer (used when Aggregation = EXISTING_LAYER)"),
            types=[QgsProcessing.TypeVectorPolygon],
            optional=True,
        )
        _agg_layer_param.setFlags(
            _agg_layer_param.flags() | QgsProcessingParameterDefinition.FlagOptional
        )
        self.addParameter(_agg_layer_param)
        self.addParameter(
            QgsProcessingParameterEnum(
                self.AGG_STAT,
                self.tr(
                    "Aggregation statistic — max: most points reachable in cell; "
                    "mean: average reachable count; sum: total"
                ),
                options=_AGG_STAT_OPTIONS,
                defaultValue=0,
            )
        )
        # --- Outputs ---
        self.addParameter(
            QgsProcessingParameterRasterDestination(
                self.OUTPUT_COUNT_RASTER,
                self.tr("Output count raster (reachable_count, 0…N service points)"),
            )
        )
        _grid_param = QgsProcessingParameterFeatureSink(
            self.OUTPUT_GRID,
            self.tr("Output grid with reachable count (produced when Aggregation ≠ NONE)"),
            type=QgsProcessing.TypeVectorPolygon,
            optional=True,
            createByDefault=True,
        )
        _grid_param.setFlags(_grid_param.flags() | QgsProcessingParameterDefinition.FlagOptional)
        self.addParameter(_grid_param)
        # --- Working directory ---
        self.addParameter(
            QgsProcessingParameterFile(
                self.WORK_DIR,
                self.tr("Working directory (intermediate surfaces, graph, cache)"),
                behavior=QgsProcessingParameterFile.Folder,
            )
        )
        # --- Advanced routing ---
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

    def _add_advanced(self, param) -> None:
        param.setFlags(param.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(param)

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        self._output_grid_dest_id = None
        self._output_grid_stat_field = "reachable_max"

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

        # ── Mode / GTFS ──────────────────────────────────────────────────────
        mode_idx = self.parameterAsEnum(parameters, self.MODE, context)
        mode_str = _MODE_OPTIONS[mode_idx]
        is_transit = mode_str in _TRANSIT_MODES
        otp_mode = _otp_mode_str(mode_str)

        gtfs_files: list[Path] = []
        gtfs_dir_str = self.parameterAsFile(parameters, self.GTFS_FILES, context)
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

        # ── Service points ───────────────────────────────────────────────────
        source = self.parameterAsSource(parameters, self.SERVICE_POINTS, context)
        if source is None:
            raise QgsProcessingException(self.tr("SERVICE_POINTS layer could not be loaded."))
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        transform = QgsCoordinateTransform(
            source.sourceCrs(), wgs84, context.transformContext()
        )
        points: list[tuple[float, float]] = []
        for feat in source.getFeatures():
            geom = feat.geometry()
            geom.transform(transform)
            pt = geom.asPoint()
            points.append((pt.y(), pt.x()))  # (lat, lon) for OTP
        n_points = len(points)
        if n_points == 0:
            raise QgsProcessingException(self.tr("SERVICE_POINTS layer has no features."))
        feedback.pushInfo(self.tr("Loaded {} service point(s).").format(n_points))

        # ── Date / time ──────────────────────────────────────────────────────
        qdt_date = self.parameterAsDateTime(parameters, self.ANALYSIS_DATE, context)
        date_s = qdt_date.date().toString("MM-dd-yyyy")

        def _extract_time(key: str, fallback: QTime) -> QTime:
            raw = parameters.get(key)
            if isinstance(raw, QTime):
                return raw
            dt = self.parameterAsDateTime(parameters, key, context)
            t = dt.time()
            return t if t.isValid() else fallback

        time_t = _extract_time(self.TIME, QTime(8, 0, 0))
        if not time_t.isValid():
            raise QgsProcessingException(self.tr("Invalid TIME value."))
        time_hhmmss = "{:02d}:{:02d}:{:02d}".format(
            time_t.hour(), time_t.minute(), time_t.second()
        )
        feedback.pushInfo(self.tr(
            "Analysis snapshot: date={}, time={}, mode={}, threshold={} min"
        ).format(date_s, time_hhmmss, mode_str, self.parameterAsInt(parameters, self.THRESHOLD_MIN, context)))  # noqa: E501

        # ── Aggregation params ───────────────────────────────────────────────
        aggregation_idx = self.parameterAsEnum(parameters, self.AGGREGATION, context)
        agg_stat_idx = self.parameterAsEnum(parameters, self.AGG_STAT, context)
        agg_stat = _AGG_STAT_OPTIONS[agg_stat_idx]
        cell_size = self.parameterAsDouble(parameters, self.GRID_CELL_SIZE, context)
        threshold_min = self.parameterAsInt(parameters, self.THRESHOLD_MIN, context)

        # ── Output path ──────────────────────────────────────────────────────
        out_count_str = self.parameterAsOutputLayer(parameters, self.OUTPUT_COUNT_RASTER, context)
        if not out_count_str:
            raise QgsProcessingException(self.tr("Output count raster path is required."))
        out_count_path = Path(out_count_str)

        # ── Working directory / graph ────────────────────────────────────────
        work_dir_str = self.parameterAsFile(parameters, self.WORK_DIR, context)
        if not work_dir_str:
            raise QgsProcessingException(self.tr("Working directory is required."))
        work_dir = Path(work_dir_str)
        work_dir.mkdir(parents=True, exist_ok=True)

        port = self.parameterAsInt(parameters, self.OTP_PORT, context)
        xmx_build = self.parameterAsString(parameters, self.OTP_XMX_BUILD, context) or "2G"
        xmx_serve = self.parameterAsString(parameters, self.OTP_XMX_SERVE, context) or "4G"
        keep_alive = self.parameterAsBool(parameters, self.KEEP_SERVER_ALIVE, context)

        existing_graph_dir_str = self.parameterAsFile(parameters, self.EXISTING_GRAPH_DIR, context)
        if existing_graph_dir_str:
            existing_dir = Path(existing_graph_dir_str)
            if not (existing_dir / "Graph.obj").exists():
                raise QgsProcessingException(self.tr(
                    "EXISTING_GRAPH_DIR does not contain Graph.obj: {}. "
                    "Point to the router directory (e.g. …/graphs/abc123/)."
                ).format(existing_dir))
            router_id = existing_dir.name
            router_dir = existing_dir
            server_work_dir = existing_dir.parent.parent
            feedback.pushInfo(self.tr(
                "Using existing graph: {} (router_id={}); skipping build."
            ).format(router_dir, router_id))
            ensure_router_config(router_dir, Path(gtfs_dir_str) if gtfs_dir_str else None,
                                 feedback, config_file=None)
        else:
            server_work_dir = work_dir
            router_id = compute_router_id(pbf, gtfs_files)
            feedback.pushInfo(self.tr("Router ID: {}").format(router_id))
            router_dir = ensure_router_dir(work_dir, router_id, pbf, gtfs_files)
            ensure_router_config(router_dir, Path(gtfs_dir_str) if gtfs_dir_str else None,
                                 feedback, config_file=None)
            if graph_build_complete(work_dir, router_id):
                feedback.pushInfo(self.tr("Graph cache hit — skipping build."))
            else:
                _off_by_one = work_dir / router_id / "Graph.obj"
                if _off_by_one.exists():
                    raise QgsProcessingException(self.tr(
                        "Graph cache miss: expected {}.\n"
                        "However, a graph was found at {} — "
                        "WORK_DIR appears to point to the 'graphs' subfolder rather than its parent.\n"  # noqa: E501
                        "Fix option A: set WORK_DIR to '{}'.\n"
                        "Fix option B: set EXISTING_GRAPH_DIR to '{}'."
                    ).format(
                        work_dir / "graphs" / router_id,
                        _off_by_one.parent,
                        work_dir.parent,
                        _off_by_one.parent,
                    ))
                feedback.pushInfo(self.tr("Building OTP graph (this can take minutes)…"))
                try:
                    build_graph(java, jar, xmx_build, work_dir, router_id, feedback)
                except RuntimeError as e:
                    raise QgsProcessingException(str(e)) from e
                write_meta(router_dir, jar, [pbf, *gtfs_files])
        pointsets = ensure_pointsets_dir(work_dir)

        # ── OTP server lifecycle ─────────────────────────────────────────────
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
                        "Port {} is held by a non-OTP process. Pick a different "
                        "OTP_PORT or stop the conflicting service."
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

            # ── Surface loop over service points ─────────────────────────────
            date_slug = date_s.replace("-", "")
            time_slug = "{:02d}{:02d}".format(time_t.hour(), time_t.minute())
            surfaces_dir = (
                work_dir / "surfaces"
                / "{}_{}_{}_{}pts".format(router_id, date_slug, time_slug, n_points)
            )
            job = SurfaceJobParams(
                from_place_lat_lon=(0.0, 0.0),  # placeholder — overridden per point
                date_mmddyyyy=date_s,
                max_walk_distance=self.parameterAsInt(parameters, self.MAX_WALK_DISTANCE, context),
                walk_reluctance=self.parameterAsDouble(parameters, self.WALK_RELUCTANCE, context),
                wait_reluctance=self.parameterAsDouble(parameters, self.WAIT_RELUCTANCE, context),
                transfer_penalty=self.parameterAsInt(parameters, self.TRANSFER_PENALTY, context),
                min_transfer_time=self.parameterAsInt(parameters, self.MIN_TRANSFER_TIME, context),
                walk_speed=self.parameterAsDouble(parameters, self.WALK_SPEED, context),
                arrive_by=False,
            )
            feedback.pushInfo(self.tr(
                "Generating {} surface(s) at {} for date={}…"
            ).format(n_points, time_hhmmss, date_s))
            try:
                surfaces = run_surface_loop_over_points(
                    client=client,
                    points=points,
                    time_hhmmss=time_hhmmss,
                    job=job,
                    surfaces_dir=surfaces_dir,
                    feedback=feedback,
                    mode=otp_mode,
                )
            except QgsProcessingException:
                raise
            except Exception as e:
                raise QgsProcessingException(
                    self.tr("Surface generation failed: {}").format(e)
                ) from e

            feedback.pushInfo(self.tr(
                "Generated {} surface(s) in {}."
            ).format(len(surfaces), surfaces_dir))

            # ── Count raster ─────────────────────────────────────────────────
            feedback.pushInfo(self.tr(
                "Counting pixels reachable within {} min across {} surface(s)…"
            ).format(threshold_min, n_points))
            try:
                count_below_threshold(surfaces, threshold_min, out_count_path, feedback)
            except RuntimeError as e:
                raise QgsProcessingException(str(e)) from e
            feedback.pushInfo(self.tr(
                "Count raster written: {}"
            ).format(out_count_path))

            # ── Aggregation ──────────────────────────────────────────────────
            if aggregation_idx != _AGG_NONE:
                feedback.pushInfo(self.tr("Building aggregation grid ({})...").format(
                    _AGG_OPTIONS[aggregation_idx]
                ))

                if aggregation_idx in (_AGG_HEX, _AGG_SQUARE):
                    _extent_result = extent_of_count_nonzero(out_count_path)
                    if _extent_result is None:
                        raise QgsProcessingException(self.tr(
                            "No pixels were reachable within the threshold. "
                            "Check SERVICE_POINTS locations, THRESHOLD_MIN, and ANALYSIS_DATE/TIME."
                        ))
                    _extent, _extent_crs = _extent_result
                    if aggregation_idx == _AGG_HEX:
                        grid_layer = build_hex_grid(
                            _extent, _extent_crs, cell_size, context, feedback,
                            buffer_m=cell_size * 3,
                        )
                    else:
                        grid_layer = _build_square_grid(
                            _extent, _extent_crs, cell_size, context, feedback,
                            buffer_m=cell_size * 3,
                        )
                else:  # EXISTING_LAYER
                    grid_layer = self.parameterAsVectorLayer(
                        parameters, self.AGG_LAYER, context
                    )
                    if grid_layer is None:
                        raise QgsProcessingException(self.tr(
                            "AGG_LAYER is required when AGGREGATION = EXISTING_LAYER."
                        ))

                feedback.pushInfo(self.tr(
                    "Running zonal statistics (stat={}) on count raster…"
                ).format(agg_stat))
                try:
                    zonal_layer = run_zonal_stats(
                        out_count_path, grid_layer, context, feedback,
                        stat=agg_stat, prefix="reachable_",
                    )
                except (RuntimeError, ValueError) as e:
                    raise QgsProcessingException(str(e)) from e

                sink, dest_id = self.parameterAsSink(
                    parameters, self.OUTPUT_GRID, context,
                    zonal_layer.fields(),
                    zonal_layer.wkbType(),
                    zonal_layer.sourceCrs(),
                )
                if sink is not None:
                    for feat in zonal_layer.getFeatures():
                        if feedback.isCanceled():
                            raise QgsProcessingException(self.tr("Run cancelled by user."))
                        sink.addFeature(feat, QgsFeatureSink.FastInsert)
                    self._output_grid_dest_id = dest_id
                    self._output_grid_stat_field = "reachable_{}".format(agg_stat)

            # ── Summary ──────────────────────────────────────────────────────
            self._log_coverage_summary(out_count_path, n_points, feedback)

            feedback.pushInfo(self.tr(
                "Pipeline complete: {n} service points, threshold {t} min."
            ).format(n=n_points, t=threshold_min))

            if server_ctx is not None:
                server_ctx.__exit__(None, None, None)
                server_ctx = None

            results: dict = {self.OUTPUT_COUNT_RASTER: str(out_count_path)}
            if self._output_grid_dest_id:
                results[self.OUTPUT_GRID] = self._output_grid_dest_id
            return results

        except BaseException:
            if server_ctx is not None:
                server_ctx.__exit__(*self._exc_info())
            raise

    def postProcessAlgorithm(self, context, feedback):  # noqa: N802
        dest_id = getattr(self, "_output_grid_dest_id", None)
        stat_field = getattr(self, "_output_grid_stat_field", "reachable_max")
        if dest_id and stat_field == "reachable_max":
            layer = QgsProcessingUtils.mapLayerFromString(dest_id, context)
            if layer:
                qml_path = Path(__file__).parent.parent / "styles" / "service_coverage_count.qml"
                if qml_path.exists():
                    layer.loadNamedStyle(str(qml_path))
                    layer.triggerRepaint()
        return {}

    def _log_coverage_summary(self, count_path: Path, n_points: int, feedback) -> None:
        try:
            from osgeo import gdal  # noqa: PLC0415

            ds = gdal.Open(str(count_path))
            arr = ds.GetRasterBand(1).ReadAsArray()
            ds = None
            valid = arr > 0  # NoData=0 filtered out
            if valid.any():
                feedback.pushInfo(self.tr(
                    "=== Coverage summary ===\n"
                    "  max reachable points: {mx}/{n}\n"
                    "  mean reachable points (non-zero cells): {mn:.2f}\n"
                    "  cells with coverage: {cv} ({pct:.1f}% of raster extent)"
                ).format(
                    mx=int(arr[valid].max()),
                    n=n_points,
                    mn=float(arr[valid].mean()),
                    cv=int(valid.sum()),
                    pct=100.0 * valid.sum() / arr.size,
                ))
            else:
                feedback.pushWarning(self.tr(
                    "No cells had any service points reachable within threshold."
                ))
        except Exception as e:  # noqa: BLE001
            feedback.pushInfo(self.tr("Could not compute summary stats: {}").format(e))

    def _require_file(
        self, parameters, context, key: str, label: str, fix_hint: str = ""
    ) -> Path:
        raw = self.parameterAsFile(parameters, key, context)
        if not raw:
            raise QgsProcessingException(self.tr(
                "{} is required (parameter {}).{}"
            ).format(label, key, " " + fix_hint if fix_hint else ""))
        path = Path(raw)
        if not path.is_file():
            raise QgsProcessingException(self.tr(
                "{} not found at: {} (parameter {}).{}"
            ).format(label, path, key, " " + fix_hint if fix_hint else ""))
        return path

    @staticmethod
    def _exc_info():
        import sys
        return sys.exc_info()
