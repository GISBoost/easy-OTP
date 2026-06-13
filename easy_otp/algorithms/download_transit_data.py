"""DownloadTransitData: auto-download OSM extract from Geofabrik + GTFS feeds from Transitland (R2)."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import shutil
import time
import zipfile
from pathlib import Path
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError

from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingOutputString,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterFile,
    QgsProcessingParameterString,
)

_GEOFABRIK_INDEX_URL = "https://download.geofabrik.de/index-v1.json"
_TRANSITLAND_FEEDS_URL = "https://transit.land/api/v2/rest/feeds"
_USER_AGENT = "easy-OTP/0.2"
_CHUNK_SIZE = 64 * 1024         # 64 KB download blocks
_HASH_CHUNK = 1 * 1024 * 1024   # 1 MB MD5 blocks
_INDEX_TTL_DAYS = 7
_OSM_CACHE_DAYS = 7
_MIN_FREE_MB_OSM = 300
_MIN_FREE_MB_GTFS = 100
_MAX_BBOX_DEG2 = 20.0    # ~largest voivodeship ≈ 9 deg²; whole Poland ≈ 63 deg²
_MAX_GTFS_FEEDS = 75
_REQUIRED_GTFS = {"agency.txt", "stops.txt", "routes.txt", "trips.txt", "stop_times.txt"}
_GTFS_CALENDAR = {"calendar.txt", "calendar_dates.txt"}


# ---------------------------------------------------------------------------
# Module-level helpers (specified verbatim in R2-spike-addendum.md)
# ---------------------------------------------------------------------------

def _bbox_from_geometry(geometry: dict) -> tuple[float, float, float, float]:
    """Derive (lon_min, lat_min, lon_max, lat_max) from GeoJSON MultiPolygon."""
    lons, lats = [], []
    for polygon in geometry["coordinates"]:
        for ring in polygon:
            for lon, lat in ring:
                lons.append(lon)
                lats.append(lat)
    return min(lons), min(lats), max(lons), max(lats)


def _is_local_feed(
    feed: dict,
    query_bbox: tuple[float, float, float, float],
    max_area_ratio: float = 5.0,
) -> bool:
    """Return True if the feed's geometry is not vastly larger than the query bbox.

    max_area_ratio=5.0 keeps feeds up to 5× the query area (sub-regional and
    city-level feeds) and discards national/continental aggregates.
    """
    feed_version = (feed.get("feed_state") or {}).get("feed_version") or {}
    geom = feed_version.get("geometry")
    if not geom or not geom.get("coordinates"):
        return True  # no geometry info — keep conservatively

    # flatten first ring of first polygon to get approximate extent
    coords = geom["coordinates"]
    ring = coords[0] if coords else []
    # handle both Polygon (list of [lon,lat]) and nested MultiPolygon
    if ring and isinstance(ring[0], list) and isinstance(ring[0][0], list):
        ring = ring[0]  # unwrap one level for MultiPolygon
    lons = [pt[0] for pt in ring if isinstance(pt, (list, tuple)) and len(pt) >= 2]
    lats = [pt[1] for pt in ring if isinstance(pt, (list, tuple)) and len(pt) >= 2]
    if not lons:
        return True

    feed_area = (max(lons) - min(lons)) * (max(lats) - min(lats))
    qlon_min, qlat_min, qlon_max, qlat_max = query_bbox
    query_area = (qlon_max - qlon_min) * (qlat_max - qlat_min)
    if query_area < 1e-9:
        return True

    return (feed_area / query_area) <= max_area_ratio


# ---------------------------------------------------------------------------
# Algorithm
# ---------------------------------------------------------------------------

class DownloadTransitData(QgsProcessingAlgorithm):
    AREA_NAME = "AREA_NAME"
    DEST_DIR = "DEST_DIR"
    DOWNLOAD_OSM = "DOWNLOAD_OSM"
    DOWNLOAD_GTFS = "DOWNLOAD_GTFS"
    GTFS_API_KEY = "GTFS_API_KEY"

    OUTPUT_OSM = "OUTPUT_OSM"
    OUTPUT_GTFS_DIR = "OUTPUT_GTFS_DIR"

    def tr(self, string: str) -> str:
        return QCoreApplication.translate("Processing", string)

    def name(self) -> str:
        return "downloadtransitdata"

    def displayName(self) -> str:  # noqa: N802
        return self.tr("Download transit data (OSM + GTFS)")

    def group(self) -> str:
        return self.tr("Setup")

    def groupId(self) -> str:  # noqa: N802
        return "setup"

    def shortHelpString(self) -> str:  # noqa: N802
        return self.tr(
            "Downloads the two data inputs required by Run temporal accessibility:\n\n"
            "• An OSM extract (.osm.pbf) from Geofabrik for the named area\n"
            "• GTFS feed(s) from Transitland v2 for the same area\n\n"
            "Use the DOWNLOAD_OSM / DOWNLOAD_GTFS checkboxes to download only what "
            "you need — skip OSM if you already have a local .osm.pbf, or skip GTFS "
            "if you already have a local feed folder.\n\n"
            "OSM data (Geofabrik): https://download.geofabrik.de — licence ODbL\n"
            "GTFS data (Transitland): https://www.transit.land — licences vary by operator\n\n"
            "Expected download sizes: OSM 50–500 MB per voivodeship, "
            "GTFS 5–20 MB total for a metropolitan area.\n\n"
            "GTFS_API_KEY: a free Transitland API key is required to download GTFS. "
            "Sign up at https://www.transit.land — no credit card needed.\n\n"
            "OSM extract is cached for 7 days: running the algorithm a second time "
            "on the same DEST_DIR skips the OSM download. GTFS feeds are always "
            "refreshed (schedules change without a fixed cycle)."
        )

    def createInstance(self):  # noqa: N802
        return DownloadTransitData()

    def initAlgorithm(self, config=None):  # noqa: N802
        self.addParameter(
            QgsProcessingParameterString(
                self.AREA_NAME,
                self.tr("Area name (Geofabrik id or name)"),
            )
        )
        self.addParameter(
            QgsProcessingParameterFile(
                self.DEST_DIR,
                self.tr("Destination folder"),
                behavior=QgsProcessingParameterFile.Folder,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.DOWNLOAD_OSM,
                self.tr("Download OSM extract (.osm.pbf) from Geofabrik"),
                defaultValue=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.DOWNLOAD_GTFS,
                self.tr("Download GTFS feed(s) from Transitland"),
                defaultValue=True,
            )
        )

        gtfs_key_param = QgsProcessingParameterString(
            self.GTFS_API_KEY,
            self.tr("Transitland API key (required for GTFS download)"),
            defaultValue="",
            optional=True,
        )
        self.addParameter(gtfs_key_param)

        self.addOutput(QgsProcessingOutputString(self.OUTPUT_OSM, self.tr("OSM extract path")))
        self.addOutput(QgsProcessingOutputString(self.OUTPUT_GTFS_DIR, self.tr("GTFS folder path")))

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        area_name = self.parameterAsString(parameters, self.AREA_NAME, context).strip()
        dest = Path(self.parameterAsFile(parameters, self.DEST_DIR, context))
        download_osm = self.parameterAsBool(parameters, self.DOWNLOAD_OSM, context)
        download_gtfs = self.parameterAsBool(parameters, self.DOWNLOAD_GTFS, context)
        api_key = (self.parameterAsString(parameters, self.GTFS_API_KEY, context) or "").strip()

        # Step 0 — validate
        if not download_osm and not download_gtfs:
            raise QgsProcessingException(self.tr(
                "Nothing to download. Enable at least one of DOWNLOAD_OSM / DOWNLOAD_GTFS."
            ))
        self._check_writable(dest)
        est_mb = _MIN_FREE_MB_OSM if download_osm else 0
        est_mb += _MIN_FREE_MB_GTFS if download_gtfs else 0
        self._check_disk(dest, est_mb)

        # Determine progress ranges
        if download_osm and download_gtfs:
            osm_p_end = 40
            gtfs_p_start, gtfs_p_end = 40, 100
        elif download_osm:
            osm_p_end = 90
            gtfs_p_start = gtfs_p_end = 0
        else:
            osm_p_end = 0
            gtfs_p_start, gtfs_p_end = 0, 90

        # Step A1 — Geofabrik index (always — needed for bbox even if OSM skipped)
        feedback.pushInfo(self.tr("Loading Geofabrik index …"))
        features = self._load_index(dest, feedback)

        # Step A2 — find area + bbox
        matched = self._find_area(features, area_name)
        area_id = matched["properties"]["id"]
        # area_id may contain path separators (e.g. "europe/poland/dolnoslaskie");
        # use only the last segment as the filename stem
        area_slug = area_id.split("/")[-1]
        urls = matched["properties"].get("urls") or {}
        pbf_url = urls.get("pbf")
        if download_osm and not pbf_url:
            raise QgsProcessingException(self.tr(
                f"Area '{area_id}' has no .osm.pbf download link in Geofabrik. "
                "Try a more specific region (e.g. a voivodeship instead of the whole country)."
            ))
        bbox = _bbox_from_geometry(matched["geometry"])
        lon_min, lat_min, lon_max, lat_max = bbox
        feedback.pushInfo(self.tr(
            f"Found area: '{area_id}'  bbox: "
            f"[{lon_min:.3f}, {lat_min:.3f}, {lon_max:.3f}, {lat_max:.3f}]"
        ))
        bbox_area = (lon_max - lon_min) * (lat_max - lat_min)
        if bbox_area > _MAX_BBOX_DEG2:
            raise QgsProcessingException(self.tr(
                f"Area '{area_id}' covers {bbox_area:.1f} deg² and is too large for "
                f"practical routing (limit: {_MAX_BBOX_DEG2} deg²). "
                "Use a sub-regional area such as a voivodeship — "
                "e.g. 'dolnoslaskie' instead of 'poland'."
            ))

        # Steps A3 + A4 — OSM download + MD5
        osm_dir = dest / "osm"
        osm_dir.mkdir(parents=True, exist_ok=True)
        osm_path = osm_dir / f"{area_slug}.osm.pbf"

        if download_osm:
            if (osm_path.exists()
                    and (time.time() - osm_path.stat().st_mtime) < _OSM_CACHE_DAYS * 86400):
                feedback.pushInfo(self.tr(f"Using cached OSM extract for '{area_id}'."))
                feedback.setProgress(osm_p_end)
            else:
                feedback.pushInfo(self.tr(f"Downloading OSM extract: {pbf_url} …"))
                tmp = osm_dir / f"{area_slug}.osm.pbf.tmp"
                cancelled = self._download_chunked(
                    pbf_url, tmp, osm_path, feedback, 0, osm_p_end
                )
                if cancelled:
                    return {}
                feedback.pushInfo(self.tr("Verifying OSM MD5 …"))
                self._verify_md5(osm_path, pbf_url, feedback)

        # Steps B2–B4 — GTFS
        gtfs_dir = dest / "gtfs" / area_slug
        if download_gtfs:
            # B2 — API key pre-check (addendum Correction 2)
            if not api_key:
                raise QgsProcessingException(self.tr(
                    "Transitland API requires a free API key. "
                    "Sign up at https://www.transit.land/documentation/api-key "
                    "and provide the key in the GTFS_API_KEY parameter."
                ))

            gtfs_dir.mkdir(parents=True, exist_ok=True)

            # B2 — query Transitland
            tl_url = (
                f"{_TRANSITLAND_FEEDS_URL}"
                f"?bbox={lon_min},{lat_min},{lon_max},{lat_max}&spec=gtfs&apikey={api_key}"
            )
            feedback.pushInfo(self.tr("Querying Transitland API …"))
            all_feeds = self._query_transitland(tl_url, area_id, gtfs_dir, feedback)

            if feedback.isCanceled():
                return {}

            # B2b — filter continental/national aggregates (addendum Correction 3)
            local_feeds = [f for f in all_feeds if _is_local_feed(f, bbox)]
            skipped = len(all_feeds) - len(local_feeds)
            if skipped:
                feedback.pushInfo(self.tr(
                    f"Skipped {skipped} feed(s) larger than 5× query bbox "
                    "(continental/national aggregates)."
                ))
            if len(local_feeds) > _MAX_GTFS_FEEDS:
                feedback.pushWarning(self.tr(
                    f"Found {len(local_feeds)} local feeds — limiting to first {_MAX_GTFS_FEEDS}. "
                    "Use a more specific region name (e.g. a voivodeship) for a smaller feed set."
                ))
                local_feeds = local_feeds[:_MAX_GTFS_FEEDS]
            feedback.pushInfo(self.tr(
                f"Feeds to download: {len(local_feeds)}"
            ))

            downloaded_zips: list[Path] = []
            if local_feeds:
                feed_budget = (gtfs_p_end - gtfs_p_start) / len(local_feeds)
                for i, feed in enumerate(local_feeds):
                    if feedback.isCanceled():
                        return {}
                    f_start = int(gtfs_p_start + i * feed_budget)
                    f_end = int(gtfs_p_start + (i + 1) * feed_budget)
                    result = self._download_feed(feed, gtfs_dir, feedback, f_start, f_end)
                    if feedback.isCanceled():
                        return {}
                    if result is not None:
                        downloaded_zips.append(result)

            # B4 — validate only feeds downloaded in this run
            for zip_path in downloaded_zips:
                self._validate_gtfs(zip_path, zip_path.stem, feedback)

        # Final progress + summary log
        feedback.setProgress(100)
        self._log_summary(
            download_osm, osm_path,
            download_gtfs, gtfs_dir,
            feedback,
        )

        return {
            self.OUTPUT_OSM: str(osm_path) if (download_osm and osm_path.exists()) else "",
            self.OUTPUT_GTFS_DIR: str(gtfs_dir) if download_gtfs else "",
        }

    # ------------------------------------------------------------------ helpers

    def _check_writable(self, dest: Path) -> None:
        if not dest.is_dir():
            raise QgsProcessingException(self.tr(
                f"Destination folder '{dest}' does not exist. "
                "Create it first or choose an existing folder."
            ))
        if not os.access(dest, os.W_OK):
            raise QgsProcessingException(self.tr(
                f"Destination folder '{dest}' is not writable. "
                "Check permissions or choose another folder."
            ))

    def _check_disk(self, dest: Path, min_mb: int) -> None:
        free_mb = shutil.disk_usage(dest).free / (1024 * 1024)
        if free_mb < min_mb:
            raise QgsProcessingException(self.tr(
                f"Not enough disk space in '{dest}'. "
                f"Need ~{min_mb} MB, have {free_mb:.0f} MB."
            ))

    def _load_index(self, dest: Path, feedback) -> list:
        cache = dest / ".geofabrik-index.json"
        if (cache.exists()
                and (time.time() - cache.stat().st_mtime) < _INDEX_TTL_DAYS * 86400):
            feedback.pushInfo(self.tr("Using cached Geofabrik index."))
            with open(cache, encoding="utf-8") as fh:
                return json.load(fh)["features"]

        feedback.pushInfo(self.tr(f"Fetching Geofabrik index from {_GEOFABRIK_INDEX_URL} …"))
        req = urllib_request.Request(
            _GEOFABRIK_INDEX_URL, headers={"User-Agent": _USER_AGENT}
        )
        try:
            with urllib_request.urlopen(req, timeout=30) as resp:  # nosec B310 — QGIS stdlib only (no requests); HTTPS URL from hardcoded endpoint or trusted API
                raw = resp.read()
        except URLError as exc:
            raise QgsProcessingException(self.tr(
                "Cannot reach Geofabrik index at https://download.geofabrik.de. "
                f"Check your network connection. ({exc})"
            )) from exc

        data = json.loads(raw.decode("utf-8"))
        with open(cache, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return data["features"]

    def _find_area(self, features: list, area_name: str) -> dict:
        needle = area_name.lower()

        # 1 — exact match on id (most precise, avoids accidental substring matches)
        exact = [f for f in features
                 if f.get("properties", {}).get("id", "").lower() == needle]
        if len(exact) == 1:
            return exact[0]

        # 2 — contains match on id or name
        matches = [
            f for f in features
            if needle in f.get("properties", {}).get("id", "").lower()
            or needle in f.get("properties", {}).get("name", "").lower()
        ]

        if len(matches) == 0:
            all_ids = [f.get("properties", {}).get("id", "") for f in features]
            all_names = [f.get("properties", {}).get("name", "") for f in features]
            suggestions = difflib.get_close_matches(area_name, all_ids + all_names, n=5)
            raise QgsProcessingException(self.tr(
                f"Area '{area_name}' not found in Geofabrik index. "
                f"Closest matches: {suggestions}."
            ))

        if len(matches) > 1:
            found_ids = [f.get("properties", {}).get("id", "") for f in matches]
            raise QgsProcessingException(self.tr(
                f"Area '{area_name}' matches multiple regions: {found_ids}. "
                "Please use a more specific id."
            ))

        return matches[0]

    def _download_chunked(
        self,
        url: str,
        tmp: Path,
        final: Path,
        feedback,
        progress_start: int,
        progress_end: int,
    ) -> bool:
        """Download url to tmp then rename to final. Returns True if cancelled."""
        req = urllib_request.Request(url, headers={"User-Agent": _USER_AGENT})
        try:
            with urllib_request.urlopen(req, timeout=60) as resp:  # nosec B310 — QGIS stdlib only (no requests); HTTPS URL from hardcoded endpoint or trusted API
                total = int(resp.headers.get("Content-Length") or 0)
                downloaded = 0
                step_no_length = 0
                with open(tmp, "wb") as fh:
                    while True:
                        if feedback.isCanceled():
                            fh.close()
                            try:
                                os.remove(tmp)
                            except OSError:
                                pass
                            return True
                        chunk = resp.read(_CHUNK_SIZE)
                        if not chunk:
                            break
                        fh.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            pct = int(
                                downloaded / total * (progress_end - progress_start)
                                + progress_start
                            )
                            feedback.setProgress(pct)
                        else:
                            step_no_length += 1
                            pct = min(
                                progress_start + step_no_length,
                                progress_end - 1,
                            )
                            feedback.setProgress(pct)
        except URLError as exc:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise QgsProcessingException(self.tr(
                f"Download failed ({url}): {exc}"
            )) from exc

        if feedback.isCanceled():
            try:
                os.remove(tmp)
            except OSError:
                pass
            return True

        os.replace(tmp, final)
        return False

    def _verify_md5(self, file_path: Path, pbf_url: str, feedback) -> None:
        md5_url = pbf_url + ".md5"
        req = urllib_request.Request(md5_url, headers={"User-Agent": _USER_AGENT})
        try:
            with urllib_request.urlopen(req, timeout=30) as resp:  # nosec B310 — QGIS stdlib only (no requests); HTTPS URL from hardcoded endpoint or trusted API
                content = resp.read().decode("utf-8").strip()
        except URLError as exc:
            feedback.pushWarning(self.tr(
                f"Could not fetch MD5 manifest ({exc}). Skipping checksum verification."
            ))
            return

        expected = content.split()[0]
        h = hashlib.md5()  # nosec B324 — MD5 mandated by Geofabrik .md5 checksum spec
        with open(file_path, "rb") as fh:
            for block in iter(lambda: fh.read(_HASH_CHUNK), b""):
                h.update(block)
        got = h.hexdigest()

        if got.lower() != expected.lower():
            file_path.unlink(missing_ok=True)
            raise QgsProcessingException(self.tr(
                "OSM extract checksum does not match Geofabrik manifest. "
                "Likely network corruption — please retry."
            ))
        feedback.pushInfo(self.tr("OSM MD5 OK."))

    def _query_transitland(
        self, base_url: str, area_id: str, gtfs_dir: Path, feedback
    ) -> list:
        all_feeds: list = []
        next_url: "str | None" = base_url + "&per_page=100"
        page = 1

        while next_url:
            if feedback.isCanceled():
                return []
            req = urllib_request.Request(next_url, headers={"User-Agent": _USER_AGENT})
            try:
                with urllib_request.urlopen(req, timeout=30) as resp:  # nosec B310 — QGIS stdlib only (no requests); HTTPS URL from hardcoded endpoint or trusted API
                    data = json.loads(resp.read().decode("utf-8"))
            except HTTPError as exc:
                if exc.code == 401:
                    raise QgsProcessingException(self.tr(
                        "Transitland API key is invalid or expired. "
                        "Get a free key at https://www.transit.land — no credit card required."
                    )) from exc
                raise QgsProcessingException(self.tr(
                    f"Transitland API returned HTTP {exc.code}: {exc.reason}"
                )) from exc
            except URLError as exc:
                raise QgsProcessingException(self.tr(
                    f"Cannot reach Transitland API. Check your network connection. ({exc})"
                )) from exc

            page_feeds = data.get("feeds", [])
            all_feeds.extend(page_feeds)

            after = (data.get("meta") or {}).get("after")
            if after and page_feeds:
                next_url = base_url + f"&per_page=100&after={after}"
                page += 1
            else:
                next_url = None

        if page > 1:
            feedback.pushInfo(self.tr(
                f"Transitland: {len(all_feeds)} feed(s) fetched across {page} pages."
            ))

        if not all_feeds:
            feedback.pushWarning(self.tr(
                f"No GTFS feeds found in Transitland for the bounding box of '{area_id}'. "
                "The GTFS folder will be empty — you can add feeds manually by "
                f"copying their .zip files into '{gtfs_dir}' after the algorithm finishes."
            ))
        return all_feeds

    def _download_feed(
        self,
        feed: dict,
        gtfs_dir: Path,
        feedback,
        progress_start: int,
        progress_end: int,
    ) -> "Path | None":
        """Download one GTFS feed. Returns zip_path on success, None on skip/cancel."""
        onestop_id = feed.get("onestop_id") or str(feed.get("id", "unknown"))
        url = (feed.get("urls") or {}).get("static_current")
        if not url:
            feedback.pushWarning(self.tr(
                f"Feed '{onestop_id}' has no static_current URL — skipping."
            ))
            return None

        # Sanitize id for use as filename (Windows dislikes some characters)
        safe_id = onestop_id.replace("/", "_").replace("\\", "_").replace(":", "_")
        zip_path = gtfs_dir / f"{safe_id}.zip"
        tmp = gtfs_dir / f"{safe_id}.zip.tmp"
        feedback.pushInfo(self.tr(f"Downloading GTFS feed '{onestop_id}' …"))

        req = urllib_request.Request(url, headers={"User-Agent": _USER_AGENT})
        try:
            with urllib_request.urlopen(req, timeout=60) as resp:  # nosec B310 — QGIS stdlib only (no requests); HTTPS URL from hardcoded endpoint or trusted API
                total = int(resp.headers.get("Content-Length") or 0)
                downloaded = 0
                step_no_length = 0
                with open(tmp, "wb") as fh:
                    while True:
                        if feedback.isCanceled():
                            fh.close()
                            try:
                                os.remove(tmp)
                            except OSError:
                                pass
                            return None
                        chunk = resp.read(_CHUNK_SIZE)
                        if not chunk:
                            break
                        fh.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            pct = int(
                                downloaded / total * (progress_end - progress_start)
                                + progress_start
                            )
                            feedback.setProgress(pct)
                        else:
                            step_no_length += 1
                            pct = min(
                                progress_start + step_no_length,
                                progress_end - 1,
                            )
                            feedback.setProgress(pct)
        except HTTPError as exc:
            try:
                os.remove(tmp)
            except OSError:
                pass
            feedback.pushWarning(self.tr(
                f"Feed '{onestop_id}': HTTP {exc.code} ({exc.reason}) — skipping."
            ))
            return None
        except URLError as exc:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise QgsProcessingException(self.tr(
                f"Feed '{onestop_id}': download failed — {exc}"
            )) from exc

        if feedback.isCanceled():
            try:
                os.remove(tmp)
            except OSError:
                pass
            return None

        os.replace(tmp, zip_path)
        size_kb = zip_path.stat().st_size // 1024
        feedback.pushInfo(self.tr(f"  Saved: {zip_path.name}  ({size_kb} KB)"))
        return zip_path

    def _validate_gtfs(self, zip_path: Path, feed_id: str, feedback) -> None:
        try:
            with zipfile.ZipFile(zip_path) as zf:
                names = {Path(n).name for n in zf.namelist()}
        except zipfile.BadZipFile:
            feedback.pushWarning(self.tr(
                f"Feed '{feed_id}': downloaded file is not a valid ZIP archive. "
                "The URL may have returned an HTML page instead of a GTFS feed. "
                "Add the correct .zip manually."
            ))
            return

        missing_core = _REQUIRED_GTFS - names
        missing_cal = _GTFS_CALENDAR - names
        issues = list(missing_core)
        if missing_cal == _GTFS_CALENDAR:
            issues.append("calendar.txt or calendar_dates.txt")
        if issues:
            feedback.pushWarning(self.tr(
                f"Feed '{feed_id}': missing GTFS files — {issues}. "
                "OTP may still load the feed if the missing files are optional."
            ))

    def _log_summary(
        self,
        download_osm: bool,
        osm_path: Path,
        download_gtfs: bool,
        gtfs_dir: Path,
        feedback,
    ) -> None:
        feedback.pushInfo(self.tr("--- Download summary ---"))
        if download_osm:
            if osm_path.exists():
                size_mb = osm_path.stat().st_size / (1024 * 1024)
                feedback.pushInfo(self.tr(
                    f"OSM extract : {osm_path}  ({size_mb:.1f} MB)"
                ))
            else:
                feedback.pushInfo(self.tr("OSM extract : not downloaded"))
        else:
            feedback.pushInfo(self.tr("OSM extract : skipped (DOWNLOAD_OSM=False)"))

        if download_gtfs:
            zips = sorted(gtfs_dir.glob("*.zip")) if gtfs_dir.exists() else []
            if zips:
                feedback.pushInfo(self.tr(
                    f"GTFS feeds  : {len(zips)} file(s) in {gtfs_dir}"
                ))
                for z in zips:
                    size_kb = z.stat().st_size // 1024
                    feedback.pushInfo(self.tr(f"  {z.name}  ({size_kb} KB)"))
            else:
                feedback.pushInfo(self.tr(f"GTFS feeds  : none (folder: {gtfs_dir})"))
        else:
            feedback.pushInfo(self.tr("GTFS feeds  : skipped (DOWNLOAD_GTFS=False)"))

        if download_osm and osm_path.exists():
            feedback.pushInfo(self.tr(
                f"\nReady for RunTemporalAccessibility:\n"
                f"  OSM extract  →  {osm_path}\n"
                f"  GTFS folder  →  {gtfs_dir if download_gtfs else '<your local gtfs folder>'}"
            ))
