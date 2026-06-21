"""Unit tests for easy_otp.core.gtfsrt_recorder (RT-2 helpers).

No QGIS, no network — pure stdlib + pytest.
Run: py -m pytest easy_otp/test/test_record_gtfs_rt.py -v
"""

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
    for count in (5, 30):
        write_manifest(tmp_path, _SAMPLE_URL, "", 60, started, stopped, count, 0, 0)
    data = json.loads((tmp_path / "recording.json").read_text(encoding="utf-8"))
    assert data["snapshot_count"] == 30
