"""GTFS-RT router-config generation for RunRealtimeAccessibility (RT-1).

OTP 1.5.0 reads ``router-config.json`` from the router directory
(``graphs/<router_id>/``) at server start. To inject live GTFS-RT TripUpdates,
this module writes a config that adds a ``stop-time-updater`` (sourceType
``gtfs-http``) alongside the analyst ``routingDefaults``.

OTP 1.5.0 only: the ``stop-time-updater`` / ``gtfs-http`` tokens are NOT
compatible with OTP 2.x. The ``feedId`` must match ``feed_id`` from the static
GTFS ``feed_info.txt`` exactly, or OTP silently ignores the RT feed.

Pure stdlib — no QGIS / GDAL dependency, so it can be unit-tested directly.
"""

from __future__ import annotations

import csv
import io
import json
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from .otp_server import DEFAULT_ROUTER_CONFIG

ROUTER_CONFIG_NAME = "router-config.json"
RT_URL_TIMEOUT_SEC = 10


def write_router_config(
    graph_dir: Path,
    url: str,
    feed_id: str,
    polling_sec: int,
) -> None:
    """Write ``router-config.json`` with a GTFS-RT updater into ``graph_dir``.

    The file is written to ``graph_dir`` (the OTP router directory,
    e.g. ``graphs/<router_id>/``) — never the base path, or OTP ignores it.

    ``routingDefaults`` is merged in from the shared ``DEFAULT_ROUTER_CONFIG``;
    without it the analyst surface SPT collapses (confirmed 2026-05-25). The
    updater uses the OTP 1.5.0 ``stop-time-updater`` / ``gtfs-http`` tokens.
    """
    config: dict = {
        "routingDefaults": dict(DEFAULT_ROUTER_CONFIG["routingDefaults"]),
        "updaters": [
            {
                "type": "stop-time-updater",
                "frequencySec": polling_sec,
                "sourceType": "gtfs-http",
                "url": url,
                "feedId": feed_id,
            }
        ],
    }

    graph_dir.mkdir(parents=True, exist_ok=True)
    (graph_dir / ROUTER_CONFIG_NAME).write_text(
        json.dumps(config, indent=2), encoding="utf-8"
    )


def suggest_feed_id(gtfs_zip_path: str) -> str | None:
    """Return the ``feed_id`` from ``feed_info.txt`` inside a GTFS zip, or None.

    Returns None if the archive has no ``feed_info.txt``, the file has no
    ``feed_id`` column, or there is no non-empty value in the first data row.
    """
    with zipfile.ZipFile(gtfs_zip_path) as zf:
        if "feed_info.txt" not in zf.namelist():
            return None
        raw = zf.read("feed_info.txt")

    # utf-8-sig strips a leading BOM, common in agency-produced GTFS files.
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    row = next(reader, None)
    if row is None:
        return None
    value = (row.get("feed_id") or "").strip()
    return value or None


def validate_rt_url(url: str) -> tuple[bool, str]:
    """Probe an RT feed URL with an HTTP GET (10 s timeout).

    Returns ``(True, "")`` on HTTP 200, otherwise ``(False, message)``.
    Only ``http``/``https`` URLs are accepted; other schemes (e.g. ``file``,
    ``ftp``) are rejected without a network call.
    """
    if urllib.parse.urlparse(url).scheme not in ("http", "https"):
        return (False, "URL must use http or https")
    try:
        with urllib.request.urlopen(url, timeout=RT_URL_TIMEOUT_SEC) as resp:  # nosec B310
            status = getattr(resp, "status", None) or resp.getcode()
            if status == 200:
                return (True, "")
            return (False, f"HTTP {status}")
    except urllib.error.HTTPError as exc:
        return (False, f"HTTP {exc.code}")
    except urllib.error.URLError as exc:
        return (False, f"Connection failed: {exc.reason}")
    except Exception as exc:  # noqa: BLE001 - report any probe failure to the user
        return (False, f"Request failed: {exc}")
