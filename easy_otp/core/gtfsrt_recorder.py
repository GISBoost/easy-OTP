"""Pure helpers for GTFS-RT snapshot recording (RT-2).

No QGIS / GDAL imports — unit-testable without a QGIS environment.
Run tests: py -m pytest easy_otp/test/test_record_gtfs_rt.py -v
"""

import json
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path


def archive_folder_name(feed_id: str, started_at: datetime) -> str:
    """Return a timestamped subfolder name for one recording session.

    Examples:
      feed_id="gdansk"  → ``gtfsrt_gdansk_20260621-183004``
      feed_id=""        → ``gtfsrt_recording_20260621-183004``
    """
    import re
    ts = started_at.strftime("%Y%m%d-%H%M%S")
    if feed_id:
        safe = re.sub(r"[^a-zA-Z0-9_-]", "_", feed_id)
        return f"gtfsrt_{safe}_{ts}"
    return f"gtfsrt_recording_{ts}"


class SnapshotFetchError(Exception):
    """Raised by ``fetch_snapshot`` on any HTTP or network error."""


def fetch_snapshot(url: str, timeout: int = 15) -> bytes:
    """Fetch a single GTFS-RT snapshot via HTTP GET.

    Returns the raw response body on HTTP 200.
    Raises :exc:`SnapshotFetchError` on non-200 status or any network error.
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # nosec B310
            status = getattr(resp, "status", None) or resp.getcode()
            if status != 200:
                raise SnapshotFetchError(f"HTTP {status}")
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise SnapshotFetchError(f"HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise SnapshotFetchError(f"Connection failed: {exc.reason}") from exc
    except SnapshotFetchError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SnapshotFetchError(f"Request failed: {exc}") from exc


def snapshot_filename(dt: datetime) -> str:
    """Return the canonical filename for a snapshot taken at *dt*.

    Format: ``snapshot_YYYYmmdd-HHMMSS.pb``.  Including the date prevents
    data loss when a recording spans midnight or runs on multiple days.
    """
    return dt.strftime("snapshot_%Y%m%d-%H%M%S.pb")


def write_snapshot(directory: Path, data: bytes, dt: datetime) -> Path:
    """Write *data* to *directory* under :func:`snapshot_filename(dt) <snapshot_filename>`.

    Returns the path that was written.
    """
    path = directory / snapshot_filename(dt)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def write_manifest(
    directory: Path,
    url: str,
    feed_id: str,
    interval: int,
    started_at: datetime,
    stopped_at: datetime,
    snapshot_count: int,
    failed_count: int,
    total_bytes: int,
) -> None:
    """Write (or overwrite) ``recording.json`` in *directory*.

    Informational metadata for the archive.  RT-3 does not read this file
    currently; all required parameters are entered directly in BuildRealizedGtfs.
    """
    manifest = {
        "url": url,
        "feed_id": feed_id,
        "sampling_interval_sec": interval,
        "started_at": started_at.isoformat(),
        "stopped_at": stopped_at.isoformat(),
        "snapshot_count": snapshot_count,
        "failed_count": failed_count,
        "total_bytes": total_bytes,
    }
    path = directory / "recording.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
