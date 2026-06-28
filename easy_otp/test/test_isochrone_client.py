"""Unit tests for easy_otp.core.isochrone_client (pure stdlib, no QGIS, no network)."""

from __future__ import annotations

import io
import json
import urllib.parse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from easy_otp.core.isochrone_client import IsochroneClient, _effective_mode
from easy_otp.core.otp_client import OtpClientError

# Path to the real OTP 1.5 isochrone response (used as a fixture).
FIXTURE_PATH = (
    Path(__file__).parent.parent.parent
    / "docs" / "gisboostgithub" / "OpenTripPlanner" / "isochrone.geojson"
)

_FIXTURE_GEOJSON = FIXTURE_PATH.read_text(encoding="utf-8")

# ── helper ──────────────────────────────────────────────────────────────────

_BASE_CALL = dict(
    from_lat=51.7474,
    from_lon=19.4518,
    cutoffs_sec=[900, 1800, 2700],
    mode="TRANSIT",
    date_mmddyyyy="11-22-2024",
    time_hhmmss="08:30:00",
    max_walk_distance=700,
    walk_reluctance=3,
    wait_reluctance=2,
    transfer_penalty=60,
    min_transfer_time=60,
)


def _make_mock_resp(body: bytes):
    """Return a mock context manager that urlopen will return."""
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _call_and_capture_url(**overrides):
    """Call get_isochrone with _BASE_CALL + overrides; return (client, url_called)."""
    client = IsochroneClient(port=8801, router="default")
    captured = {}

    def fake_urlopen(url, timeout=None):
        captured["url"] = url
        return _make_mock_resp(_FIXTURE_GEOJSON.encode())

    with patch("easy_otp.core.isochrone_client.urllib.request.urlopen", side_effect=fake_urlopen):
        client.get_isochrone(**{**_BASE_CALL, **overrides})

    return captured["url"]


# ── _effective_mode ──────────────────────────────────────────────────────────

def test_effective_mode_transit_appends_walk():
    assert _effective_mode("TRANSIT") == "TRANSIT,WALK"


def test_effective_mode_bus_appends_walk():
    assert _effective_mode("BUS") == "BUS,WALK"


def test_effective_mode_rail_appends_walk():
    assert _effective_mode("RAIL") == "RAIL,WALK"


def test_effective_mode_tram_appends_walk():
    assert _effective_mode("TRAM") == "TRAM,WALK"


def test_effective_mode_subway_appends_walk():
    assert _effective_mode("SUBWAY") == "SUBWAY,WALK"


def test_effective_mode_walk_bare():
    assert _effective_mode("WALK") == "WALK"


def test_effective_mode_car_bare():
    assert _effective_mode("CAR") == "CAR"


def test_effective_mode_bicycle_bare():
    assert _effective_mode("BICYCLE") == "BICYCLE"


def test_effective_mode_case_insensitive():
    assert _effective_mode("transit") == "TRANSIT,WALK"


# ── URL construction ─────────────────────────────────────────────────────────

def test_url_contains_isochrone_endpoint():
    url = _call_and_capture_url()
    assert "/otp/routers/default/isochrone" in url


def test_url_contains_batch_true():
    url = _call_and_capture_url()
    assert "batch=true" in url


def test_url_contains_mode_with_walk():
    url = _call_and_capture_url()
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)
    assert params["mode"] == ["TRANSIT,WALK"]


def test_url_contains_from_place():
    url = _call_and_capture_url()
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)
    assert params["fromPlace"] == ["51.7474,19.4518"]


def test_url_repeated_cutoff_sec():
    url = _call_and_capture_url()
    parsed = urllib.parse.urlparse(url)
    # parse_qs collapses repeated keys into a list
    params = urllib.parse.parse_qs(parsed.query)
    assert sorted(params["cutoffSec"]) == ["1800", "2700", "900"]


def test_url_direction_from_no_toplace():
    url = _call_and_capture_url(direction="FROM")
    assert "toPlace" not in url


def test_url_direction_to_adds_toplace():
    url = _call_and_capture_url(direction="TO")
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)
    assert "toPlace" in params
    assert params["toPlace"] == params["fromPlace"]


def test_url_router_id_sent():
    url = _call_and_capture_url()
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)
    assert params["routerId"] == ["default"]


# ── parse_isochrones from fixture ────────────────────────────────────────────

def test_parse_fixture_returns_three_items():
    result = IsochroneClient.parse_isochrones(_FIXTURE_GEOJSON)
    assert len(result) == 3


def test_parse_fixture_sorted_ascending():
    result = IsochroneClient.parse_isochrones(_FIXTURE_GEOJSON)
    cutoffs = [r["cutoff_sec"] for r in result]
    assert cutoffs == sorted(cutoffs)


def test_parse_fixture_cutoff_sec_values():
    result = IsochroneClient.parse_isochrones(_FIXTURE_GEOJSON)
    cutoffs = sorted(r["cutoff_sec"] for r in result)
    assert cutoffs == [900, 1800, 2700]


def test_parse_fixture_cutoff_sec_is_int():
    result = IsochroneClient.parse_isochrones(_FIXTURE_GEOJSON)
    for item in result:
        assert isinstance(item["cutoff_sec"], int)


def test_parse_fixture_geometry_is_dict():
    result = IsochroneClient.parse_isochrones(_FIXTURE_GEOJSON)
    for item in result:
        assert isinstance(item["geometry"], dict)
        assert "type" in item["geometry"]
        assert "coordinates" in item["geometry"]


def test_parse_fixture_reads_properties_time():
    """Verify we read properties.time (not some other field)."""
    raw = json.loads(_FIXTURE_GEOJSON)
    times_in_fixture = {f["properties"]["time"] for f in raw["features"]}
    result = IsochroneClient.parse_isochrones(_FIXTURE_GEOJSON)
    result_cutoffs = {r["cutoff_sec"] for r in result}
    assert result_cutoffs == times_in_fixture


# ── error path ───────────────────────────────────────────────────────────────

def test_non_feature_collection_raises():
    client = IsochroneClient()
    bad_body = b'{"error": "no route found"}'

    with patch(
        "easy_otp.core.isochrone_client.urllib.request.urlopen",
        return_value=_make_mock_resp(bad_body),
    ):
        with pytest.raises(OtpClientError, match="FeatureCollection"):
            client.get_isochrone(**_BASE_CALL)


def test_http_error_raises_otp_client_error():
    import urllib.error as _ue

    client = IsochroneClient()
    http_err = _ue.HTTPError(url="http://x", code=500, msg="error", hdrs={}, fp=io.BytesIO(b"oops"))

    with patch(
        "easy_otp.core.isochrone_client.urllib.request.urlopen",
        side_effect=http_err,
    ):
        with pytest.raises(OtpClientError, match="HTTP 500"):
            client.get_isochrone(**_BASE_CALL)


def test_url_error_raises_otp_client_error():
    import urllib.error as _ue

    client = IsochroneClient()

    with patch(
        "easy_otp.core.isochrone_client.urllib.request.urlopen",
        side_effect=_ue.URLError("connection refused"),
    ):
        with pytest.raises(OtpClientError, match="unreachable"):
            client.get_isochrone(**_BASE_CALL)
