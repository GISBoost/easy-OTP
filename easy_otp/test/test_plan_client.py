"""Unit tests for PlanClient — no network, no QGIS required."""

import json
import urllib.error
import urllib.parse
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from easy_otp.core.otp_client import OtpClientError
from easy_otp.core.plan_client import PlanClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_resp(body: dict) -> MagicMock:
    raw = json.dumps(body).encode()
    resp = MagicMock()
    resp.read.return_value = raw
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _call_get_trip(body: dict) -> dict:
    client = PlanClient(hostname="localhost", port=8801, router="default")
    with patch("easy_otp.core.plan_client.urllib.request.urlopen", return_value=_make_resp(body)):
        return client.get_trip(
            from_lat=51.80707,
            from_lon=19.32293,
            to_lat=51.7474,
            to_lon=19.4518,
            mode="TRANSIT",
            date_mmddyyyy="11-22-2024",
            time_hhmmss="08:30:00",
        )


# ---------------------------------------------------------------------------
# OK itinerary — ground-truth fixture from pop_results2.csv row 1
# ---------------------------------------------------------------------------

class TestOkItinerary:
    """OTP /plan success path: seconds → decimal minutes, rounding, types."""

    # Row 1 of pop_results2.csv:
    #   duration=75.02, transittime=44, walktime=10.77, waitingtime=20.25, transfers=1
    # OTP would have returned these raw second values:
    #   duration=4501 (75.02*60=4501.2 → OTP truncates to int → 4501 → 4501/60=75.0166… → round=75.02)
    # We use the exact seconds that produce the CSV values after round(x/60, 2):
    #   4501 / 60 = 75.0166... → round 2dp = 75.02  ✓
    #   2640 / 60 = 44.0       → round 2dp = 44.0   ✓
    #   646  / 60 = 10.7666... → round 2dp = 10.77  ✓
    #   1215 / 60 = 20.25      → round 2dp = 20.25  ✓

    _BODY = {
        "plan": {
            "itineraries": [{
                "duration": 4501,
                "transitTime": 2640,
                "walkTime": 646,
                "waitingTime": 1215,
                "transfers": 1,
            }]
        }
    }

    def test_status_ok(self):
        result = _call_get_trip(self._BODY)
        assert result["status"] == "OK"

    def test_duration_matches_csv(self):
        result = _call_get_trip(self._BODY)
        assert result["duration"] == 75.02

    def test_transittime_matches_csv(self):
        result = _call_get_trip(self._BODY)
        assert result["transittime"] == 44.0

    def test_walktime_matches_csv(self):
        result = _call_get_trip(self._BODY)
        assert result["walktime"] == 10.77

    def test_waitingtime_matches_csv(self):
        result = _call_get_trip(self._BODY)
        assert result["waitingtime"] == 20.25

    def test_transfers_is_int(self):
        result = _call_get_trip(self._BODY)
        assert result["transfers"] == 1
        assert isinstance(result["transfers"], int)


# ---------------------------------------------------------------------------
# Error body (e.g. 404 PATH_NOT_FOUND) — row 4 of pop_results2.csv
# ---------------------------------------------------------------------------

class TestErrorBody:
    """OTP /plan error path: numeric id → string status, all time fields None."""

    _BODY_404 = {
        "error": {
            "id": 404,
            "msg": "PATH_NOT_FOUND",
            "noPath": True,
        }
    }

    def test_status_is_string_code(self):
        result = _call_get_trip(self._BODY_404)
        assert result["status"] == "404"

    def test_time_fields_none(self):
        result = _call_get_trip(self._BODY_404)
        for field in ("duration", "transittime", "walktime", "waitingtime", "transfers"):
            assert result[field] is None, f"{field} should be None on 404"

    def test_other_error_codes_preserved(self):
        for code in (406, 409, 410, 440, 450):
            body = {"error": {"id": code, "msg": "SOME_ERROR"}}
            result = _call_get_trip(body)
            assert result["status"] == str(code)

    def test_empty_itineraries_not_found(self):
        body = {"plan": {"itineraries": []}}
        result = _call_get_trip(body)
        assert result["status"] == "NO_ITINERARY"
        assert result["duration"] is None


