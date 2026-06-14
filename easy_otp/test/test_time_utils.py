"""Tests for easy_otp.core.time_utils.

Pure stdlib — no QGIS / GDAL dependency. Run with standard pytest:
    python -m pytest easy_otp/test/test_time_utils.py -v
"""

from __future__ import annotations

from datetime import datetime

import pytest

from easy_otp.core.time_utils import (
    build_time_list,
    forward_window,
    time_to_filename_slug,
)


# ---------------------------------------------------------------------------
# build_time_list — happy paths
# ---------------------------------------------------------------------------

def test_1min_window():
    """06:00–22:00 at 1 min → 961 entries with correct endpoints."""
    result = build_time_list(6, 0, 22, 0, 1)
    assert len(result) == 961
    assert result[0] == "06:00:00"
    assert result[-1] == "22:00:00"


def test_15min_window():
    """06:00–22:00 at 15 min → 65 entries."""
    result = build_time_list(6, 0, 22, 0, 15)
    assert len(result) == 65
    assert result[0] == "06:00:00"
    assert result[-1] == "22:00:00"


def test_60min_window():
    """06:00–22:00 at 60 min → 17 entries."""
    result = build_time_list(6, 0, 22, 0, 60)
    assert len(result) == 17
    assert result[0] == "06:00:00"
    assert result[-1] == "22:00:00"


def test_single_entry():
    """start == end → list with exactly one entry."""
    result = build_time_list(10, 30, 10, 30, 15)
    assert result == ["10:30:00"]


def test_format():
    """Entries are formatted as HH:MM:SS with zero-padding."""
    result = build_time_list(6, 0, 6, 1, 1)
    assert result == ["06:00:00", "06:01:00"]


# ---------------------------------------------------------------------------
# build_time_list — error paths
# ---------------------------------------------------------------------------

def test_end_before_start():
    """Window end before start raises ValueError."""
    with pytest.raises(ValueError, match="(?i)before"):
        build_time_list(22, 0, 6, 0, 1)


def test_invalid_interval():
    """Interval not in {1, 15, 60} raises ValueError."""
    with pytest.raises(ValueError, match="(?i)interval"):
        build_time_list(6, 0, 22, 0, 5)


def test_invalid_hours():
    """Hour value out of 0–23 raises ValueError."""
    with pytest.raises(ValueError, match="(?i)hour"):
        build_time_list(25, 0, 22, 0, 1)


# ---------------------------------------------------------------------------
# forward_window — now-anchored realtime window
# ---------------------------------------------------------------------------

def test_forward_window_normal():
    """Window starts at now (truncated to the minute) and ends now+horizon."""
    now = datetime(2026, 6, 15, 10, 33, 47)
    sh, sm, eh, em, truncated = forward_window(now, 60)
    assert (sh, sm) == (10, 33)
    assert (eh, em) == (11, 33)
    assert truncated is False


def test_forward_window_truncates_at_midnight():
    """A horizon crossing midnight is clamped to 23:59 with truncated=True."""
    now = datetime(2026, 6, 15, 23, 30, 0)
    sh, sm, eh, em, truncated = forward_window(now, 60)
    assert (sh, sm) == (23, 30)
    assert (eh, em) == (23, 59)
    assert truncated is True


def test_forward_window_feeds_build_time_list():
    """forward_window output drives build_time_list to a sensible surface count."""
    now = datetime(2026, 6, 15, 10, 0, 0)
    sh, sm, eh, em, _ = forward_window(now, 30)
    times = build_time_list(sh, sm, eh, em, 1)
    assert times[0] == "10:00:00"
    assert times[-1] == "10:30:00"
    assert len(times) == 31


def test_forward_window_rejects_nonpositive_horizon():
    with pytest.raises(ValueError, match="(?i)horizon"):
        forward_window(datetime(2026, 6, 15, 10, 0, 0), 0)


# ---------------------------------------------------------------------------
# time_to_filename_slug
# ---------------------------------------------------------------------------

def test_filename_slug():
    assert time_to_filename_slug("06:30:00") == "06-30-00"
    assert time_to_filename_slug("22:00:00") == "22-00-00"
