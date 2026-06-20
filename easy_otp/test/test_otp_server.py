"""Tests for easy_otp.core.otp_server process-creation flags.

Pure stdlib — no QGIS / GDAL dependency. Run with standard pytest:
    py -m pytest easy_otp/test/test_otp_server.py -v

Regression guard for the windowing fix: the graph --build phase must stay
windowless (CREATE_NO_WINDOW), while the --server phase must show a closeable
console window (CREATE_NO_WINDOW omitted) so the user can stop the server by
closing it. Windows-only, because the flags only exist there.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from easy_otp.core.otp_server import _popen_kwargs

win32_only = pytest.mark.skipif(
    sys.platform != "win32", reason="creationflags are Windows-only"
)


@win32_only
def test_build_phase_is_windowless() -> None:
    flags = _popen_kwargs()["creationflags"]
    assert flags & subprocess.CREATE_NO_WINDOW
    assert flags & subprocess.CREATE_NEW_PROCESS_GROUP


@win32_only
def test_server_phase_shows_window() -> None:
    flags = _popen_kwargs(show_window=True)["creationflags"]
    assert not (flags & subprocess.CREATE_NO_WINDOW)
    assert flags & subprocess.CREATE_NEW_PROCESS_GROUP


def test_non_windows_returns_empty() -> None:
    if sys.platform == "win32":
        pytest.skip("covered by the Windows-specific tests")
    assert _popen_kwargs() == {}
    assert _popen_kwargs(show_window=True) == {}
