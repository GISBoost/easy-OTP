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
from typing import Callable, Optional

LogFn = Callable[[str], None]


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

    def get_feed_ids(self) -> list:
        """Return the feed IDs OTP actually loaded for this router.

        Uses the OTP 1.5 index API (``/index/feeds``), which returns a JSON array
        of feedId strings. RunRealtimeAccessibility logs these so the user can set
        ``GTFS_RT_FEED_ID`` to a value OTP recognises — a mismatch makes OTP
        silently ignore the GTFS-RT feed. Raises OtpClientError on transport
        failure or unexpected payload.
        """
        url = f"{self.base_url}/routers/{self.router}/index/feeds"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout_s) as resp:  # nosec B310
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise OtpClientError(f"GET {url} returned HTTP {e.code}") from e
        except (urllib.error.URLError, OSError) as e:
            raise OtpClientError(f"GET {url} unreachable: {e}") from e
        except json.JSONDecodeError as e:
            raise OtpClientError(f"GET {url} returned non-JSON") from e
        if not isinstance(data, list):
            raise OtpClientError(f"GET {url} returned non-list payload: {data!r}")
        return data

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
        arrive_by: bool = False,
        mode: str = "TRANSIT,WALK",
        log_fn: Optional[LogFn] = None,
    ) -> int:
        # OTP expects fromPlace="lat,lon". Caller passes (lat, lon).
        # routerId MUST be sent: SurfaceResource defaults to "default" router
        # when omitted, and our server runs with --router <sha256>.
        _place = f"{from_place_lat_lon[0]},{from_place_lat_lon[1]}"
        params = {
            "routerId": self.router,
            "fromPlace": _place,
            "mode": mode,
            "date": date_mmddyyyy,
            "time": time_hhmmss,
            "maxWalkDistance": max_walk_distance,
            "walkReluctance": walk_reluctance,
            "waitReluctance": wait_reluctance,
            "transferPenalty": transfer_penalty,
            "minTransferTime": min_transfer_time,
            "walkSpeed": walk_speed,
            "arriveBy": "true" if arrive_by else "false",
            "batch": "true",
        }
        # OTP 1.5.0 buildRequest() dereferences toPlace when arriveBy=true;
        # without it the code throws NullPointerException. Mirror fromPlace.
        if arrive_by:
            params["toPlace"] = _place
        url = f"{self.base_url}/surfaces?{urllib.parse.urlencode(params)}"
        if log_fn is not None:
            log_fn(f"POST {url}")
        req = urllib.request.Request(url, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:  # nosec B310
                body = resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise OtpClientError(
                    "OTP /surfaces returned HTTP 404: analyst surfaces endpoint "
                    "not found. Make sure the server was started with "
                    "--analyst --pointSets flags."
                ) from e
            if e.code == 500:
                detail = e.read().decode("utf-8", errors="replace")[:1000]
                arrive_hint = (
                    "\nNote: arriveBy=true (reverse routing) may require more heap "
                    "than forward routing — restart with KEEP_SERVER_ALIVE=False to "
                    "get a fresh server with the current OTP_XMX_SERVE setting."
                    if arrive_by else
                    "\nIf this is a memory error, increase OTP_XMX_SERVE and restart "
                    "the server (set KEEP_SERVER_ALIVE=False for one run)."
                )
                raise OtpClientError(
                    f"OTP /surfaces returned HTTP 500 (Internal Server Error).{arrive_hint}\n"
                    f"OTP detail: {detail if detail else '(empty response body)'}"
                ) from e
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
        surface_id = int(data["id"])
        if log_fn is not None:
            log_fn(f"OTP returned surface_id={surface_id}, full response: {data!r}")
        return surface_id

    def download_surface_raster(
        self,
        surface_id: int,
        output_path: Path,
        timeout_s: Optional[float] = None,
        log_fn: Optional[LogFn] = None,
    ) -> None:
        # routerId is defensive — some OTP versions key surfaces per-router.
        # Bigger timeout default than the class field: analyst raster
        # computation for a large city can take a couple of minutes.
        url = (
            f"{self.base_url}/surfaces/{surface_id}/raster"
            f"?routerId={urllib.parse.quote(self.router)}"
        )
        if log_fn is not None:
            log_fn(f"GET {url}")
        effective_timeout = timeout_s if timeout_s is not None else self.timeout_s
        try:
            with urllib.request.urlopen(url, timeout=effective_timeout) as resp, open(output_path, "wb") as fh:  # nosec B310
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
            with urllib.request.urlopen(url, timeout=self.timeout_s) as resp:  # nosec B310
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
