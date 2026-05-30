"""Tests for easy_otp.core.zonal._classify_value.

Pure Python — no QGIS / GDAL dependency. Run with standard pytest:
    python -m pytest easy_otp/test/test_classification.py -v

_classify_value(val, interval_min) -> str maps an otp_mean count to one of
four service-time category strings (or "" for inaccessible).

Thresholds are always in service-minutes regardless of the sampling interval:
    constantly accessible   : service_min >= 720
    regularly accessible    : 360 <= service_min < 720
    periodically accessible : 180 <= service_min < 360
    episodically accessible : 0   <  service_min < 180
    inaccessible ("")       : service_min <= 0 or val is None
"""

from __future__ import annotations

from easy_otp.core.zonal import _classify_value


# ---------------------------------------------------------------------------
# 1-minute interval (service_min = val * 1)
# ---------------------------------------------------------------------------

def test_constantly_accessible():
    assert _classify_value(720.0, 1) == "constantly accessible"
    assert _classify_value(961.0, 1) == "constantly accessible"


def test_regularly_accessible():
    assert _classify_value(360.0, 1) == "regularly accessible"
    assert _classify_value(500.0, 1) == "regularly accessible"
    assert _classify_value(719.0, 1) == "regularly accessible"


def test_periodically_accessible():
    assert _classify_value(180.0, 1) == "periodically accessible"
    assert _classify_value(270.0, 1) == "periodically accessible"
    assert _classify_value(359.0, 1) == "periodically accessible"


def test_episodically_accessible():
    assert _classify_value(1.0, 1) == "episodically accessible"
    assert _classify_value(90.0, 1) == "episodically accessible"
    assert _classify_value(179.0, 1) == "episodically accessible"


# ---------------------------------------------------------------------------
# Inaccessible
# ---------------------------------------------------------------------------

def test_inaccessible_zero():
    assert _classify_value(0.0, 1) == ""


def test_inaccessible_null():
    assert _classify_value(None, 1) == ""


def test_inaccessible_negative():
    assert _classify_value(-1.0, 1) == ""


# ---------------------------------------------------------------------------
# Boundary values (service_min exactly at thresholds)
# ---------------------------------------------------------------------------

def test_boundary_720():
    """Exactly 720 min → constantly accessible."""
    assert _classify_value(720.0, 1) == "constantly accessible"


def test_boundary_360():
    """Exactly 360 min → regularly accessible."""
    assert _classify_value(360.0, 1) == "regularly accessible"


def test_boundary_180():
    """Exactly 180 min → periodically accessible."""
    assert _classify_value(180.0, 1) == "periodically accessible"


# ---------------------------------------------------------------------------
# Non-1-minute intervals
# ---------------------------------------------------------------------------

def test_interval_15min():
    """val=48, interval=15 → service_min=720 → constantly accessible."""
    assert _classify_value(48.0, 15) == "constantly accessible"


def test_interval_60min():
    """val=12, interval=60 → service_min=720 → constantly accessible."""
    assert _classify_value(12.0, 60) == "constantly accessible"


def test_interval_15min_episodic():
    """val=1, interval=15 → service_min=15 → episodically accessible."""
    assert _classify_value(1.0, 15) == "episodically accessible"
