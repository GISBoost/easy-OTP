"""REST client for OpenTripPlanner 1.5.0 (uses only stdlib urllib).

Port of the relevant calls from the R package otpr (Marcus Young, MIT):
- POST /otp/surfaces?...                  create_surface()
- GET  /otp/surfaces/{id}/raster          download_surface_raster()
- GET  /otp                               get_version() / used as liveness probe
- GET  /otp/routers/{router}              get_router_info() / used as readiness probe

OTP returns travel-time rasters in MINUTES with a hardcoded ceiling of 120
(see PR section 7); unreachable cells get the value 120 and are
indistinguishable from "reachable in >=120 min".
"""

from __future__ import annotations

import json
import shutil
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class OtpClientError(Exception):
    """Raised when the OTP REST API returns an error or is unreachable."""


@dataclass
class OtpClient:
    hostname: str = "localhost"
    port: int = 8801
    router: str = "default"
    timeout_s: float = 60.0

    @property
    def base_url(self) -> str:
        return f"http://{self.hostname}:{self.port}/otp"

    def get_version(self) -> dict:
        return self._get_json(self.base_url)

    def get_router_info(self) -> dict:
        return self._get_json(f"{self.base_url}/routers/{self.router}")

    def create_surface(
        self,
        *,
        from_place_lat_lon: tuple[float, float],
        date_mmddyyyy: str,
        time_hhmmss: str,
        max_walk_distance: float,
        walk_reluctance: float,
        wait_reluctance: float,
        transfer_penalty: int,
        min_transfer_time: int,
        walk_speed: float,
        mode: str = "TRANSIT",
    ) -> int:
        # OTP expects fromPlace="lat,lon". Caller passes (lat, lon).
        params = {
            "fromPlace": f"{from_place_lat_lon[0]},{from_place_lat_lon[1]}",
            "mode": mode,
            "date": date_mmddyyyy,
            "time": time_hhmmss,
            "maxWalkDistance": max_walk_distance,
            "walkReluctance": walk_reluctance,
            "waitReluctance": wait_reluctance,
            "transferPenalty": transfer_penalty,
            "minTransferTime": min_transfer_time,
            "walkSpeed": walk_speed,
            "arriveBy": "false",
            "batch": "true",
        }
        url = f"{self.base_url}/surfaces?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                body = resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:500]
            raise OtpClientError(
                f"OTP /surfaces returned HTTP {e.code}: {detail}"
            ) from e
        except (urllib.error.URLError, OSError) as e:
            raise OtpClientError(f"OTP /surfaces unreachable: {e}") from e
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            raise OtpClientError(
                f"OTP /surfaces returned non-JSON: {body[:200]!r}"
            ) from e
        if "id" not in data:
            raise OtpClientError(
                f"OTP /surfaces response missing 'id' field: {data!r}"
            )
        return int(data["id"])

    def download_surface_raster(self, surface_id: int, output_path: Path) -> None:
        url = f"{self.base_url}/surfaces/{surface_id}/raster"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout_s) as resp, open(output_path, "wb") as fh:
                shutil.copyfileobj(resp, fh)
        except urllib.error.HTTPError as e:
            raise OtpClientError(
                f"OTP raster download for surface {surface_id} failed with HTTP {e.code}"
            ) from e
        except (urllib.error.URLError, OSError) as e:
            raise OtpClientError(
                f"OTP raster download for surface {surface_id} unreachable: {e}"
            ) from e

    def _get_json(self, url: str) -> dict:
        try:
            with urllib.request.urlopen(url, timeout=self.timeout_s) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise OtpClientError(f"GET {url} returned HTTP {e.code}") from e
        except (urllib.error.URLError, OSError) as e:
            raise OtpClientError(f"GET {url} unreachable: {e}") from e
        except json.JSONDecodeError as e:
            raise OtpClientError(f"GET {url} returned non-JSON") from e


def probe_otp(port: int, timeout_s: float = 2.0) -> Optional[dict]:
    """If an OTP server responds on `port`, return its /otp JSON; else None."""
    try:
        return OtpClient(port=port, timeout_s=timeout_s).get_version()
    except OtpClientError:
        return None
