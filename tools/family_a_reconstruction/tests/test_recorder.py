"""Unit tests for family_a.recorder (FA-1 helpers).

No QGIS, no network — pure stdlib + pytest.
Run: pytest tests/test_recorder.py -v
"""

import json
import re
import ssl
import urllib.error
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from unittest import mock

import pytest

from family_a.recorder import (
    SnapshotFetchError,
    earliest_recording_date,
    fetch_snapshot,
    parse_snapshot_filename,
    snapshot_filename,
    write_manifest,
    write_snapshot,
)

_SAMPLE_URL = "http://example.com/vehicle-positions.pb"
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


def test_fetch_snapshot_uses_certifi_ssl_context():
    """Verify HTTPS requests use certifi's CA bundle, not just the OS default -
    the OS root store can be missing a root a given city host chains to (see
    recorder.py's _SSL_CONTEXT docstring), even though the root is legitimate.
    """
    resp = _FakeResponse(_SAMPLE_BYTES, 200)
    with mock.patch("urllib.request.urlopen", return_value=resp) as mock_urlopen:
        fetch_snapshot("https://example.com/vehicle-positions.pb")

    _, kwargs = mock_urlopen.call_args
    assert isinstance(kwargs.get("context"), ssl.SSLContext)


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
# parse_snapshot_filename / earliest_recording_date (FA-6)
# ---------------------------------------------------------------------------

def test_parse_snapshot_filename_roundtrips_snapshot_filename():
    for dt in (
        datetime(2026, 1, 1, 0, 0, 0),
        datetime(2026, 6, 21, 8, 30, 15),
        datetime(2026, 12, 31, 23, 59, 59),
    ):
        assert parse_snapshot_filename(snapshot_filename(dt)) == dt


def test_parse_snapshot_filename_rejects_malformed_name():
    assert parse_snapshot_filename("snapshot_bogus.pb") is None
    assert parse_snapshot_filename("not_a_snapshot_at_all.txt") is None
    assert parse_snapshot_filename("snapshot_20260101.pb") is None


def test_earliest_recording_date_picks_earliest_across_unsorted_input():
    paths = [
        Path("snapshot_20260103-000000.pb"),
        Path("snapshot_20260101-120000.pb"),
        Path("snapshot_20260102-060000.pb"),
    ]
    assert earliest_recording_date(paths) == date(2026, 1, 1)


def test_earliest_recording_date_raises_on_no_parseable_names():
    paths = [Path("snapshot_bogus.pb"), Path("other_file.pb")]
    with pytest.raises(ValueError):
        earliest_recording_date(paths)


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
        feed_id="",
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
    assert data["feed_id"] == ""
    assert data["sampling_interval_sec"] == 60
    assert data["started_at"] == started.isoformat()
    assert data["stopped_at"] == stopped.isoformat()
    assert data["snapshot_count"] == 59
    assert data["failed_count"] == 1
    assert data["total_bytes"] == 1_800_000
    assert "unchanged_streak_max" not in data


def test_write_manifest_overwrites(tmp_path):
    started = datetime(2026, 6, 21, 6, 0, 0)
    stopped_first = datetime(2026, 6, 21, 6, 30, 0)
    stopped_second = datetime(2026, 6, 21, 7, 0, 0)

    write_manifest(
        tmp_path,
        url=_SAMPLE_URL,
        feed_id="",
        interval=60,
        started_at=started,
        stopped_at=stopped_first,
        snapshot_count=30,
        failed_count=0,
        total_bytes=900_000,
    )
    write_manifest(
        tmp_path,
        url=_SAMPLE_URL,
        feed_id="",
        interval=60,
        started_at=started,
        stopped_at=stopped_second,
        snapshot_count=60,
        failed_count=1,
        total_bytes=1_800_000,
    )

    data = json.loads((tmp_path / "recording.json").read_text(encoding="utf-8"))
    assert data["stopped_at"] == stopped_second.isoformat()
    assert data["snapshot_count"] == 60
    assert data["failed_count"] == 1
    assert data["total_bytes"] == 1_800_000
