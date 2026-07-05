"""Unit tests for family_a.cli (FA-1).

No QGIS, no network, no real wall-clock waits (time.monotonic/time.sleep are
mocked so the record loop runs instantly). Run: pytest tests/test_cli.py -v
"""

import json
from datetime import datetime
from unittest import mock

import pytest

from family_a.cli import _cmd_build, _cmd_match, _cmd_record, build_parser
from family_a.recorder import SnapshotFetchError


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------

def test_record_defaults():
    parser = build_parser()
    args = parser.parse_args(["record", "--url", "http://x", "--out-dir", "out"])
    assert args.duration_min == 60
    assert args.interval_sec == 60
    assert args.func is _cmd_record


def test_match_and_build_wired():
    parser = build_parser()
    assert parser.parse_args(["match"]).func is _cmd_match
    assert parser.parse_args(["build"]).func is _cmd_build


@pytest.mark.parametrize("flag", ["--duration-min", "--interval-sec"])
def test_non_positive_values_rejected(flag):
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["record", "--url", "http://x", "--out-dir", "out", flag, "0"])


# ---------------------------------------------------------------------------
# match / build stubs
# ---------------------------------------------------------------------------

def test_cmd_match_prints_and_returns_1(capsys):
    assert _cmd_match(argparse_namespace()) == 1
    captured = capsys.readouterr()
    assert "not implemented yet" in captured.err
    assert "FA-2" in captured.err


def test_cmd_build_prints_and_returns_1(capsys):
    assert _cmd_build(argparse_namespace()) == 1
    captured = capsys.readouterr()
    assert "not implemented yet" in captured.err
    assert "FA-3" in captured.err


def argparse_namespace():
    class _NS:
        pass

    return _NS()


# ---------------------------------------------------------------------------
# _cmd_record (time mocked so the loop resolves instantly)
# ---------------------------------------------------------------------------

class _FakeClock:
    """Deterministic stand-in for time.monotonic that advances on each read."""

    def __init__(self, step: float = 1.0):
        self._now = 0.0
        self._step = step

    def monotonic(self) -> float:
        value = self._now
        self._now += self._step
        return value

    def sleep(self, _seconds: float) -> None:
        self._now += 1.0


def _make_args(tmp_path, **overrides):
    class _NS:
        url = "http://example.com/vehicles.pb"
        out_dir = str(tmp_path)
        duration_min = 1
        interval_sec = 60

    ns = _NS()
    for key, value in overrides.items():
        setattr(ns, key, value)
    return ns


def test_cmd_record_success_writes_snapshots_and_manifest(tmp_path):
    clock = _FakeClock(step=10.0)  # total_sec = 60; ~6 iterations before break
    with mock.patch("family_a.cli.time.monotonic", side_effect=clock.monotonic), \
            mock.patch("family_a.cli.time.sleep", side_effect=clock.sleep), \
            mock.patch("family_a.cli.fetch_snapshot", return_value=b"\x01\x02"):
        result = _cmd_record(_make_args(tmp_path))

    assert result == 0
    manifest = json.loads((tmp_path / "recording.json").read_text(encoding="utf-8"))
    assert manifest["snapshot_count"] >= 1
    assert manifest["failed_count"] == 0
    assert manifest["total_bytes"] == manifest["snapshot_count"] * 2
    snapshots = list(tmp_path.glob("snapshot_*.pb"))
    assert len(snapshots) == manifest["snapshot_count"]


def test_cmd_record_counts_failures_and_still_writes_manifest(tmp_path):
    clock = _FakeClock(step=10.0)
    with mock.patch("family_a.cli.time.monotonic", side_effect=clock.monotonic), \
            mock.patch("family_a.cli.time.sleep", side_effect=clock.sleep), \
            mock.patch("family_a.cli.fetch_snapshot", side_effect=SnapshotFetchError("HTTP 403")):
        result = _cmd_record(_make_args(tmp_path))

    assert result == 0
    manifest = json.loads((tmp_path / "recording.json").read_text(encoding="utf-8"))
    assert manifest["snapshot_count"] == 0
    assert manifest["failed_count"] >= 1
    assert manifest["feed_id"] == ""


def test_cmd_record_keyboard_interrupt_still_writes_manifest(tmp_path):
    with mock.patch("family_a.cli.time.monotonic", side_effect=[0.0, 5.0]), \
            mock.patch("family_a.cli.fetch_snapshot", side_effect=KeyboardInterrupt):
        result = _cmd_record(_make_args(tmp_path))

    assert result == 0
    assert (tmp_path / "recording.json").exists()
