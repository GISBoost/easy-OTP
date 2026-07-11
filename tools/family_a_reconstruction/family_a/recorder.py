"""Pure helpers for GTFS-RT VehiclePositions snapshot recording (FA-1).

This is an INTENTIONAL DUPLICATE of easy_otp/core/gtfsrt_recorder.py, not an
import: family_a is a standalone tool that must run without the easy-OTP QGIS
plugin (or QGIS itself) installed, so it cannot depend on anything under
easy_otp/. Function names, signatures, and behaviour are kept identical to
RT-2's recorder so that anyone already familiar with RT-2 immediately
understands this code, and so that .pb archives produced by either tool share
the same snapshot_YYYYmmdd-HHMMSS.pb filename format.

Unlike RT-2, this polls a VehiclePositions feed rather than TripUpdates — but
at this layer the recorder is content-agnostic: it just saves whatever raw
bytes the URL returns, so no code difference is needed here.

write_manifest omits RT-2's ``unchanged_streak_max`` parameter: that field
tracks frozen-feed detection (RT3-3), which is out of scope for Family A —
keeping a parameter whose value would always be a hardcoded 0 would be
misleading dead API surface.

No QGIS / GDAL imports. Run tests: pytest tests/test_recorder.py -v
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

import certifi


class SnapshotFetchError(Exception):
    """Raised by ``fetch_snapshot`` on any HTTP or network error."""


_USER_AGENT = "easy-otp-family_a/0.1 (standalone GTFS-RT VehiclePositions recorder)"

# Some city open-data hosts (e.g. Lodz's otwarte.miasto.lodz.pl, which chains
# up to "Certum Trusted Root CA") serve a chain that a plain
# ssl.create_default_context() rejects with CERTIFICATE_VERIFY_FAILED /
# self-signed certificate in certificate chain on Windows: Python's default
# context falls back to the OS root store, and Windows only fetches a root
# into that store lazily (via "Automatic Root Certificates Update") the first
# time a trusted app like a browser needs it - a root can be legitimately
# trusted yet still be absent from a given machine's cache. certifi ships
# Mozilla's actively-maintained root bundle directly, sidestepping that gap
# without weakening verification (verification stays on; this only changes
# which trusted root list is consulted).
_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


def fetch_snapshot(url: str, timeout: int = 15) -> bytes:
    """Fetch a single GTFS-RT snapshot via HTTP GET.

    Sends an explicit User-Agent header: some feed hosts (e.g. the mkuran.pl
    Warszawa mirror) return HTTP 403 for urllib's default
    ``Python-urllib/x.y`` signature specifically, while accepting any other
    User-Agent.

    Returns the raw response body on HTTP 200.
    Raises :exc:`SnapshotFetchError` on non-200 status or any network error.
    """
    try:
        request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(request, timeout=timeout, context=_SSL_CONTEXT) as resp:  # nosec B310
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


def parse_snapshot_filename(name: str) -> datetime | None:
    """Parse a ``snapshot_YYYYmmdd-HHMMSS.pb`` filename back into the naive
    local datetime that :func:`snapshot_filename` encoded it from. Inverse of
    ``snapshot_filename`` — kept beside it so the format is defined once.

    Returns ``None`` (not an exception) for any name not matching the
    pattern, so a caller scanning a directory can skip stray/malformed names
    instead of catching a parse exception per file.

    TIMEZONE NOTE (FA-6): the returned datetime is NAIVE local wall-clock
    time (the recording machine's ``datetime.now()`` at poll time) — NOT
    UTC. Do not run it through zoneinfo/agency-timezone conversion before
    reading off a calendar date; it is already local. This differs
    deliberately from ``segment_stats.py``'s ``day_type_for_date``, which
    starts from a UTC matched-observation timestamp and must convert.
    """
    stem = name[:-3] if name.endswith(".pb") else name
    prefix = "snapshot_"
    if not stem.startswith(prefix):
        return None
    try:
        return datetime.strptime(stem[len(prefix):], "%Y%m%d-%H%M%S")
    except ValueError:
        return None


def earliest_recording_date(snapshot_paths: Iterable[Path]) -> date:
    """Local calendar date of the earliest snapshot among *snapshot_paths*.

    Used by ``match`` (FA-6) to derive a directory's ``recording_date`` from
    its own contents, never from the directory's name (a directory named
    e.g. ``positions_lodz2`` carries no reliable date information).

    Raises :exc:`ValueError` if none of the filenames parse — callers turn
    this into a clean CLI error rather than a raw traceback.
    """
    parsed = [parse_snapshot_filename(p.name) for p in snapshot_paths]
    valid = [dt for dt in parsed if dt is not None]
    if not valid:
        raise ValueError("no snapshot_YYYYmmdd-HHMMSS.pb filenames could be parsed for a date")
    return min(valid).date()


def write_snapshot(directory: Path, data: bytes, dt: datetime) -> Path:
    """Write *data* to *directory* under :func:`snapshot_filename(dt) <snapshot_filename>`.

    Returns the path that was written. *directory* must already exist —
    creating it is the caller's responsibility.
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

    Informational metadata for the archive, consumed by later Family A steps
    (FA-2/FA-3).
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
