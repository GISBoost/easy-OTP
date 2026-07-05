"""Unit tests for easy_otp.core.gtfsrt_recorder (RT-2 helpers).

No QGIS, no network — pure stdlib + pytest.
Run: py -m pytest easy_otp/test/test_record_gtfs_rt.py -v
"""

import hashlib
import json
import re
import urllib.error
from datetime import datetime
from io import BytesIO
from unittest import mock

import pytest

from easy_otp.core.gtfsrt_recorder import (
    SnapshotFetchError,
    archive_folder_name,
    fetch_snapshot,
    snapshot_filename,
    snapshot_hash,
    write_manifest,
    write_snapshot,
)

_SAMPLE_URL = "http://example.com/gtfs-rt.pb"
_SAMPLE_BYTES = b"\x0a\x06\x08\x01\x10\x02"  # minimal protobuf-ish bytes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeResponse:
    """Minimal file-like object for mocking urlopen."""

    def __init__(self, body: bytes, status: int = 200):
        self._body = BytesIO(body)
        self.status = status

    def read(self) -> bytes:
        return self._body.read()

    def getcode(self) -> int:
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


# ---------------------------------------------------------------------------
# fetch_snapshot
# ---------------------------------------------------------------------------

def test_fetch_snapshot_success():
    resp = _FakeResponse(_SAMPLE_BYTES, 200)
    with mock.patch("urllib.request.urlopen", return_value=resp):
        result = fetch_snapshot(_SAMPLE_URL)
    assert result == _SAMPLE_BYTES


def test_fetch_snapshot_non_200():
    resp = _FakeResponse(b"Service Unavailable", 503)
    with mock.patch("urllib.request.urlopen", return_value=resp):
        with pytest.raises(SnapshotFetchError, match="HTTP 503"):
            fetch_snapshot(_SAMPLE_URL)


def test_fetch_snapshot_http_error():
    exc = urllib.error.HTTPError(_SAMPLE_URL, 404, "Not Found", {}, None)
    with mock.patch("urllib.request.urlopen", side_effect=exc):
        with pytest.raises(SnapshotFetchError, match="HTTP 404"):
            fetch_snapshot(_SAMPLE_URL)


def test_fetch_snapshot_url_error():
    exc = urllib.error.URLError("timed out")
    with mock.patch("urllib.request.urlopen", side_effect=exc):
        with pytest.raises(SnapshotFetchError, match="Connection failed"):
            fetch_snapshot(_SAMPLE_URL)


def test_fetch_snapshot_generic_error():
    with mock.patch("urllib.request.urlopen", side_effect=OSError("socket error")):
        with pytest.raises(SnapshotFetchError, match="Request failed"):
            fetch_snapshot(_SAMPLE_URL)


# ---------------------------------------------------------------------------
# snapshot_filename
# ---------------------------------------------------------------------------

def test_snapshot_filename_format():
    dt = datetime(2026, 6, 21, 8, 30, 15)
    name = snapshot_filename(dt)
    assert name == "snapshot_20260621-083015.pb"
    assert re.match(r"^snapshot_\d{8}-\d{6}\.pb$", name)


def test_snapshot_filename_unique():
    dt1 = datetime(2026, 6, 21, 8, 30, 0)
    dt2 = datetime(2026, 6, 21, 8, 30, 1)
    assert snapshot_filename(dt1) != snapshot_filename(dt2)


def test_snapshot_filename_midnight_boundary():
    dt1 = datetime(2026, 6, 21, 23, 59, 59)
    dt2 = datetime(2026, 6, 22, 0, 0, 0)
    # Date is included — cross-midnight filenames never collide.
    assert snapshot_filename(dt1) != snapshot_filename(dt2)


# ---------------------------------------------------------------------------
# snapshot_hash
# ---------------------------------------------------------------------------

def test_snapshot_hash_deterministic():
    assert snapshot_hash(_SAMPLE_BYTES) == snapshot_hash(_SAMPLE_BYTES)


def test_snapshot_hash_differs_for_different_bytes():
    assert snapshot_hash(b"AAAA") != snapshot_hash(b"BBBB")


def test_snapshot_hash_matches_hashlib():
    assert snapshot_hash(_SAMPLE_BYTES) == hashlib.sha256(_SAMPLE_BYTES).hexdigest()


# ---------------------------------------------------------------------------
# write_snapshot
# ---------------------------------------------------------------------------

def test_write_snapshot_roundtrip(tmp_path):
    dt = datetime(2026, 6, 21, 10, 0, 0)
    path = write_snapshot(tmp_path, _SAMPLE_BYTES, dt)
    assert path.exists()
    assert path.read_bytes() == _SAMPLE_BYTES
    assert path.name == snapshot_filename(dt)


def test_write_snapshot_returns_path(tmp_path):
    dt = datetime(2026, 6, 21, 10, 0, 5)
    path = write_snapshot(tmp_path, b"\x00\xff", dt)
    assert path == tmp_path / snapshot_filename(dt)


# ---------------------------------------------------------------------------
# write_manifest
# ---------------------------------------------------------------------------

def test_write_manifest_keys(tmp_path):
    started = datetime(2026, 6, 21, 6, 0, 0)
    stopped = datetime(2026, 6, 21, 7, 0, 0)
    write_manifest(
        tmp_path,
        url=_SAMPLE_URL,
        feed_id="gdansk",
        interval=60,
        started_at=started,
        stopped_at=stopped,
        snapshot_count=59,
        failed_count=1,
        total_bytes=1_800_000,
        unchanged_streak_max=3,
    )
    manifest_path = tmp_path / "recording.json"
    assert manifest_path.exists()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert data["url"] == _SAMPLE_URL
    assert data["feed_id"] == "gdansk"
    assert data["sampling_interval_sec"] == 60
    assert data["started_at"] == started.isoformat()
    assert data["stopped_at"] == stopped.isoformat()
    assert data["snapshot_count"] == 59
    assert data["failed_count"] == 1
    assert data["total_bytes"] == 1_800_000
    assert data["unchanged_streak_max"] == 3


