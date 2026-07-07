"""Realtime algorithm: Build a realized GTFS from RT-2 snapshot archive (RT-3).

Reads an RT-2 archive of GTFS-RT TripUpdates `.pb` snapshots and a matching
static GTFS `.zip`, aggregates observed stop-pair segment travel times across all
snapshots, and emits two modified static GTFS feeds:
  - <prefix>_p50.zip  — P50 (median) travel times: typical conditions
  - <prefix>_p85.zip  — P85 (85th-percentile) travel times: reliability bound

Method: Braga et al. (2023) segment-based aggregation.  Segments with no
observations fall back to scheduled travel times (gaps).

Requires google.protobuf + gtfs-realtime-bindings, which must be installed
before running this algorithm.  The plugin offers to install them at startup
(same dialog as openpyxl).  This is the only algorithm in easy-OTP that needs
this dependency.
"""

import os
from pathlib import Path

from qgis.PyQt.QtCore import (
    QCoreApplication,
    QMetaObject,
    QObject,
    Qt,
    pyqtSlot,
)
from qgis.PyQt.QtWidgets import QApplication, QMessageBox
from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingOutputString,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFile,
    QgsProcessingParameterString,
)

from ..core.dependencies import ensure_gtfsrt_bindings, install_gtfsrt_bindings
from ..core.gtfsrt_realizer import (
    aggregate_segments,
    check_snapshot_time_span,
    check_trip_overlap,
    collect_segment_times,
    deduplicate_snapshots,
    load_static_indices,
    rebuild_stop_times,
    repackage_gtfs,
    sample_feed_capabilities,
)

_MATCHING_MODE_OPTIONS = ["AUTO", "TRIP_ID", "ROUTE_STOP_FALLBACK"]

# AUTO-resolution thresholds — deliberately explicit, revisitable choices (see
# shortHelpString). 0.05 matches the pre-existing trip-id overlap warning threshold.
_TRIP_ID_OVERLAP_THRESHOLD = 0.05
_FALLBACK_CAPABILITY_THRESHOLD = 0.5


def resolve_matching_mode(overlap: float, capability: dict, requested_mode: str) -> str:
    """Resolve AUTO to a concrete matching mode; pass an explicit mode through unchanged.

    AUTO resolution: TRIP_ID if trip_id overlap >= 5%; else ROUTE_STOP_FALLBACK if the
    capability sample shows route_id_overlap, stop_id_presence_ratio, stop_id_overlap,
    and absolute_time_ratio ALL >= 50% — route_id_overlap must be checked too, since
    collect_segment_times' ROUTE_STOP_FALLBACK branch silently skips any TripUpdate
    whose route_id isn't in the static feed; without this check AUTO could select a
    mode that then drops most entities with no fail-fast signal, recreating the
    silent-empty-result problem this milestone exists to fix. Else raises ValueError
    naming both modes and the measured ratios — a fail-fast message.
    """
    if requested_mode != "AUTO":
        return requested_mode

    if overlap >= _TRIP_ID_OVERLAP_THRESHOLD:
        return "TRIP_ID"

    if (
        capability.get("route_id_overlap", 0.0) >= _FALLBACK_CAPABILITY_THRESHOLD
        and capability.get("stop_id_presence_ratio", 0.0) >= _FALLBACK_CAPABILITY_THRESHOLD
        and capability.get("stop_id_overlap", 0.0) >= _FALLBACK_CAPABILITY_THRESHOLD
        and capability.get("absolute_time_ratio", 0.0) >= _FALLBACK_CAPABILITY_THRESHOLD
    ):
        return "ROUTE_STOP_FALLBACK"

    raise ValueError(
        f"Neither TRIP_ID (trip_id overlap {overlap:.0%}) nor ROUTE_STOP_FALLBACK "
        f"(route_id overlap {capability.get('route_id_overlap', 0.0):.0%}, stop_id "
        f"presence {capability.get('stop_id_presence_ratio', 0.0):.0%}, stop_id overlap "
        f"{capability.get('stop_id_overlap', 0.0):.0%}, absolute time "
        f"{capability.get('absolute_time_ratio', 0.0):.0%}) is usable for this feed. "
        "See docs/reference/RT_test-feeds-by-city.md for known-working feeds."
    )


