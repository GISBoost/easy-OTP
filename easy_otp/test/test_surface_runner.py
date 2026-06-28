"""Unit tests for surface_runner.run_surface_loop_over_points.

No network, no QGIS interpreter required.  QGIS modules are stubbed via
sys.modules before the project import so QgsProcessingException becomes a
plain RuntimeError subclass that pytest can intercept normally.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Stub QGIS modules before importing surface_runner
# ---------------------------------------------------------------------------

class _FakeQgsProcessingException(RuntimeError):
    """Stand-in for QgsProcessingException — a real Exception subclass."""


if "qgis" not in sys.modules:
    _qgis_core = MagicMock()
    _qgis_core.QgsProcessingException = _FakeQgsProcessingException
    sys.modules["qgis"] = MagicMock()
    sys.modules["qgis.core"] = _qgis_core
    sys.modules["qgis.PyQt"] = MagicMock()
    sys.modules["qgis.PyQt.QtCore"] = MagicMock()
else:
    # Running inside QGIS interpreter — use the real exception
    from qgis.core import QgsProcessingException as _FakeQgsProcessingException  # type: ignore[no-redef]

from easy_otp.core.surface_runner import SurfaceJobParams, run_surface_loop_over_points  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_job() -> SurfaceJobParams:
    return SurfaceJobParams(
        from_place_lat_lon=(0.0, 0.0),
        date_mmddyyyy="01-15-2024",
        max_walk_distance=800,
        walk_reluctance=3.0,
        wait_reluctance=2.0,
        transfer_penalty=60,
        min_transfer_time=60,
        walk_speed=1.3,
        arrive_by=False,
    )


def _mock_feedback(cancel_after: int = 9999) -> MagicMock:
    fb = MagicMock()
    call_count = {"n": 0}

    def _is_canceled():
        call_count["n"] += 1
        return call_count["n"] > cancel_after

    fb.isCanceled.side_effect = _is_canceled
    return fb


def _make_client(tmp_path: Path, n_points: int) -> MagicMock:
    """Return a mocked OtpClient whose download writes an empty file."""
    client = MagicMock()
    client.create_surface.side_effect = list(range(1, n_points + 1))

    def _fake_download(surface_id, output_path, timeout_s=None, log_fn=None):
        output_path.touch()

    client.download_surface_raster.side_effect = _fake_download
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_one_call_per_point_fixed_time(tmp_path):
    """create_surface is called once per point with arrive_by=False and the fixed time."""
    points = [(51.1, 17.0), (51.2, 17.1), (51.3, 17.2)]
    client = _make_client(tmp_path, len(points))
    job = _make_job()

    result = run_surface_loop_over_points(
        client, points, "08:00:00", job, tmp_path / "surfaces", _mock_feedback()
    )

    assert client.create_surface.call_count == 3
    for idx, pt in enumerate(points):
        c = client.create_surface.call_args_list[idx]
        assert c.kwargs["from_place_lat_lon"] == pt
        assert c.kwargs["time_hhmmss"] == "08:00:00"
        assert c.kwargs["arrive_by"] is False


def test_deterministic_filenames(tmp_path):
    """Output filenames follow surface_point_NNNN.tiff, zero-padded, in order."""
    points = [(51.1, 17.0), (51.2, 17.1), (51.3, 17.2)]
    client = _make_client(tmp_path, len(points))

    surfaces = run_surface_loop_over_points(
        client, points, "08:00:00", _make_job(), tmp_path / "surfaces", _mock_feedback()
    )

    names = [p.name for p in surfaces]
    assert names == [
        "surface_point_0000.tiff",
        "surface_point_0001.tiff",
        "surface_point_0002.tiff",
    ]


def test_returns_paths_in_input_order(tmp_path):
    """Return list has the same length as points and paths exist."""
    points = [(51.0, 17.0), (52.0, 18.0)]
    client = _make_client(tmp_path, len(points))

    surfaces = run_surface_loop_over_points(
        client, points, "09:30:00", _make_job(), tmp_path / "surfaces", _mock_feedback()
    )

    assert len(surfaces) == 2
    for p in surfaces:
        assert p.exists()


def test_mode_forwarded_to_create_surface(tmp_path):
    """The mode argument is forwarded to create_surface."""
    points = [(51.0, 17.0)]
    client = _make_client(tmp_path, 1)

    run_surface_loop_over_points(
        client, points, "08:00:00", _make_job(), tmp_path / "surfaces",
        _mock_feedback(), mode="WALK",
    )

    assert client.create_surface.call_args.kwargs["mode"] == "WALK"


def test_cancel_before_first_create(tmp_path):
    """isCanceled() True on first check → exception raised, create_surface never called."""
    points = [(51.0, 17.0), (51.1, 17.1)]
    client = _make_client(tmp_path, 2)
    fb = _mock_feedback(cancel_after=0)  # cancel immediately

    with pytest.raises(_FakeQgsProcessingException):
        run_surface_loop_over_points(
            client, points, "08:00:00", _make_job(), tmp_path / "surfaces", fb
        )

    client.create_surface.assert_not_called()


def test_cancel_between_create_and_download(tmp_path):
    """isCanceled() True between create and download → exception raised, output file not created."""
    points = [(51.0, 17.0), (51.1, 17.1)]
    surfaces_dir = tmp_path / "surfaces"
    surfaces_dir.mkdir()

    client = MagicMock()
    client.create_surface.return_value = 1

    partial = surfaces_dir / "surface_point_0000.tiff"

    def _fake_download(surface_id, output_path, timeout_s=None, log_fn=None):
        output_path.touch()  # simulate partial write

    client.download_surface_raster.side_effect = _fake_download

    # cancel_after=1: first isCanceled() call (top of loop) returns False,
    # second call (between create and download) returns True
    fb = _mock_feedback(cancel_after=1)

    with pytest.raises(_FakeQgsProcessingException):
        run_surface_loop_over_points(
            client, points, "08:00:00", _make_job(), surfaces_dir, fb
        )

    # partial file must be cleaned up
    assert not partial.exists()


def test_empty_points_raises(tmp_path):
    """Passing an empty list raises QgsProcessingException immediately."""
    client = _make_client(tmp_path, 0)
    with pytest.raises(_FakeQgsProcessingException):
        run_surface_loop_over_points(
            client, [], "08:00:00", _make_job(), tmp_path / "surfaces", _mock_feedback()
        )
    client.create_surface.assert_not_called()
