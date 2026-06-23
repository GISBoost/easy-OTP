"""Unit tests for the _origin_attributes helper (pure Python, no QGIS)."""

import re

import pytest

from easy_otp.core.origin_utils import _origin_attributes

EXPECTED_KEYS = [
    "router_id",
    "lon",
    "lat",
    "analysis_type",
    "analysis_date",
    "time_start",
    "time_end",
    "interval_min",
    "threshold_min",
    "arrive_by",
    "walk_speed",
    "max_walk_distance",
    "created_at",
]

_BASE = dict(
    router_id="abc123",
    lon=17.038538,
    lat=51.107883,
    date_s="2024-05-15",
    start_s="06:00",
    end_s="22:00",
    interval_min=1,
    threshold_min=30,
    arrive_by=False,
    walk_speed=1.3,
    max_walk_distance=800,
    created_at="2024-05-15T08:00:00",
)


def _call(**overrides):
    kw = {**_BASE, **overrides}
    return _origin_attributes(**kw)


def test_key_order():
    result = _call()
    assert list(result.keys()) == EXPECTED_KEYS


def test_router_id_passthrough():
    assert _call(router_id="xyz987")["router_id"] == "xyz987"


def test_lon_rounded_to_6dp():
    result = _call(lon=17.0385381234567)
    assert result["lon"] == round(17.0385381234567, 6)
    # Verify it really is 6 dp precision
    assert abs(result["lon"] - 17.038538) < 1e-6


def test_lat_rounded_to_6dp():
    result = _call(lat=51.1078831234567)
    assert result["lat"] == round(51.1078831234567, 6)


def test_analysis_type_always_static():
    assert _call()["analysis_type"] == "static"


def test_arrive_by_true():
    assert _call(arrive_by=True)["arrive_by"] is True


def test_arrive_by_false():
    assert _call(arrive_by=False)["arrive_by"] is False


def test_created_at_passthrough():
    ts = "2024-01-01T12:34:56"
    assert _call(created_at=ts)["created_at"] == ts


def test_created_at_iso_format():
    # Validate that a datetime.now().isoformat(timespec="seconds") value matches
    # the expected pattern YYYY-MM-DDTHH:MM:SS
    iso_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")
    assert iso_pattern.match(_call()["created_at"])


def test_integer_fields():
    result = _call(interval_min=5, threshold_min=45, max_walk_distance=1000)
    assert result["interval_min"] == 5
    assert result["threshold_min"] == 45
    assert result["max_walk_distance"] == 1000


def test_walk_speed_passthrough():
    assert _call(walk_speed=1.5)["walk_speed"] == 1.5


def test_date_and_time_strings():
    result = _call(date_s="2024-09-01", start_s="07:30", end_s="21:00")
    assert result["analysis_date"] == "2024-09-01"
    assert result["time_start"] == "07:30"
    assert result["time_end"] == "21:00"