def _ask_on_main_thread(title: str, text: str) -> int:
    """Show QMessageBox.question on the GUI thread; safe to call from a background thread.

    processAlgorithm runs inside QgsProcessingAlgRunnerTask (a QgsTask, background thread).
    Qt forbids creating/showing widgets from non-main threads — doing so causes a crash.
    BlockingQueuedConnection posts the call to the main thread's event loop and blocks
    the calling thread until the slot returns, giving us the user's answer synchronously.
    """
    class _Asker(QObject):
        def __init__(self, title: str, text: str) -> None:
            super().__init__()
            self._title = title
            self._text = text
            self.reply = QMessageBox.No

        @pyqtSlot()
        def ask(self) -> None:
            self.reply = QMessageBox.question(
                None,
                self._title,
                self._text,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )

    asker = _Asker(title, text)
    asker.moveToThread(QApplication.instance().thread())
    QMetaObject.invokeMethod(asker, "ask", Qt.BlockingQueuedConnection)
    return asker.reply


class BuildRealizedGtfs(QgsProcessingAlgorithm):
    SNAPSHOT_DIR = "SNAPSHOT_DIR"
    STATIC_GTFS = "STATIC_GTFS"
    STATIC_GTFS_EXTRA = "STATIC_GTFS_EXTRA"
    OUTPUT_PREFIX = "OUTPUT_PREFIX"
    WRITE_P85 = "WRITE_P85"
    DEDUPLICATE_FROZEN_SNAPSHOTS = "DEDUPLICATE_FROZEN_SNAPSHOTS"
    RECONCILE_LAST_SNAPSHOT = "RECONCILE_LAST_SNAPSHOT"
    MATCHING_MODE = "MATCHING_MODE"
    OUTPUT_P50 = "OUTPUT_P50"
    OUTPUT_P85 = "OUTPUT_P85"

    def tr(self, string: str) -> str:
        return QCoreApplication.translate(type(self).__name__, string)

    def name(self) -> str:
        return "buildrealizedgtfs"

    def displayName(self) -> str:  # noqa: N802 — Qt API name
        return self.tr("Build realized GTFS from RT snapshots")

    def group(self) -> str:
        return self.tr("4 · Realtime")

    def groupId(self) -> str:  # noqa: N802 — Qt API name
        return "realtime"

    def shortHelpString(self) -> str:  # noqa: N802 — Qt API name
        return self.tr(
            "Reconstructs a 'realized timetable' from an RT-2 snapshot archive and a "
            "matching static GTFS feed.\n\n"
            "Outputs two modified GTFS .zip files:\n"
            "  <prefix>_p50.zip — P50 (median) segment travel times (typical conditions)\n"
            "  <prefix>_p85.zip — P85 (85th percentile) travel times (reliability / TTV)\n\n"
            "Run the standard RunTemporalAccessibility algorithm on each output to compare "
            "schedule-based, median-realized, and reliability-adjusted accessibility.\n\n"
            "Method: Braga et al. (2023) stop-pair segment aggregation across all "
            "trips and days. Segments with no observations keep their scheduled duration "
            "(gap). CANCELED trips are dropped from the output.\n\n"
            "IMPORTANT — same-day static required:\n"
            "If the archive's trip_ids embed the service date (e.g. Gdańsk), the static "
            "GTFS must be downloaded the same day as the archive. A wrong-day static will "
            "yield near-zero overlap and the output will be uncorrected. This requirement "
            "also applies to every file in STATIC_GTFS_EXTRA — all extra files must be "
            "the same service date as STATIC_GTFS and the RT archive.\n\n"
            "Multi-file static feeds (STATIC_GTFS_EXTRA):\n"
            "Some cities publish per-mode static feeds against one combined realtime feed "
            "— e.g. Kraków (ZTP) publishes GTFS_KRK_T.zip (tram) and GTFS_KRK_A.zip (bus) "
            "separately, but one combined TripUpdates.pb for both modes. Set STATIC_GTFS "
            "to one of the two (e.g. the tram feed) and STATIC_GTFS_EXTRA to a folder "
            "containing the other (e.g. the bus feed) so both modes' trip_ids are "
            "recognized when computing trip-id overlap and segment statistics. If a "
            "trip_id happens to appear in more than one file, the first file's "
            "(STATIC_GTFS's) version is kept and a warning is reported.\n\n"
            "Trip matching mode (MATCHING_MODE):\n"
            "By default (AUTO), trips are matched to the static feed by trip_id, exactly "
            "as before RT3-5. If trip_id overlap is below 5% but the archive's "
            "capability sample shows the feed carries usable route_id, stop_id, and "
            "absolute-time fields (route_id overlap, stop_id presence, stop_id overlap "
            "with the static feed, and absolute-time ratio ALL at least 50% — "
            "thresholds chosen as a reasonable starting point, revisit if a known-good "
            "feed narrowly misses them), AUTO falls back to matching by route_id + "
            "stop_id instead. This handles feeds (e.g. Poznań, Kraków) whose trip_id "
            "namespace is permanently disjoint from the static feed's — a different, "
            "larger problem than a wrong-day static feed. If neither join is usable, "
            "the algorithm fails fast with a message reporting the measured ratios, "
            "instead of silently producing an uncorrected result. ROUTE_STOP_FALLBACK "
            "matching cannot distinguish direction_id (direction is unknowable without "
            "a matched trip), only counts stop-time pairs with an absolute observed "
            "time (not a bare delay offset), and does not drop CANCELED trips from the "
            "output (canceled_trip_ids are collected via the RT-side trip_id, which by "
            "construction never matches a static trip_id in this mode). TRIP_ID and "
            "ROUTE_STOP_FALLBACK can also be forced explicitly (e.g. to compare both on "
            "the same archive); the four capability ratios are always logged, and a "
            "warning is shown if a forced mode looks unlikely to work well.\n\n"
            "This algorithm assumes the archive covers a single service day. If the "
            "snapshot archive spans more than ~25 hours, a warning is logged (not "
            "blocking) noting that results may mix unrelated days.\n\n"
            "Frozen-feed deduplication (DEDUPLICATE_FROZEN_SNAPSHOTS):\n"
            "When an upstream feed freezes mid-recording — e.g. Poland's national rail "
            "aggregate feed (mkuran.pl) is documented to stop updating for over an "
            "hour, once or twice a day — RecordGtfsRt (RT-2) still writes one "
            "identical snapshot file per poll throughout the freeze. Left uncorrected, "
            "that period would be counted once per snapshot in the P50/P85 pool, "
            "skewing the aggregate toward whatever travel time happened to be "
            "observed the instant the feed froze. When enabled (default: on), "
            "consecutive snapshots whose raw bytes exactly match the immediately "
            "preceding kept snapshot are dropped before aggregation, so a frozen "
            "period contributes at most once. This does not affect the archive "
            "time-span warning above, which is always computed from the full, "
            "undeduplicated snapshot list.\n\n"
            "Prediction reconciliation (RECONCILE_LAST_SNAPSHOT):\n"
            "Each TripUpdate snapshot re-predicts a trip's future stop times as the trip "
            "progresses, so the same trip-segment is observed repeatedly across "
            "snapshots — predictions made closer to the actual event (shorter lead time) "
            "are more accurate. When enabled (default: on), only the observation from "
            "the chronologically last snapshot with a complete pair of stop-time events "
            "is kept per (trip_id, from_stop_sequence, to_stop_sequence); earlier, less "
            "mature predictions for the same trip-segment are discarded before P50/P85 "
            "aggregation. This is an intentional behavior change: P50/P85 values may "
            "shift slightly compared to archives processed before this feature — this is "
            "not a regression. Disable it to restore pre-0.7 behavior (every snapshot's "
            "observation counted), which is useful for very short test recordings where "
            "reducing each trip-segment to a single observation would leave too few data "
            "points for a meaningful P85.\n\n"
            "Dependency:\n"
            "This algorithm requires google.protobuf and gtfs-realtime-bindings. "
            "If missing, the error message includes install instructions. "
            "This is the only easy-OTP feature that needs this dependency.\n\n"
            "Methodological limitations:\n"
            "- TripUpdates carry predicted times / delay offsets, not empirically recorded "
            "stop events (Wessel 2017, rt2gtfs 2026). The realized feed reflects predicted "
            "operations.\n"
            "- P50 ≈ typical conditions; P85 ≈ reliability bound (travel-time variability).\n"
            "- Aggregation is by stop-pair segment across all trips/days, not per trip_id.\n"
            "- With RECONCILE_LAST_SNAPSHOT enabled, each trip-segment contributes "
            "exactly one observation regardless of how many snapshots covered it; "
            "disabling it weights repeated/evolving predictions of the same "
            "trip-segment equally.\n"
            "- Gaps (unobserved segments) fall back to scheduled travel time.\n"
            "- ROUTE_STOP_FALLBACK matching (auto-selected or forced) loses "
            "direction_id distinction and requires an absolute observed time per "
            "stop event; pairs lacking either are skipped rather than counted.\n"
            "- In ROUTE_STOP_FALLBACK mode, CANCELED-trip dropping does not apply: "
            "canceled_trip_ids are collected using the RT-side trip_id, which by "
            "construction does not match any static trip_id in this mode, so CANCELED "
            "RT trips remain in the output feed (unlike TRIP_ID mode).\n"
            "- Output is a reproducible static GTFS feed; it is not a record of any "
            "single actual day.\n\n"
            "See docs/RT-3_realized-gtfs-notes.md for full methodology and references."
        )

    def createInstance(self):  # noqa: N802 — Qt API name
        return BuildRealizedGtfs()

    def initAlgorithm(self, config=None) -> None:  # noqa: N802 — Qt API name
        self.addParameter(
            QgsProcessingParameterFile(
                self.SNAPSHOT_DIR,
                self.tr("RT-2 snapshot archive directory"),
                behavior=QgsProcessingParameterFile.Folder,
            )
        )
        self.addParameter(
            QgsProcessingParameterFile(
                self.STATIC_GTFS,
                self.tr("Static GTFS feed (.zip, must match archive service date)"),
                extension="zip",
            )
        )
        self.addParameter(
            QgsProcessingParameterFile(
                self.STATIC_GTFS_EXTRA,
                self.tr(
                    "Additional static GTFS files (optional, folder of .zip files "
                    "matching the same service date — e.g. Kraków: put the bus feed "
                    "here if STATIC_GTFS above is the tram feed, or vice versa)"
                ),
                behavior=QgsProcessingParameterFile.Folder,
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.OUTPUT_PREFIX,
                self.tr(
                    "Output base name  "
                    "(saved next to the static GTFS as <name>_p50.zip / <name>_p85.zip)"
                ),
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.WRITE_P85,
                self.tr("Also write P85 (85th-percentile) realized feed"),
                defaultValue=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.DEDUPLICATE_FROZEN_SNAPSHOTS,
                self.tr(
                    "Deduplicate consecutive identical snapshots before aggregation "
                    "(collapses frozen-feed periods so they don't bias P50/P85)"
                ),
                defaultValue=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.RECONCILE_LAST_SNAPSHOT,
                self.tr(
                    "Keep only the latest-snapshot observation per trip-segment "
                    "(reduces repeated/evolving RT predictions to one "
                    "lead-time-accurate value)"
                ),
                defaultValue=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.MATCHING_MODE,
                self.tr(
                    "Trip matching mode (AUTO: use TRIP_ID if trip_id overlap >= 5%, "
                    "else ROUTE_STOP_FALLBACK if the feed supports it, else fail)"
                ),
                options=_MATCHING_MODE_OPTIONS,
                defaultValue=0,
            )
        )
        self.addOutput(
            QgsProcessingOutputString(
                self.OUTPUT_P50,
                self.tr("P50 realized GTFS path"),
            )
        )
        self.addOutput(
            QgsProcessingOutputString(
                self.OUTPUT_P85,
                self.tr("P85 realized GTFS path (empty if WRITE_P85 is False)"),
            )
        )

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802 — Qt API name
        # --- Step 1: dependency check — offer auto-install on first use ---
        if not ensure_gtfsrt_bindings():
            reply = _ask_on_main_thread(
                self.tr("easy-OTP: missing dependency"),
                self.tr(
                    "google.protobuf / gtfs-realtime-bindings is not installed.\n\n"
                    "It is required by Build Realized GTFS (RT-3) only.\n\n"
                    "Install it now? (downloads wheels via urllib; "
                    "requires internet access)\n\n"
                    "Choosing 'No' will stop the algorithm."
                ),
            )
            if reply != QMessageBox.Yes:
                raise QgsProcessingException(
                    self.tr("Dependency not installed — algorithm cancelled.")
                )
            feedback.pushInfo(self.tr(
                "Installing google.protobuf + gtfs-realtime-bindings…"
            ))
            success, msg = install_gtfsrt_bindings()
            if not success:
                raise QgsProcessingException(
                    self.tr(
                        "Auto-install failed:\n\n%1\n\n"
                        "Install manually from the OSGeo4W Shell:\n\n"
                        "    python -m pip install protobuf==3.20.3 "
                        "gtfs-realtime-bindings==1.0.0\n\n"
                        "Then restart QGIS."
                    ).replace("%1", msg)
                )
            feedback.pushInfo(self.tr("Installed successfully."))

        # --- Step 2: read parameters ---
        snapshot_dir = Path(
            self.parameterAsFile(parameters, self.SNAPSHOT_DIR, context)
        )
        static_gtfs = self.parameterAsFile(parameters, self.STATIC_GTFS, context)
        static_gtfs_extra = self.parameterAsFile(
            parameters, self.STATIC_GTFS_EXTRA, context
        )
        output_prefix = self.parameterAsString(
            parameters, self.OUTPUT_PREFIX, context
        ).strip()
        write_p85 = self.parameterAsBool(parameters, self.WRITE_P85, context)
        deduplicate_frozen = self.parameterAsBool(
            parameters, self.DEDUPLICATE_FROZEN_SNAPSHOTS, context
        )
        reconcile_last_snapshot = self.parameterAsBool(
            parameters, self.RECONCILE_LAST_SNAPSHOT, context
        )
        matching_mode_idx = self.parameterAsEnum(parameters, self.MATCHING_MODE, context)
        requested_matching_mode = _MATCHING_MODE_OPTIONS[matching_mode_idx]
        canceled_policy = "skip"

        if not output_prefix:
            raise QgsProcessingException(self.tr("Output base name is required."))

        output_dir = Path(static_gtfs).parent
        p50_path = str(output_dir / (output_prefix + "_p50.zip"))
        p85_path = str(output_dir / (output_prefix + "_p85.zip")) if write_p85 else ""

        # --- Step 3: collect snapshot files ---
        snapshot_paths = sorted(snapshot_dir.glob("snapshot_*.pb"))
        if not snapshot_paths:
            raise QgsProcessingException(self.tr(
                f"No snapshot_*.pb files found in: {snapshot_dir}\n"
                "Make sure this is an RT-2 archive directory produced by "
                "RecordGtfsRt."
            ))

        feedback.pushInfo(self.tr(
            f"Found {len(snapshot_paths)} snapshot(s) in {snapshot_dir.name}"
        ))
        feedback.setProgress(2)

        # --- Step 3b: deduplicate frozen-feed runs (before static load / pre-flight) ---
        raw_snapshot_paths = snapshot_paths  # undeduplicated, for the time-span check below
        if deduplicate_frozen:
            snapshot_paths, dropped_count = deduplicate_snapshots(snapshot_paths)
            feedback.pushInfo(self.tr(
                f"Deduplication: dropped {dropped_count} snapshot(s) identical to the "
                f"immediately preceding kept snapshot ({len(snapshot_paths)} of "
                f"{len(raw_snapshot_paths)} retained)."
            ))

        # --- Step 4: load static index (primary + optional extra files) ---
        static_paths = [static_gtfs]
        if static_gtfs_extra:
            extra_zips = sorted(Path(static_gtfs_extra).glob("*.zip"))
            static_paths.extend(str(p) for p in extra_zips)

        if len(static_paths) > 1:
            feedback.pushInfo(self.tr(
                f"Loading static GTFS: {static_gtfs} + {len(static_paths) - 1} extra "
                f"file(s) from {static_gtfs_extra}"
            ))
        else:
            feedback.pushInfo(self.tr(f"Loading static GTFS: {static_gtfs}"))

        try:
            static_index, collision_count = load_static_indices(static_paths)
        except Exception as exc:
            raise QgsProcessingException(
                self.tr(f"Failed to read static GTFS: {exc}")
            ) from exc

        if collision_count:
            feedback.pushWarning(self.tr(
                f"{collision_count} trip_id(s) appeared in more than one static "
                "file; for each, the version from whichever file listed earliest "
                f"({static_gtfs}, then STATIC_GTFS_EXTRA files in alphabetical "
                "order) was kept."
            ))

        feedback.pushInfo(self.tr(
            f"Static index loaded: {len(static_index.all_trip_ids):,} trips, "
            f"{len(static_index.stop_map):,} stop-time entries"
        ))
        feedback.setProgress(5)

        # --- Step 5: pre-flight overlap check ---
        # Uses the (deduplicated) snapshot_paths, not raw_snapshot_paths: sampling the
        # first 5 kept snapshots is more representative than sampling 5 copies of the
        # same instant if the archive happens to open on a frozen period.
        feedback.pushInfo(self.tr("Checking trip_id overlap (archive vs static)…"))
        try:
            overlap = check_trip_overlap(snapshot_paths, static_index)
        except Exception as exc:  # noqa: BLE001
            overlap = 0.0
            feedback.pushWarning(self.tr(f"Overlap check failed: {exc}"))

        feedback.pushInfo(self.tr(
            f"Trip-id overlap: {overlap:.0%} "
            f"({'OK' if overlap >= 0.05 else 'LOW — see warning below'})"
        ))
        if overlap < 0.05:
            feedback.pushWarning(self.tr(
                f"Only {overlap:.0%} of TripUpdate trip_ids are present in the "
                "static feed. Likely causes:\n"
                "  • The static GTFS is from a different service date than the archive "
                "(feeds whose trip_ids embed the date, e.g. Gdańsk).\n"
                "  • The static GTFS is from a different city or agency.\n"
                "  • The feed's trip_id namespace is permanently disjoint from the "
                "static feed's (e.g. Poznań, Kraków) — see the capability sample "
                "below for whether ROUTE_STOP_FALLBACK matching can be used instead."
            ))

        feedback.pushInfo(self.tr(
            "Sampling feed capabilities (route_id / stop_id / absolute-time support)…"
        ))
        try:
            capability = sample_feed_capabilities(snapshot_paths, static_index)
        except Exception as exc:  # noqa: BLE001
            capability = {}
            feedback.pushWarning(self.tr(f"Capability sample failed: {exc}"))

        feedback.pushInfo(self.tr(
            "Capability sample — route_id overlap: {0:.0%}  |  stop_id presence: "
            "{1:.0%}  |  stop_id overlap: {2:.0%}  |  absolute time: {3:.0%}"
        ).format(
            capability.get("route_id_overlap", 0.0),
            capability.get("stop_id_presence_ratio", 0.0),
            capability.get("stop_id_overlap", 0.0),
            capability.get("absolute_time_ratio", 0.0),
        ))

        try:
            matching_mode = resolve_matching_mode(overlap, capability, requested_matching_mode)
        except ValueError as exc:
            raise QgsProcessingException(self.tr(str(exc))) from exc

        feedback.pushInfo(self.tr(f"Matching mode: {matching_mode}"))
        if matching_mode == "ROUTE_STOP_FALLBACK":
            feedback.pushWarning(self.tr(
                "ROUTE_STOP_FALLBACK matching is in use: segments are joined on "
                "route_id + stop_id instead of trip_id, so direction_id cannot be "
                "distinguished (an intentional, documented limitation), and only "
                "stop-time pairs with absolute observed times are counted."
            ))
        elif requested_matching_mode != "AUTO":
            # User forced a mode — don't block it, but warn if the sample suggests it
            # is unlikely to produce usable segments.
            if matching_mode == "TRIP_ID" and overlap < _TRIP_ID_OVERLAP_THRESHOLD:
                feedback.pushWarning(self.tr(
                    f"TRIP_ID matching was forced, but trip_id overlap is only "
                    f"{overlap:.0%} — most segments will likely be uncorrected."
                ))
            elif matching_mode == "ROUTE_STOP_FALLBACK" and not (
                capability.get("route_id_overlap", 0.0) >= _FALLBACK_CAPABILITY_THRESHOLD
                and capability.get("stop_id_presence_ratio", 0.0) >= _FALLBACK_CAPABILITY_THRESHOLD
                and capability.get("stop_id_overlap", 0.0) >= _FALLBACK_CAPABILITY_THRESHOLD
                and capability.get("absolute_time_ratio", 0.0) >= _FALLBACK_CAPABILITY_THRESHOLD
            ):
                feedback.pushWarning(self.tr(
                    "ROUTE_STOP_FALLBACK matching was forced, but the capability "
                    "sample suggests this feed may not support it well — most "
                    "segments will likely be skipped."
                ))

        span_sec = check_snapshot_time_span(raw_snapshot_paths)
        if span_sec > 25 * 3600:
            feedback.pushWarning(self.tr(
                f"Archive spans more than one service day ({span_sec / 3600:.1f}h) — "
                "results may mix unrelated days."
            ))

        if feedback.isCanceled():
            return {self.OUTPUT_P50: "", self.OUTPUT_P85: ""}

        # --- Step 6: collect segment times from snapshots ---
        feedback.pushInfo(self.tr(
            f"Parsing {len(snapshot_paths)} snapshot(s)…"
        ))

        def _progress(fraction: float) -> None:
            feedback.setProgress(5 + int(fraction * 60))

        def _cancel() -> bool:
            return feedback.isCanceled()

        partial_outputs: list[str] = []
        _completed = False
        try:
            segment_times, canceled_trip_ids, fallback_time_skipped = collect_segment_times(
                snapshot_paths,
                static_index,
                canceled_policy=canceled_policy,
                progress_cb=_progress,
                cancel_check=_cancel,
                reconcile_last_snapshot=reconcile_last_snapshot,
                matching_mode=matching_mode,
            )

            if feedback.isCanceled():
                return {self.OUTPUT_P50: "", self.OUTPUT_P85: ""}

            feedback.pushInfo(self.tr(
                f"Segments observed: {len(segment_times):,}  |  "
                f"CANCELED trips: {len(canceled_trip_ids)}  |  "
                f"skipped (no absolute time): {fallback_time_skipped:,}"
            ))
            feedback.setProgress(65)

            # --- Step 7: aggregate P50 + P85 ---
            feedback.pushInfo(self.tr("Aggregating segment statistics (P50, P85)…"))
            p50_stats, p85_stats = aggregate_segments(segment_times)
            feedback.setProgress(70)

            drop_trip_ids = frozenset(canceled_trip_ids)

            # --- Step 8a: rebuild stop times for P50 ---
            feedback.pushInfo(self.tr("Rebuilding stop_times for P50 feed…"))
            p50_corrections, p50_corrected, p50_gaps = rebuild_stop_times(
                static_index, p50_stats, drop_trip_ids, matching_mode=matching_mode
            )
            feedback.setProgress(75)

            # --- Step 8b: rebuild stop times for P85 ---
            p85_corrections = None
            if write_p85:
                feedback.pushInfo(self.tr("Rebuilding stop_times for P85 feed…"))
                p85_corrections, p85_corrected, p85_gaps = rebuild_stop_times(
                    static_index, p85_stats, drop_trip_ids, matching_mode=matching_mode
                )
            feedback.setProgress(80)

            # --- Step 9a: write P50 zip ---
            feedback.pushInfo(self.tr("Writing P50 feed → ") + p50_path)
            partial_outputs.append(p50_path)
            repackage_gtfs(static_gtfs, p50_path, p50_corrections, drop_trip_ids)
            partial_outputs.remove(p50_path)  # P50 complete — safe to keep on cancel
            feedback.setProgress(90)

            # --- Step 9b: write P85 zip ---
            if write_p85 and p85_corrections is not None:
                if feedback.isCanceled():
                    return {self.OUTPUT_P50: p50_path, self.OUTPUT_P85: ""}
                feedback.pushInfo(self.tr("Writing P85 feed → ") + p85_path)
                partial_outputs.append(p85_path)
                repackage_gtfs(static_gtfs, p85_path, p85_corrections, drop_trip_ids)
                partial_outputs.remove(p85_path)  # P85 complete

            feedback.setProgress(100)
            _completed = True

            # --- Step 10: summary ---
            total_trips = len(static_index.trip_stops) - len(drop_trip_ids)
            p85_stats_line = (
                self.tr("  P85 corrected    : {0}  |  gaps: {1}\n").format(
                    f"{p85_corrected:,}", f"{p85_gaps:,}"
                )
                if write_p85 and p85_corrections is not None else ""
            )
            feedback.pushInfo(
                self.tr(
                    "\nDone.\n"
                    "  Snapshots parsed : {0}\n"
                    "  Segments observed: {1}\n"
                    "  P50 corrected    : {2}  |  gaps: {3}\n"
                ).format(
                    len(snapshot_paths),
                    f"{len(segment_times):,}",
                    f"{p50_corrected:,}",
                    f"{p50_gaps:,}",
                )
                + p85_stats_line
                + self.tr(
                    "  Trips dropped    : {0} (CANCELED, policy=skip)\n"
                    "  Trips in output  : {1}\n"
                    "  P50 feed         : {2}\n"
                ).format(len(drop_trip_ids), f"{total_trips:,}", p50_path)
                + (self.tr("  P85 feed         : {0}\n").format(p85_path) if write_p85 else "")
            )

            return {self.OUTPUT_P50: p50_path, self.OUTPUT_P85: p85_path}

        except QgsProcessingException:
            raise
        except Exception as exc:
            raise QgsProcessingException(
                self.tr(f"BuildRealizedGtfs failed: {exc}")
            ) from exc
        finally:
            if not _completed:
                for path in partial_outputs:
                    try:
                        if os.path.exists(path):
                            os.remove(path)
                    except OSError:
                        pass
