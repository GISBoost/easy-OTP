"""REST client for the OTP 1.5.0 /plan endpoint (point-to-point routing).

Port of otpr::otp_get_times (Marcus Young, MIT):
  GET /otp/routers/{router}/plan

OTP error id codes (numeric in JSON, stored as strings in the result dict):
  404  PATH_NOT_FOUND      — no route found within constraints
  406  NO_TRANSIT_TIMES    — transit does not run at the requested time/date
  409  TOO_CLOSE           — origin and destination are too close
  410  OUTSIDE_BOUNDS      — point is outside the graph coverage area
  440  GEOCODE_FROM_NOT_FOUND — origin could not be snapped to the network
  450  GEOCODE_TO_NOT_FOUND   — destination could not be snapped to the network

Deterministic errors (404, 406, 409, 410, 440, 450) are recorded and not retried.
Network/transport failures raise OtpClientError; the caller decides whether to abort.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

from .otp_client import OtpClientError


def decode_polyline(encoded: str, precision: int = 5) -> list[tuple[float, float]]:
    """Decode a Google Encoded Polyline string into (lat, lon) pairs.

    Implements the standard algorithm at precision-5 (the OTP default).
    Empty string returns []. Pure Python, no dependencies.
    """
    if not encoded:
        return []
    factor = 10 ** precision
    result: list[tuple[float, float]] = []
    index = 0
    lat = 0
    lon = 0
    n = len(encoded)
    while index < n:
        for is_lon in range(2):
            shift = 0
            result_bits = 0
            while True:
                chunk = ord(encoded[index]) - 63
                index += 1
                result_bits |= (chunk & 0x1F) << shift
                shift += 5
                if chunk < 0x20:
                    break
            delta = ~(result_bits >> 1) if (result_bits & 1) else (result_bits >> 1)
            if is_lon == 0:
                lat += delta
            else:
                lon += delta
        result.append((lat / factor, lon / factor))
    return result


@dataclass
class PlanClient:
    """Client for the OTP 1.5.0 /plan (route-planning) endpoint."""

    hostname: str = "localhost"
    port: int = 8801
    router: str = "default"
    timeout_s: float = 30.0

    @property
    def _base(self) -> str:
        return f"http://{self.hostname}:{self.port}/otp/routers/{self.router}/plan"

    def get_trip(
        self,
        *,
        from_lat: float,
        from_lon: float,
        to_lat: float,
        to_lon: float,
        mode: str,
        date_mmddyyyy: str,
        time_hhmmss: str,
        max_walk_distance: float = 800.0,
        walk_reluctance: float = 3.0,
        wait_reluctance: float = 2.0,
        transfer_penalty: int = 60,
        min_transfer_time: int = 600,
        arrive_by: bool = False,
        timeout_s: Optional[float] = None,
    ) -> dict:
        """Query OTP for the best route from one point to another.

        Returns a dict with keys:
          status       — "OK" on success, or numeric error code as string (e.g. "404")
          duration     — total trip time in decimal minutes (None on error)
          transittime  — time aboard transit vehicles in decimal minutes (None on error)
          walktime     — walking time in decimal minutes (None on error)
          waitingtime  — time waiting for transit in decimal minutes (None on error)
          transfers    — number of transfers as int (None on error)

        All time values are OTP seconds divided by 60, rounded to 2 decimal places.
        This matches the schema and units of docs/gisboostgithub/pop_results2.csv.

        Raises OtpClientError on network/transport failure (timeout, connection refused,
        non-JSON response). Deterministic OTP errors (404, 406, 409, 410, 440, 450) are
        returned as {"status": "<code>", ...None} and are not retried.
        """
        params = {
            "routerId": self.router,
            "fromPlace": f"{from_lat},{from_lon}",
            "toPlace": f"{to_lat},{to_lon}",
            "mode": mode,
            "date": date_mmddyyyy,
            "time": time_hhmmss,
            "maxWalkDistance": max_walk_distance,
            "walkReluctance": walk_reluctance,
            "waitReluctance": wait_reluctance,
            "transferPenalty": transfer_penalty,
            "minTransferTime": min_transfer_time,
            "arriveBy": "true" if arrive_by else "false",
            "numItineraries": 1,
        }
        url = f"{self._base}?{urllib.parse.urlencode(params)}"
        effective_timeout = timeout_s if timeout_s is not None else self.timeout_s
        try:
            with urllib.request.urlopen(url, timeout=effective_timeout) as resp:  # nosec B310
                body = resp.read()
        except urllib.error.HTTPError as e:
            raise OtpClientError(
                f"OTP /plan returned HTTP {e.code} for {from_lat},{from_lon} → {to_lat},{to_lon}"
            ) from e
        except (urllib.error.URLError, OSError) as e:
            raise OtpClientError(
                f"OTP /plan unreachable: {e}"
            ) from e

        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            raise OtpClientError(
                f"OTP /plan returned non-JSON: {body[:200]!r}"
            ) from e

        return self._parse(data)

    def get_trip_via(
        self,
        *,
        from_lat: float,
        from_lon: float,
        to_lat: float,
        to_lon: float,
        intermediate_places: list[tuple[float, float]],
        mode: str = "WALK",
        date_mmddyyyy: str,
        time_hhmmss: str,
        max_walk_distance: float = 50000.0,
        walk_reluctance: float = 3.0,
        timeout_s: Optional[float] = None,
    ) -> dict:
        """Query OTP for a multi-point route with optional via-points.

        Sends ``intermediatePlaces`` as repeated query parameters (doseq=True)
        in the computed visit order. Returns legs with decoded geometry.

        Returns a dict with keys:
          status         — "OK" on success, error code string on failure
          duration       — total trip time in decimal minutes (None on error)
          walk_distance_m — total walk distance in metres (None on error)
          legs           — list of per-leg dicts (empty on error):
            duration_min — leg duration in decimal minutes
            distance_m   — leg distance in metres
            mode         — leg transport mode string
            geometry     — list of (lat, lon) tuples decoded from legGeometry

        Raises OtpClientError on network/transport failure.
        """
        params: dict = {
            "routerId": self.router,
            "fromPlace": f"{from_lat},{from_lon}",
            "toPlace": f"{to_lat},{to_lon}",
            "mode": mode,
            "date": date_mmddyyyy,
            "time": time_hhmmss,
            "maxWalkDistance": max_walk_distance,
            "walkReluctance": walk_reluctance,
            "numItineraries": 1,
        }
        if intermediate_places:
            params["intermediatePlaces"] = [
                f"{lat},{lon}" for lat, lon in intermediate_places
            ]
        url = f"{self._base}?{urllib.parse.urlencode(params, doseq=True)}"
        effective_timeout = timeout_s if timeout_s is not None else self.timeout_s
        try:
            with urllib.request.urlopen(url, timeout=effective_timeout) as resp:  # nosec B310
                body = resp.read()
        except urllib.error.HTTPError as e:
            raise OtpClientError(
                f"OTP /plan returned HTTP {e.code} for via-point route"
            ) from e
        except (urllib.error.URLError, OSError) as e:
            raise OtpClientError(f"OTP /plan unreachable: {e}") from e

        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            raise OtpClientError(
                f"OTP /plan returned non-JSON: {body[:200]!r}"
            ) from e

        return self._parse_via(data)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _null_result(status: str) -> dict:
        return {
            "status": status,
            "duration": None,
            "transittime": None,
            "walktime": None,
            "waitingtime": None,
            "transfers": None,
        }

    @staticmethod
    def _parse(data: dict) -> dict:
        """Parse the OTP /plan JSON response into a normalised result dict."""
        # OTP returns an error object when no route is found or input is invalid.
        if "error" in data and data["error"]:
            error_id = data["error"].get("id", "UNKNOWN")
            return PlanClient._null_result(str(error_id))

        # Success path: take the first (best) itinerary.
        try:
            itin = data["plan"]["itineraries"][0]
        except (KeyError, IndexError, TypeError):
            # plan object present but empty itineraries — treat as not found.
            return PlanClient._null_result("NO_ITINERARY")

        return {
            "status":      "OK",                                # noqa: E241
            "duration":    round(itin["duration"] / 60, 2),    # noqa: E241
            "transittime": round(itin["transitTime"] / 60, 2),
            "walktime":    round(itin["walkTime"] / 60, 2),    # noqa: E241
            "waitingtime": round(itin["waitingTime"] / 60, 2),
            "transfers":   int(itin["transfers"]),              # noqa: E241
        }

    @staticmethod
    def _parse_via(data: dict) -> dict:
        """Parse an OTP /plan response into a legs-aware result dict."""
        _no_legs: dict = {"status": "", "duration": None, "walk_distance_m": None, "legs": []}

        if "error" in data and data["error"]:
            error_id = data["error"].get("id", "UNKNOWN")
            return {**_no_legs, "status": str(error_id)}

        try:
            itin = data["plan"]["itineraries"][0]
        except (KeyError, IndexError, TypeError):
            return {**_no_legs, "status": "NO_ITINERARY"}

        raw_legs = itin.get("legs") or []
        legs = []
        for raw_leg in raw_legs:
            encoded = (raw_leg.get("legGeometry") or {}).get("points", "")
            legs.append({
                "duration_min": round(raw_leg.get("duration", 0) / 60, 2),
                "distance_m":   round(raw_leg.get("distance", 0), 1),
                "mode":         raw_leg.get("mode", "WALK"),
                "geometry":     decode_polyline(encoded) if encoded else [],
            })
        return {
            "status":         "OK",
            "duration":       round(itin.get("duration", 0) / 60, 2),
            "walk_distance_m": round(itin.get("walkDistance", 0), 1),
            "legs":           legs,
        }
