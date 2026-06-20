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
import re
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
    fuzzy_matching: bool = False,
) -> None:
    """Write ``router-config.json`` with a GTFS-RT updater into ``graph_dir``.

    The file is written to ``graph_dir`` (the OTP router directory,
    e.g. ``graphs/<router_id>/``) — never the base path, or OTP ignores it.

    ``routingDefaults`` is merged in from the shared ``DEFAULT_ROUTER_CONFIG``;
    without it the analyst surface SPT collapses (confirmed 2026-05-25). The
    updater uses the OTP 1.5.0 ``stop-time-updater`` / ``gtfs-http`` tokens.

    When ``fuzzy_matching`` is True, ``"fuzzyTripMatching": true`` is added so OTP
    matches TripUpdates by route/direction/start-time instead of exact trip_id —
    necessary when the static GTFS and the live RT feed are from different editions
    (their trip_ids don't match), which otherwise yields "Applied 0 trip updates".
    ``fuzzyTripMatching`` is an OTP 1.5.0 key (do not carry it to OTP 2.x).
    """
    updater: dict = {
        "type": "stop-time-updater",
        "frequencySec": polling_sec,
        "sourceType": "gtfs-http",
        "url": url,
        "feedId": feed_id,
    }
    if fuzzy_matching:
        updater["fuzzyTripMatching"] = True
    config: dict = {
        "routingDefaults": dict(DEFAULT_ROUTER_CONFIG["routingDefaults"]),
        "updaters": [updater],
    }

    graph_dir.mkdir(parents=True, exist_ok=True)
    (graph_dir / ROUTER_CONFIG_NAME).write_text(
        json.dumps(config, indent=2), encoding="utf-8"
    )


_APPLIED_RE = re.compile(r"Applied (\d+) trip updates")
_NO_PATTERN_RE = re.compile(r"No pattern found for tripId")
_FAILED_APPLY_RE = re.compile(r"Failed to apply TripUpdate")
_NON_INCREASING_RE = re.compile(r"non-increasing|Negative running time")


def summarize_trip_update_log(text: str) -> "tuple[int, int]":
    """Scan an OTP server log for GTFS-RT application results.

    Returns ``(applied_total, skipped_count)`` where ``applied_total`` is the sum of
    all ``Applied N trip updates`` lines and ``skipped_count`` is the number of
    ``No pattern found for tripId`` warnings. A result of ``(0, N>0)`` means the RT
    feed was fetched but none of it matched the loaded graph — the analysis is
    effectively static despite the realtime updater being active.
    """
    applied_total = sum(int(m) for m in _APPLIED_RE.findall(text))
    skipped_count = len(_NO_PATTERN_RE.findall(text))
    return applied_total, skipped_count


def count_rt_polls(text: str) -> int:
    """Number of completed ``Applied N trip updates`` summary lines in an OTP log.

    OTP emits exactly one such line per finished GTFS-RT poll, so a non-zero count
    means at least one poll has completed. The pre-flight guard uses this to avoid
    aborting mid-poll: only when a poll has *finished* with 0 applied (and trips
    were skipped) is the static↔RT edition mismatch conclusive.
    """
    return len(_APPLIED_RE.findall(text))


def summarize_rt_log(text: str) -> dict:
    """Parse an OTP 1.5 server log for GTFS-RT effectiveness signals.

    Caps the input to the last 2 MB before matching (RAM guard for long runs).

    Returns a dict with five integer keys:

    applied_polling  — sum of N from all "Applied N trip updates" lines.
                       In OTP 1.5 TimetableSnapshotSource, this line is ONLY
                       emitted when the poll cycle ends with zero net applications;
                       when any trip applies silently, this line is suppressed.
                       A zero sum therefore does NOT mean "RT had no effect."
    no_pattern       — count of "No pattern found for tripId" occurrences.
                       Each is a trip_id that OTP could not match to the static
                       graph — signals a data-mismatch (different GTFS edition).
    failed_apply     — count of "Failed to apply TripUpdate" occurrences.
                       Fired for both no-pattern and non-increasing-times failures;
                       alone it does not distinguish between the two cases.
    non_increasing   — count of "non-increasing" or "Negative running time"
                       occurrences. Generated by Timetable.java AFTER a successful
                       trip_id match — its presence proves the feed IS matched
                       against the graph, regardless of applied_polling being 0.
    polls            — count of "Applied N trip updates" lines of any N.
                       NOT equal to completed poll cycles for TimetableSnapshotSource
                       (may be 0 even after many polls); kept for diagnostics only.
    """
    tail = text[-2_000_000:]
    return {
        "applied_polling": sum(int(m) for m in _APPLIED_RE.findall(tail)),
        "no_pattern":      len(_NO_PATTERN_RE.findall(tail)),
        "failed_apply":    len(_FAILED_APPLY_RE.findall(tail)),
        "non_increasing":  len(_NON_INCREASING_RE.findall(tail)),
        "polls":           len(_APPLIED_RE.findall(tail)),
    }


def assess_rt_effectiveness(signals: dict, updaters: dict) -> int:
    """Derive the RT effectiveness verdict from log signals and updater REST status.

    Priority-ordered rules (first match wins):

    1. applied_polling > 0                       → 1  (PollingStoptimeUpdater confirmed)
    2. non_increasing > 0                         → 1  (validator rejection only after match)
    3. no_pattern > 0  AND  non_increasing == 0   → 0  (pure data mismatch)
    4. failed_apply > 0  AND  no_pattern == 0     → 1  (failure implies trip_id matched)
    5. updaters non-empty  AND  no_pattern == 0   → 1  (updater live, silent success)
    6. (none of the above)                        → -1 (inconclusive)

    Returns:
       1  — RT confirmed effective
       0  — RT confirmed not effective (data mismatch; drives RT-NOT-APPLIED_ prefix)
      -1  — inconclusive (no confirming signals; no prefix added)
    """
    applied    = signals.get("applied_polling", 0)
    no_pattern = signals.get("no_pattern", 0)
    non_incr   = signals.get("non_increasing", 0)
    failed     = signals.get("failed_apply", 0)

    if applied > 0:
        return 1
    if non_incr > 0:
        return 1
    if no_pattern > 0 and non_incr == 0:
        return 0
    if failed > 0 and no_pattern == 0:
        return 1
    if updaters and no_pattern == 0:
        return 1
    return -1


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
