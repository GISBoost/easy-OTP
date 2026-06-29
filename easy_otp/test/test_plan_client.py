"""Unit tests for PlanClient — no network, no QGIS required."""

import json
import urllib.error
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