# ---------------------------------------------------------------------------
# Network failure → OtpClientError raised
# ---------------------------------------------------------------------------

class TestNetworkFailure:
    def test_url_error_raises_otp_client_error(self):
        client = PlanClient()
        with patch(
            "easy_otp.core.plan_client.urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            with pytest.raises(OtpClientError, match="unreachable"):
                client.get_trip(
                    from_lat=0.0, from_lon=0.0, to_lat=1.0, to_lon=1.0,
                    mode="TRANSIT", date_mmddyyyy="01-01-2024", time_hhmmss="08:00:00",
                )

    def test_http_error_raises_otp_client_error(self):
        client = PlanClient()
        http_err = urllib.error.HTTPError(
            url="http://x", code=500, msg="Server Error", hdrs=None, fp=None
        )
        with patch(
            "easy_otp.core.plan_client.urllib.request.urlopen",
            side_effect=http_err,
        ):
            with pytest.raises(OtpClientError, match="HTTP 500"):
                client.get_trip(
                    from_lat=0.0, from_lon=0.0, to_lat=1.0, to_lon=1.0,
                    mode="TRANSIT", date_mmddyyyy="01-01-2024", time_hhmmss="08:00:00",
                )

    def test_non_json_raises_otp_client_error(self):
        bad_resp = MagicMock()
        bad_resp.read.return_value = b"<html>Not JSON</html>"
        bad_resp.__enter__ = lambda s: s
        bad_resp.__exit__ = MagicMock(return_value=False)
        client = PlanClient()
        with patch(
            "easy_otp.core.plan_client.urllib.request.urlopen",
            return_value=bad_resp,
        ):
            with pytest.raises(OtpClientError, match="non-JSON"):
                client.get_trip(
                    from_lat=0.0, from_lon=0.0, to_lat=1.0, to_lon=1.0,
                    mode="TRANSIT", date_mmddyyyy="01-01-2024", time_hhmmss="08:00:00",
                )


# ---------------------------------------------------------------------------
# URL construction sanity check
# ---------------------------------------------------------------------------

class TestUrlConstruction:
    def test_from_and_to_place_format(self):
        """fromPlace and toPlace must be "lat,lon" strings."""
        captured = {}

        def fake_urlopen(url, timeout=None):
            captured["url"] = url
            resp = MagicMock()
            resp.read.return_value = json.dumps(
                {"plan": {"itineraries": [{"duration": 60, "transitTime": 0,
                                           "walkTime": 60, "waitingTime": 0, "transfers": 0}]}}
            ).encode()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        client = PlanClient(hostname="localhost", port=8801, router="myrouter")
        with patch("easy_otp.core.plan_client.urllib.request.urlopen", side_effect=fake_urlopen):
            client.get_trip(
                from_lat=51.807, from_lon=19.323,
                to_lat=51.747, to_lon=19.452,
                mode="TRANSIT",
                date_mmddyyyy="11-22-2024",
                time_hhmmss="08:30:00",
            )

        url = captured["url"]
        assert "fromPlace=51.807%2C19.323" in url or "fromPlace=51.807,19.323" in url
        assert "toPlace=51.747%2C19.452" in url or "toPlace=51.747,19.452" in url
        assert "routerId=myrouter" in url
        assert "numItineraries=1" in url


# ---------------------------------------------------------------------------
# get_trip_via — success, error, and URL construction
# ---------------------------------------------------------------------------

# Canonical Google polyline for [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]
_POLYLINE = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"

_VIA_SUCCESS_BODY = {
    "plan": {
        "itineraries": [{
            "duration": 1800,
            "walkDistance": 3000.0,
            "legs": [
                {
                    "duration": 600,
                    "distance": 800.0,
                    "mode": "WALK",
                    "legGeometry": {"points": _POLYLINE},
                },
                {
                    "duration": 1200,
                    "distance": 2200.0,
                    "mode": "WALK",
                    "legGeometry": {"points": _POLYLINE},
                },
            ],
        }]
    }
}


def _call_get_trip_via(body: dict, intermediate_places=None) -> dict:
    client = PlanClient(hostname="localhost", port=8801, router="default")
    with patch("easy_otp.core.plan_client.urllib.request.urlopen", return_value=_make_resp(body)):
        return client.get_trip_via(
            from_lat=51.0, from_lon=17.0,
            to_lat=51.1, to_lon=17.1,
            intermediate_places=intermediate_places or [],
            mode="WALK",
            date_mmddyyyy="07-02-2026",
            time_hhmmss="10:00:00",
        )


class TestGetTripViaSuccess:
    def test_status_ok(self):
        result = _call_get_trip_via(_VIA_SUCCESS_BODY)
        assert result["status"] == "OK"

    def test_duration_converted_to_minutes(self):
        result = _call_get_trip_via(_VIA_SUCCESS_BODY)
        assert result["duration"] == 30.0

    def test_walk_distance_returned(self):
        result = _call_get_trip_via(_VIA_SUCCESS_BODY)
        assert result["walk_distance_m"] == 3000.0

    def test_legs_count(self):
        result = _call_get_trip_via(_VIA_SUCCESS_BODY)
        assert len(result["legs"]) == 2

    def test_leg_duration_min(self):
        result = _call_get_trip_via(_VIA_SUCCESS_BODY)
        assert result["legs"][0]["duration_min"] == round(600 / 60, 2)

    def test_leg_distance_m(self):
        result = _call_get_trip_via(_VIA_SUCCESS_BODY)
        assert result["legs"][0]["distance_m"] == 800.0

    def test_leg_mode(self):
        result = _call_get_trip_via(_VIA_SUCCESS_BODY)
        assert result["legs"][0]["mode"] == "WALK"

    def test_leg_geometry_decoded(self):
        result = _call_get_trip_via(_VIA_SUCCESS_BODY)
        geom = result["legs"][0]["geometry"]
        assert len(geom) == 3
        assert geom[0] == pytest.approx((38.5, -120.2), abs=1e-3)


class TestGetTripViaError:
    def test_otp_error_status_string(self):
        body = {"error": {"id": 404, "msg": "PATH_NOT_FOUND"}}
        result = _call_get_trip_via(body)
        assert result["status"] == "404"

    def test_otp_error_legs_empty(self):
        body = {"error": {"id": 404, "msg": "PATH_NOT_FOUND"}}
        result = _call_get_trip_via(body)
        assert result["legs"] == []

    def test_otp_error_duration_none(self):
        body = {"error": {"id": 404, "msg": "PATH_NOT_FOUND"}}
        result = _call_get_trip_via(body)
        assert result["duration"] is None

    def test_no_itinerary_status(self):
        body = {"plan": {"itineraries": []}}
        result = _call_get_trip_via(body)
        assert result["status"] == "NO_ITINERARY"
        assert result["legs"] == []


class TestGetTripViaUrlConstruction:
    def _capture_url(self, intermediate_places):
        captured = {}

        def fake_urlopen(url, timeout=None):
            captured["url"] = url
            resp = MagicMock()
            resp.read.return_value = json.dumps(_VIA_SUCCESS_BODY).encode()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        client = PlanClient(hostname="localhost", port=8801, router="r1")
        with patch("easy_otp.core.plan_client.urllib.request.urlopen", side_effect=fake_urlopen):
            client.get_trip_via(
                from_lat=1.0, from_lon=2.0,
                to_lat=3.0, to_lon=4.0,
                intermediate_places=intermediate_places,
                mode="WALK",
                date_mmddyyyy="07-02-2026",
                time_hhmmss="10:00:00",
            )
        return captured["url"]

    def test_no_intermediate_places_omits_param(self):
        url = self._capture_url([])
        assert "intermediatePlaces" not in url

    def test_two_via_points_produces_repeated_params(self):
        url = self._capture_url([(51.1, 17.1), (51.2, 17.2)])
        # doseq=True should produce two separate intermediatePlaces= entries
        decoded = urllib.parse.unquote(url)
        assert decoded.count("intermediatePlaces=") == 2

    def test_via_point_format_lat_comma_lon(self):
        url = self._capture_url([(48.5, 21.3)])
        decoded = urllib.parse.unquote(url)
        assert "intermediatePlaces=48.5,21.3" in decoded

    def test_router_id_present(self):
        url = self._capture_url([])
        assert "routerId=r1" in url
