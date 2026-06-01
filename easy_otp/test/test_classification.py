"""Tests for easy_otp.core.zonal._classify_value.

Pure Python — no QGIS / GDAL dependency. Run with standard pytest:
    python -m pytest easy_otp/test/test_classification.py -v

_classify_value(val, interval_min, n_surfaces) -> str maps an otp_mean count
to one of four service-time category strings (or "" for inaccessible).

Thresholds scale proportionally for windows shorter than the reference 960-min
window (06:00–22:00). For full-window runs the original absolute values apply:
    constantly accessible   : service_min >= 720
    regularly accessible    : 360 <= service_min < 720
    periodically accessible : 180 <= service_min < 360
    episodically accessible : 0   <  service_min < 180
    inaccessible ("")       : service_min <= 0 or val is None
"""

from __future__ import annotations

from easy_otp.core.zonal import _classify_value


# ---------------------------------------------------------------------------
# 1-minute interval, standard window (961 surfaces × 1 min = 961 min ≥ 960)
# scale = 1.0 → thresholds 720 / 360 / 180 unchanged
# ---------------------------------------------------------------------------

def test_constantly_accessible():
    assert _classify_value(720.0, 1, n_surfaces=961) == "constantly accessible"
    assert _classify_value(961.0, 1, n_surfaces=961) == "constantly accessible"


def test_regularly_accessible():
    assert _classify_value(360.0, 1, n_surfaces=961) == "regularly accessible"
    assert _classify_value(500.0, 1, n_surfaces=961) == "regularly accessible"
    assert _classify_value(719.0, 1, n_surfaces=961) == "regularly accessible"


def test_periodically_accessible():
    assert _classify_value(180.0, 1, n_surfaces=961) == "periodically accessible"
    assert _classify_value(270.0, 1, n_surfaces=961) == "periodically accessible"
    assert _classify_value(359.0, 1, n_surfaces=961) == "periodically accessible"


def test_episodically_accessible():
    assert _classify_value(1.0, 1, n_surfaces=961) == "episodically accessible"
    assert _classify_value(90.0, 1, n_surfaces=961) == "episodically accessible"
    assert _classify_value(179.0, 1, n_surfaces=961) == "episodically accessible"


# ---------------------------------------------------------------------------
# Inaccessible
# ---------------------------------------------------------------------------

def test_inaccessible_zero():
    assert _classify_value(0.0, 1, n_surfaces=961) == ""


def test_inaccessible_null():
    assert _classify_value(None, 1, n_surfaces=961) == ""


def test_inaccessible_negative():
    assert _classify_value(-1.0, 1, n_surfaces=961) == ""


# ---------------------------------------------------------------------------
# Boundary values (service_min exactly at thresholds, standard window)
# ---------------------------------------------------------------------------

def test_boundary_720():
    """Exactly 720 min → constantly accessible."""
    assert _classify_value(720.0, 1, n_surfaces=961) == "constantly accessible"


def test_boundary_360():
    """Exactly 360 min → regularly accessible."""
    assert _classify_value(360.0, 1, n_surfaces=961) == "regularly accessible"


def test_boundary_180():
    """Exactly 180 min → periodically accessible."""
    assert _classify_value(180.0, 1, n_surfaces=961) == "periodically accessible"


# ---------------------------------------------------------------------------
# Non-1-minute intervals, standard windows (scale = 1.0)
# ---------------------------------------------------------------------------

def test_interval_15min():
    """val=48, interval=15 → service_min=720, n_surfaces=65 (standard) → constantly."""
    assert _classify_value(48.0, 15, n_surfaces=65) == "constantly accessible"


def test_interval_60min():
    """val=12, interval=60 → service_min=720, n_surfaces=17 (standard) → constantly."""
    assert _classify_value(12.0, 60, n_surfaces=17) == "constantly accessible"


def test_interval_15min_episodic():
    """val=1, interval=15 → service_min=15 → episodically accessible."""
    assert _classify_value(1.0, 15, n_surfaces=65) == "episodically accessible"


# ---------------------------------------------------------------------------
# Short window: 07:00–10:00, 15-min interval, 13 surfaces
# window_min = 195; scale = 195/960 ≈ 0.2031
# thresholds: constantly ≥ 146.25 min, regularly ≥ 73.1 min, periodically ≥ 36.6 min
# ---------------------------------------------------------------------------

def test_short_window_constantly():
    # 10 surfaces × 15 min = 150 min ≥ 146.25
    assert _classify_value(10.0, 15, n_surfaces=13) == "constantly accessible"


def test_short_window_regularly():
    # 5 surfaces × 15 min = 75 min, 73.1 ≤ 75 < 146.25
    assert _classify_value(5.0, 15, n_surfaces=13) == "regularly accessible"


def test_short_window_periodically():
    # 3 surfaces × 15 min = 45 min, 36.6 ≤ 45 < 73.1
    assert _classify_value(3.0, 15, n_surfaces=13) == "periodically accessible"


def test_short_window_episodic():
    # 1 surface × 15 min = 15 min < 36.6
    assert _classify_value(1.0, 15, n_surfaces=13) == "episodically accessible"
