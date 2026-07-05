"""Realtime algorithm: GTFS-RT TripUpdates snapshot recorder (RT-2).

Polls a GTFS-RT TripUpdates feed at a fixed interval and saves each raw HTTP
response body as a ``.pb`` file.  The resulting archive can later be processed
by RT-3 (``BuildRealizedGtfs``) to reconstruct a "realized" static GTFS without
requiring a live session.

A 06:00–22:00 window at the default 60 s interval produces ~960 snapshots
(≈ 28 MB for a typical TripUpdates feed).  The effective interval is measured
from the end of each fetch, so the actual snapshot count may be slightly lower
than duration_min * 60 / interval_sec when individual fetches take >1 s.
"""

import time
from datetime import datetime
from pathlib import Path

from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingOutputFolder,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterFile,
    QgsProcessingParameterNumber,
    QgsProcessingParameterString,
)

from ..core.gtfsrt_config import validate_rt_url
from ..core.gtfsrt_recorder import (
    SnapshotFetchError,
    archive_folder_name,
    fetch_snapshot,
    snapshot_filename,
    snapshot_hash,
    write_manifest,
    write_snapshot,
)


class RecordGtfsRt(QgsProcessingAlgorithm):
    GTFS_RT_URL = "GTFS_RT_URL"
    FEED_ID = "FEED_ID"
    OUTPUT_DIR = "OUTPUT_DIR"
    DURATION_MIN = "DURATION_MIN"
    SAMPLING_INTERVAL_SEC = "SAMPLING_INTERVAL_SEC"
    FREEZE_WARNING_THRESHOLD = "FREEZE_WARNING_THRESHOLD"

    def tr(self, string: str) -> str:
        return QCoreApplication.translate(type(self).__name__, string)

    def name(self) -> str:
        return "recordgtfsrt"

    def displayName(self) -> str:  # noqa: N802 — Qt API name
        return self.tr("Record GTFS-RT snapshots")

    def group(self) -> str:
        return self.tr("4 · Realtime")

    def groupId(self) -> str:  # noqa: N802 — Qt API name
        return "realtime"

    def shortHelpString(self) -> str:  # noqa: N802 — Qt API name
        return self.tr(
            "Polls a GTFS-RT TripUpdates feed at regular intervals and saves each "
            "raw response as a .pb snapshot file.\n\n"
            "The output directory will contain one snapshot_YYYYmmdd-HHMMSS.pb file "
            "per successful poll plus a recording.json manifest.  Use the "
            "BuildRealizedGtfs (RT-3) algorithm to turn the archive into a modified "
            "static GTFS.\n\n"
            "A full service day (06:00–22:00) at 60 s interval yields ~960 "
            "snapshots (~28 MB for a typical TripUpdates feed).\n\n"
            "Recording duration is capped at 1500 minutes (25 h) per session — one "
            "service day plus margin for overnight trips crossing midnight. This keeps "
            "each archive tied to a single service day so BuildRealizedGtfs (RT-3) isn't "
            "fed a multi-day archive that would silently mix unrelated days.\n\n"
            "Frozen-feed detection:\n"
            "Some real-world GTFS-RT feeds occasionally stop updating without erroring "
            "— the HTTP poll still succeeds (HTTP 200) but returns byte-identical "
            "content. Poland's national rail aggregate feed (mkuran.pl) is documented "
            "to freeze like this for over an hour, once or twice a day. If "
            "FREEZE_WARNING_THRESHOLD (advanced, default 5) consecutive polls return "
            "identical bytes, a warning is logged once per frozen period; recording "
            "continues uninterrupted. The manifest's unchanged_streak_max field "
            "records the longest such run observed in the session, for later "
            "cross-reference against BuildRealizedGtfs's deduplication log.\n\n"
            "Only TripUpdates feeds are supported.  Cities with VehiclePositions-only "
            "feeds (e.g. Warsaw, Wrocław) cannot use this tool."
        )

    def createInstance(self):  # noqa: N802 — Qt API name
        return RecordGtfsRt()

    def _add_advanced(self, param) -> None:
        param.setFlags(param.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(param)

    def initAlgorithm(self, config=None) -> None:  # noqa: N802 — Qt API name
        self.addParameter(
            QgsProcessingParameterString(
                self.GTFS_RT_URL,
                self.tr("GTFS-RT TripUpdates URL"),
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.FEED_ID,
                self.tr("Feed ID (recorded in manifest only, not used to fetch)"),
                optional=True,
                defaultValue="",
            )
        )
        self.addParameter(
            QgsProcessingParameterFile(
                self.OUTPUT_DIR,
                self.tr("Output directory for snapshots"),
                behavior=QgsProcessingParameterFile.Folder,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.DURATION_MIN,
                self.tr("Recording duration (minutes)"),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=60,
                minValue=1,
                maxValue=1500,
            )
        )
        self._add_advanced(
            QgsProcessingParameterNumber(
                self.SAMPLING_INTERVAL_SEC,
                self.tr("Sampling interval (seconds, 15–600)"),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=60,
                minValue=15,
                maxValue=600,
            )
        )
        self._add_advanced(
            QgsProcessingParameterNumber(
                self.FREEZE_WARNING_THRESHOLD,
                self.tr(
                    "Warn after this many consecutive identical polls "
                    "(frozen-feed detection)"
                ),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=5,
                minValue=2,
            )
        )
        self.addOutput(
            QgsProcessingOutputFolder(
                self.OUTPUT_DIR,
                self.tr("Output directory"),
            )
        )

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802 — Qt API name
        url = self.parameterAsString(parameters, self.GTFS_RT_URL, context).strip()
        feed_id = self.parameterAsString(parameters, self.FEED_ID, context).strip()
        output_dir = Path(self.parameterAsFile(parameters, self.OUTPUT_DIR, context))
        duration_min = self.parameterAsInt(parameters, self.DURATION_MIN, context)
        interval_sec = self.parameterAsInt(parameters, self.SAMPLING_INTERVAL_SEC, context)
        freeze_threshold = self.parameterAsInt(
            parameters, self.FREEZE_WARNING_THRESHOLD, context
        )

        if not url:
            raise QgsProcessingException(self.tr("GTFS-RT URL is required."))

        feedback.pushInfo(self.tr(f"Validating feed URL: {url}"))
        ok, msg = validate_rt_url(url)
        if not ok:
            raise QgsProcessingException(self.tr(f"RT feed unreachable: {msg}"))

        started_at = datetime.now()
        output_dir = output_dir / archive_folder_name(feed_id, started_at)
        output_dir.mkdir(parents=True, exist_ok=True)
        feedback.pushInfo(self.tr(f"Archive folder: {output_dir.name}"))

        total_sec = duration_min * 60
        ok_count = 0
        failed_count = 0
        total_bytes = 0
        _cancelled = False
        prev_hash = None
        streak = 0
        unchanged_streak_max = 0
        warned_this_streak = False

        feedback.pushInfo(
            self.tr(
                f"Recording started. Duration: {duration_min} min, "
                f"interval: {interval_sec} s. Output: {output_dir}"
            )
        )

        try:
            start_mono = time.monotonic()

            while True:
                elapsed = time.monotonic() - start_mono
                if elapsed >= total_sec:
                    break
                if feedback.isCanceled():
                    _cancelled = True
                    feedback.pushInfo(self.tr("Recording cancelled by user."))
                    break

                now = datetime.now()
                try:
                    data = fetch_snapshot(url)
                    write_snapshot(output_dir, data, now)
                    ok_count += 1
                    total_bytes += len(data)
                    feedback.pushInfo(
                        self.tr(
                            f"[{ok_count}] {snapshot_filename(now)} ({len(data):,} B)"
                        )
                    )

                    h = snapshot_hash(data)
                    if prev_hash is None or h != prev_hash:
                        streak = 1
                        warned_this_streak = False
                    else:
                        streak += 1
                    prev_hash = h
                    unchanged_streak_max = max(unchanged_streak_max, streak)

                    if streak >= freeze_threshold and not warned_this_streak:
                        feedback.pushWarning(
                            self.tr(
                                f"Feed unchanged for {streak} consecutive polls "
                                f"(~{(streak - 1) * interval_sec} s) — possible frozen "
                                "upstream feed. Recording continues."
                            )
                        )
                        warned_this_streak = True
                except (SnapshotFetchError, OSError) as exc:
                    failed_count += 1
                    feedback.pushWarning(
                        self.tr(f"Poll {ok_count + failed_count} failed: {exc}")
                    )

                elapsed = time.monotonic() - start_mono
                feedback.setProgress(min(elapsed / total_sec * 100, 99))

                # Sleep in 1-second steps so Cancel is responsive.
                next_poll = time.monotonic() + interval_sec
                while True:
                    now_mono = time.monotonic()
                    if now_mono - start_mono >= total_sec:
                        break
                    if now_mono >= next_poll:
                        break
                    if feedback.isCanceled():
                        break
                    time.sleep(1)

        finally:
            stopped_at = datetime.now()
            write_manifest(
                output_dir,
                url,
                feed_id,
                interval_sec,
                started_at,
                stopped_at,
                ok_count,
                failed_count,
                total_bytes,
                unchanged_streak_max,
            )

        if _cancelled:
            size_kb = total_bytes / 1024
            feedback.pushInfo(
                self.tr(
                    f"Partial archive: {ok_count} snapshots, {failed_count} failed, "
                    f"{size_kb:.1f} KB. Manifest written to {output_dir / 'recording.json'}"
                )
            )
        else:
            feedback.setProgress(100)
            size_kb = total_bytes / 1024
            feedback.pushInfo(
                self.tr(
                    f"Recording finished: {ok_count} snapshots, {failed_count} failed, "
                    f"{size_kb:.1f} KB total. Manifest: {output_dir / 'recording.json'}"
                )
            )

        return {self.OUTPUT_DIR: str(output_dir)}
