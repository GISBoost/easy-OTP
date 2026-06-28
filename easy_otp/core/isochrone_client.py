"""REST client for OTP 1.5.0 isochrone endpoint (stdlib urllib only, no QGIS).

Port of `otp_get_isochrone` from the R package otpr (Marcus Young, MIT).
Endpoint: GET /otp/routers/{router}/isochrone
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

from .otp_client import OtpClientError

_TRANSIT_MODES = {"TRANSIT", "BUS", "RAIL", "TRAM", "SUBWAY"}


def _effective_mode(mode: str) -> str:
    """Append ,WALK for transit modes; leave WALK/CAR/BICYCLE bare."""
    upper = mode.upper()
    if upper in _TRANSIT_MODES:
        return f"{upper},WALK"
    return upper


@dataclass
class IsochroneClient:
    hostname: str = "localhost"
    port: int = 8801
    router: str = "default"
    timeout_s: float = 60.0

    @property
    def base_url(self) -> str:
        return f"http://{self.hostname}:{self.port}/otp"

    def get_isochrone(
        self,
        *,
        from_lat: float,
        from_lon: float,
        cutoffs_sec: list[int],
        mode: str,
        date_mmddyyyy: str,
        time_hhmmss: str,
        direction: str = "FROM",
        max_walk_distance: float,
        walk_reluctance: float,
        wait_reluctance: float,
        transfer_penalty: int,
        min_transfer_time: int,
        arrive_by: bool = False,
        timeout_s: Optional[float] = None,
    ) -> str:
        """Call OTP isochrone endpoint; return raw GeoJSON string.

        direction="TO" uses the OTP 1.5 workaround: pass the same location
        in both fromPlace and toPlace (otpr fromLocation=FALSE branch).
        """
        place = f"{from_lat},{from_lon}"
        # Build as a list of (key, value) pairs so cutoffSec can repeat.
        params: list[tuple[str, str]] = [
            ("routerId", self.router),
            ("fromPlace", place),
            ("mode", _effective_mode(mode)),
            ("batch", "true"),
            ("date", date_mmddyyyy),
            ("time", time_hhmmss),
            ("maxWalkDistance", str(max_walk_distance)),
            ("walkReluctance", str(walk_reluctance)),
            ("waitReluctance", str(wait_reluctance)),
            ("arriveBy", "true" if arrive_by else "false"),
            ("transferPenalty", str(transfer_penalty)),
            ("minTransferTime", str(min_transfer_time)),
        ]
        for c in cutoffs_sec:
            params.append(("cutoffSec", str(c)))
        # OTP 1.5 bug: for "arrive-by" isochrone (reach the point) both
        # fromPlace and toPlace must be set to the same location.
        if direction.upper() == "TO":
            params.append(("toPlace", place))

        query = urllib.parse.urlencode(params)
        url = f"{self.base_url}/routers/{self.router}/isochrone?{query}"
        effective_timeout = timeout_s if timeout_s is not None else self.timeout_s
        try:
            with urllib.request.urlopen(url, timeout=effective_timeout) as resp:  # nosec B310
                body = resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:500]
            raise OtpClientError(
                f"OTP isochrone returned HTTP {e.code}: {detail}"
            ) from e
        except (urllib.error.URLError, OSError) as e:
            raise OtpClientError(f"OTP isochrone unreachable: {e}") from e

        text = body.decode("utf-8", errors="replace")
        if '"type":"FeatureCollection"' not in text and '"type": "FeatureCollection"' not in text:
            raise OtpClientError(
                f"OTP isochrone did not return a FeatureCollection: {text[:500]}"
            )
        return text

    @staticmethod
    def parse_isochrones(geojson_str: str) -> list[dict]:
        """Parse OTP isochrone GeoJSON; return list sorted by cutoff_sec asc.

        Each dict: {"cutoff_sec": int, "geometry": <GeoJSON geometry dict>}.
        OTP 1.5 stores the cutoff in seconds as ``properties.time``.
        """
        data = json.loads(geojson_str)
        result = []
        for feature in data.get("features", []):
            cutoff_sec = int(feature["properties"]["time"])
            result.append({
                "cutoff_sec": cutoff_sec,
                "geometry": feature["geometry"],
            })
        result.sort(key=lambda x: x["cutoff_sec"])
        return result
