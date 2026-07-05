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
    QgsProcessingParameterFile,
    QgsProcessingParameterString,
)

from ..core.dependencies import ensure_gtfsrt_bindings, install_gtfsrt_bindings
from ..core.gtfsrt_realizer import (
    aggregate_segments,
    check_snapshot_time_span,
    check_trip_overlap,
    collect_segment_times,
    load_static_indices,
    rebuild_stop_times,
    repackage_gtfs,
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
            "This algorithm assumes the archive covers a single service day. If the "
            "snapshot archive spans more than ~25 hours, a warning is logged (not "
            "blocking) noting that results may mix unrelated days.\n\n"
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
            "- Gaps (unobserved segments) fall back to scheduled travel time.\n"
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
                "The output will be produced but most segments will be uncorrected "
                "(gaps falling back to scheduled times)."
            ))

        span_sec = check_snapshot_time_span(snapshot_paths)
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
            segment_times, canceled_trip_ids = collect_segment_times(
                snapshot_paths,
                static_index,
                canceled_policy=canceled_policy,
                progress_cb=_progress,
                cancel_check=_cancel,
            )

            if feedback.isCanceled():
                return {self.OUTPUT_P50: "", self.OUTPUT_P85: ""}

            feedback.pushInfo(self.tr(
                f"Segments observed: {len(segment_times):,}  |  "
                f"CANCELED trips: {len(canceled_trip_ids)}"
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
                static_index, p50_stats, drop_trip_ids
            )
            feedback.setProgress(75)

            # --- Step 8b: rebuild stop times for P85 ---
            p85_corrections = None
            if write_p85:
                feedback.pushInfo(self.tr("Rebuilding stop_times for P85 feed…"))
                p85_corrections, p85_corrected, p85_gaps = rebuild_stop_times(
                    static_index, p85_stats, drop_trip_ids
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