def test_write_manifest_unchanged_streak_max_zero(tmp_path):
    started = datetime(2026, 6, 21, 6, 0, 0)
    stopped = datetime(2026, 6, 21, 6, 0, 30)
    write_manifest(
        tmp_path,
        url=_SAMPLE_URL,
        feed_id="",
        interval=60,
        started_at=started,
        stopped_at=stopped,
        snapshot_count=0,
        failed_count=1,
        total_bytes=0,
        unchanged_streak_max=0,
    )
    data = json.loads((tmp_path / "recording.json").read_text(encoding="utf-8"))
    assert data["unchanged_streak_max"] == 0


# ---------------------------------------------------------------------------
# _archive_folder_name
# ---------------------------------------------------------------------------

def test_archive_folder_name_with_feed_id():
    dt = datetime(2026, 6, 21, 18, 30, 4)
    name = archive_folder_name("gdansk", dt)
    assert name == "gtfsrt_gdansk_20260621-183004"


def test_archive_folder_name_no_feed_id():
    dt = datetime(2026, 6, 21, 18, 30, 4)
    name = archive_folder_name("", dt)
    assert name == "gtfsrt_recording_20260621-183004"


def test_archive_folder_name_sanitizes_special_chars():
    dt = datetime(2026, 6, 21, 8, 0, 0)
    name = archive_folder_name("Poznań ZTM / bus", dt)
    assert name == "gtfsrt_Pozna__ZTM___bus_20260621-080000"
    assert "/" not in name
    assert " " not in name


def test_archive_folder_name_unique_per_second():
    dt1 = datetime(2026, 6, 21, 8, 0, 0)
    dt2 = datetime(2026, 6, 21, 8, 0, 1)
    assert archive_folder_name("", dt1) != archive_folder_name("", dt2)


def test_write_manifest_overwrites(tmp_path):
    """A second call must overwrite, not append."""
    started = datetime(2026, 6, 21, 6, 0, 0)
    stopped = datetime(2026, 6, 21, 6, 30, 0)
    for count, streak in ((5, 1), (30, 4)):
        write_manifest(
            tmp_path, _SAMPLE_URL, "", 60, started, stopped, count, 0, 0, streak
        )
    data = json.loads((tmp_path / "recording.json").read_text(encoding="utf-8"))
    assert data["snapshot_count"] == 30
    assert data["unchanged_streak_max"] == 4


# ---------------------------------------------------------------------------
# RecordGtfsRt polling-loop streak wiring (RT3-3)
#
# Replicates only the hash/streak/warn-once fragment of
# RecordGtfsRt.processAlgorithm's success branch — same pattern used by
# test_run_travel_time_matrix.py / test_run_origin_destination_times.py to
# test loop wiring without a QGIS environment.
# ---------------------------------------------------------------------------

def _run_streak_loop(payloads: list[bytes], freeze_threshold: int):
    """Replicate record_gtfsrt.py's per-poll streak/warn-once bookkeeping.

    Returns (warnings, unchanged_streak_max), where warnings is the list of
    streak values at which a warning fired.
    """
    prev_hash = None
    streak = 0
    unchanged_streak_max = 0
    warned_this_streak = False
    warnings = []

    for data in payloads:
        h = snapshot_hash(data)
        if prev_hash is None or h != prev_hash:
            streak = 1
            warned_this_streak = False
        else:
            streak += 1
        prev_hash = h
        unchanged_streak_max = max(unchanged_streak_max, streak)

        if streak >= freeze_threshold and not warned_this_streak:
            warnings.append(streak)
            warned_this_streak = True

    return warnings, unchanged_streak_max


def test_streak_loop_no_repeats_never_warns():
    payloads = [b"A", b"B", b"C", b"D"]
    warnings, streak_max = _run_streak_loop(payloads, freeze_threshold=5)
    assert warnings == []
    assert streak_max == 1


def test_streak_loop_warns_once_at_threshold():
    # 5 identical polls in a row, then a change.
    payloads = [b"A", b"A", b"A", b"A", b"A", b"B"]
    warnings, streak_max = _run_streak_loop(payloads, freeze_threshold=5)
    assert warnings == [5]
    assert streak_max == 5


def test_streak_loop_does_not_repeat_warning_within_same_streak():
    # 8 identical polls in a row — must warn exactly once, at poll 5, not again at 6/7/8.
    payloads = [b"A"] * 8
    warnings, streak_max = _run_streak_loop(payloads, freeze_threshold=5)
    assert warnings == [5]
    assert streak_max == 8


def test_streak_loop_rewarns_after_reset_and_recross():
    # Freeze for 5, change, freeze for 5 again — warns once per frozen period.
    payloads = [b"A"] * 5 + [b"B"] + [b"C"] * 5
    warnings, streak_max = _run_streak_loop(payloads, freeze_threshold=5)
    assert warnings == [5, 5]
    assert streak_max == 5


def test_streak_loop_max_matches_longest_run_in_script():
    payloads = [b"A", b"A", b"B", b"B", b"B", b"B", b"C"]
    warnings, streak_max = _run_streak_loop(payloads, freeze_threshold=3)
    assert warnings == [3]  # only the B-run (length 4) crosses threshold=3
    assert streak_max == 4  # longest run overall (the B run)
