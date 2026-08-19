"""Client for gtfs-dashboard's published manifest.json - the online tidy-table catalogue.

Only ever talks to gtfs-dashboard's static GitHub Pages manifest and the Release-asset CDN
URLs it lists inside - never `api.github.com`. Unauthenticated GitHub REST calls are rate
limited to 60 req/hour/IP; gtfs-dashboard's own daily `refresh-manifest.yml` job already pays
that cost once so every `chart_lab` user doesn't have to pay it again per click (PRD §1).
`urllib`, not `requests`: chart_lab isn't PyQGIS-constrained the way the plugin is, but one GET
call doesn't justify a dependency the eventual PyInstaller build would have to bundle.
"""
from __future__ import annotations

import json
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MANIFEST_URL = "https://gisboost.github.io/gtfs-dashboard/manifest.json"

# Persists across app restarts (OS temp dir, not a repo/install path) so re-picking the same
# city-day doesn't re-download every time.
DEFAULT_CACHE_DIR = Path(tempfile.gettempdir()) / "chart_lab_cache"

_TIMEOUT_S = 20


class ManifestError(RuntimeError):
    """A catalogue fetch/parse/download problem a user can be told about - never a raw
    urllib/json/OSError traceback. This runs on an end user's home connection, which will
    sometimes just be offline; that must degrade to a message, not take down the GUI.
    """


@dataclass(frozen=True)
class CityDay:
    city_id: str
    display_name: str
    date: str
    tidy_table_url: str

    @property
    def label(self) -> str:
        return f"{self.display_name} — {self.date}"


def fetch_manifest(url: str = DEFAULT_MANIFEST_URL) -> dict:
    """GET and parse the manifest. Raises ManifestError on any network/HTTP/JSON problem."""
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT_S) as resp:
            status = resp.status
            body = resp.read()
    except urllib.error.HTTPError as exc:
        raise ManifestError(f"{url} returned HTTP {exc.code} ({exc.reason})") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise ManifestError(
            f"could not reach {url} ({exc}) - check your internet connection"
        ) from exc
    if status != 200:
        raise ManifestError(f"{url} returned HTTP {status}")
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"{url} did not return valid JSON ({exc})") from exc


def list_available_city_days(manifest: dict) -> list[CityDay]:
    """Every city-day with a published tidy table.

    A day recorded before 2026-08-03 (when easy-GTFS-RT started publishing this asset) has
    `assets.tidy_table: null` - filtered out here, never shown as a greyed/broken option that
    invites a confusing click. A malformed entry (missing `date`/`assets`) is skipped rather
    than raised on - one bad row in a 400+ row manifest must not blank the whole catalogue.
    """
    out: list[CityDay] = []
    for city_id, city in (manifest.get("cities") or {}).items():
        display_name = city.get("display_name") or city_id
        for day in city.get("days") or []:
            date = day.get("date")
            url = (day.get("assets") or {}).get("tidy_table")
            if date and url:
                out.append(CityDay(city_id=city_id, display_name=display_name,
                                    date=date, tidy_table_url=url))
    return out


def download_tidy_table(url: str, cache_dir: Path = DEFAULT_CACHE_DIR) -> Path:
    """Download a tidy-table Release asset, or reuse an already-cached copy.

    Cached under the URL's own filename (e.g. `boston_tidy_2026-07-27.csv.gz`) so a human
    browsing the cache folder can tell what's in it without opening anything.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    filename = url.rsplit("/", 1)[-1]
    if not filename:
        raise ManifestError(f"can't derive a filename from {url}")
    dest = cache_dir / filename
    if dest.exists() and dest.stat().st_size > 0:
        return dest

    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            status = resp.status
            data = resp.read()
    except urllib.error.HTTPError as exc:
        raise ManifestError(f"download of {url} failed: HTTP {exc.code} ({exc.reason})") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise ManifestError(f"could not download {url} ({exc})") from exc
    if status != 200 or not data:
        raise ManifestError(f"download of {url} did not complete (HTTP {status}, "
                             f"{len(data)} bytes received)")

    # Write to a sibling temp name and rename into place, so a crash mid-download never
    # leaves a truncated file that a later run would wrongly treat as a valid cache hit.
    tmp_dest = dest.with_name(dest.name + ".part")
    tmp_dest.write_bytes(data)
    tmp_dest.replace(dest)
    return dest
