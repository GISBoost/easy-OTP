"""generate_isochrones_multi_city.py — isochrones-over-time, static vs GTFS-RT,
for several cities in one unattended run.

Standalone ANALYSIS tooling. NOT part of the easy-OTP plugin and not imported
by it. Wraps the plugin's own `easyotp:generateisochronesovertime` Processing
algorithm (see easy_otp/algorithms/generate_isochrones_over_time.py) in a
per-city loop, downloading whatever OSM/GTFS inputs each city needs first.

Run inside the QGIS Python Console (Plugins -> Python Console -> "Show
Editor", paste, Run) or as a one-off Processing "Script" — same convention as
every other script in this folder. Deliberately NOT driven through the
qgis-mcp bridge/tool: that bridge's own socket has a ~60s timeout, far
shorter than a single city's isochrone run (~10-15 min), which was observed
this project to cause the bridge to silently retry a "timed out" call,
running the same city 2-3x back to back, and once even left QGIS itself
hung after an OTP subprocess was killed out from under it. Calling
processing.run() directly, in-process, in this script sidesteps that whole
class of problem — QGIS will show "Not Responding" for the run's full
duration (same as it would through any long Processing algorithm call); that
is expected and harmless, not a hang.

Also deliberately does NOT go through easyotp:downloadtransitdata for the
OSM step - that algorithm hard-fails above a 20 square-degree bounding box
(_MAX_BBOX_DEG2 in easy_otp/algorithms/download_transit_data.py), a sane
guard for interactive use but wrong here: several cities only have a
whole-country Geofabrik extract with no smaller subdivision available at
all. OTP itself has no such limit - a bigger extract just costs more
time/RAM to build, which is exactly why OTP_XMX_BUILD/OTP_XMX_SERVE below
default to 8G. This script's own _download_osm() is a trimmed copy of that
algorithm's downloader (direct Geofabrik URL + .md5 verify + on-disk cache),
without the bbox gate.

What it does, per city in CITIES:
  1. Downloads that city's OSM extract from Geofabrik (skipped if already
     cached under WORK_DIR/osm/ and less than OSM_CACHE_DAYS old).
  2. Downloads that city's static + Family A realized-P50 GTFS zips from its
     GitHub release (GISBoost/easy-GTFS-RT), each into its OWN folder -
     OTP cannot load static and realized GTFS together in one graph, since
     Family A's realized feed intentionally reuses the exact same trip_id/
     stop_id/route_id as the static feed it was built from (byte-identical
     except for corrected times - see tools/family_a_reconstruction's
     README), so loading both at once would collide, not compare.
  3. Runs GenerateIsochronesOverTime twice (static, then rt) - one origin
     point per city, TIME_START-TIME_END every INTERVAL_MIN minutes,
     KEEP_SERVER_ALIVE=False so each call's OTP server fully tears down
     before the next one starts (single shared OTP_PORT, sequential only,
     no parallelism).
  4. Writes each run's isochrone polygons to its own GeoPackage file
     (OUTPUT_DIR/<city>_<static|rt>_isochrones.gpkg) - a real file on disk,
     unlike a `memory:` layer, which does not survive a QGIS crash/restart
     (lost exactly that way once already this project). One file per
     city/variant rather than one shared multi-layer GeoPackage: the
     "<path>.gpkg|layername=X" convention for targeting a named layer inside
     an existing GeoPackage was tested against this QGIS install and did NOT
     work as a plain sink string (it tried to literally create a file named
     "...gpkg|layername=X.gpkg" and failed) - one file per output, exactly
     the syntax already proven twice this project (the manual Łódź test),
     is the reliable choice for an unattended run nobody is watching.
  5. Loads the freshly-written layer into the current QGIS project right
     after each successful write, so layers appear incrementally as the run
     progresses - the only visible proof-of-life during a multi-hour
     unattended run, short of the Python Console's own print() output.

A single bad city (network hiccup, unexpectedly-missing release asset, OTP
failing to route from that city's origin point, etc.) is caught and logged,
not allowed to abort the rest of the run - see the try/except around each
city's block in main(). A final summary is printed at the end listing every
city/variant's outcome.

Edit the CONFIG block below, then run. Expect roughly 10-15 minutes per
city per variant (961 timestamps at ~0.6s/isochrone, the rate observed
against a warm OTP server this project) plus a one-off graph build per
variant (graphs are cached by router_id after the first run, but static and
rt use different GTFS so each city builds two graphs, not one) - for the
default 9-city CONFIG below, total runtime is roughly 3-4.5 hours,
sequential, unattended.
"""
from __future__ import annotations

import hashlib
import subprocess
import time
from pathlib import Path
from urllib import request as urllib_request
from urllib.error import URLError

import processing
from qgis.core import QgsProject, QgsVectorLayer

# ------------------------------- CONFIG -------------------------------

# Each entry: (city id, display name, Geofabrik path under download.geofabrik.de/,
# origin longitude, origin latitude - a well-known, transit-connected central
# square/station). Only cities with a complete 2026-07-16 recording are
# included here (bucharest's latest release is 07-15; boston/brisbane are
# first-day/partial recordings) - add more rows the same shape to extend.
CITIES = [
    ("lodz",     "Łódź",     "europe/poland/lodzkie",           19.4571,  51.7769),
    ("poznan",   "Poznań",   "europe/poland/wielkopolskie",     16.9335,  52.4083),
    ("szczecin", "Szczecin", "europe/poland/zachodniopomorskie", 14.5528, 53.4285),
    ("prague",   "Prague",   "europe/czech-republic/praha",     14.4208,  50.0870),
    ("rome",     "Rome",     "europe/italy/centro",             12.4823,  41.8955),
    ("turin",    "Turin",    "europe/italy/nord-ovest",          7.6869,  45.0703),
    ("vilnius",  "Vilnius",  "europe/lithuania",                25.2880,  54.6863),
    ("sofia",    "Sofia",    "europe/bulgaria",                 23.3219,  42.6977),
    ("lisbon",   "Lisbon",   "europe/portugal",                 -9.1394,  38.7139),
]

ANALYSIS_DATE = "2026-07-16"   # same day for every city in this run (see CITIES comment)
GTFS_REPO = "GISBoost/easy-GTFS-RT"
GH_EXE = r"C:\Program Files\GitHub CLI\gh.exe"   # full path always - see CLAUDE.md

WORK_DIR = Path("C:/Users/Public/otp")
OSM_DIR = WORK_DIR / "osm"
GTFS_DIR = WORK_DIR / "gtfs-multi"
OSM_CACHE_DAYS = 7   # matches easyotp:downloadtransitdata's own cache convention

CUTOFFS_MIN = "30"
TIME_START = "06:00:00"
TIME_END = "22:00:00"
INTERVAL_MIN = 1
MODE = 0        # index into ["TRANSIT","BUS","RAIL","TRAM","SUBWAY","WALK","CAR","BICYCLE"] - TRANSIT
DIRECTION = 0   # index into ["FROM","TO"] - FROM (catchment reachable from the origin)

OTP_JAR_PATH = str(WORK_DIR / "otp-1.5.0-shaded.jar")
OTP_XMX_BUILD = "8G"
OTP_XMX_SERVE = "8G"
OTP_PORT = 8801

OUTPUT_DIR = WORK_DIR / "isochrones_multi"

# ------------------------------------------------------------------------

_USER_AGENT = "easy-OTP/0.2 (generate_isochrones_multi_city.py)"
_CHUNK_SIZE = 64 * 1024


def _download_file(url: str, dest: Path) -> None:
    """Plain chunked download, no bbox/size gate - see module docstring for why
    this doesn't reuse easyotp:downloadtransitdata's own downloader.
    """
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    req = urllib_request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib_request.urlopen(req, timeout=120) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        downloaded = 0
        with open(tmp, "wb") as fh:
            while True:
                chunk = resp.read(_CHUNK_SIZE)
                if not chunk:
                    break
                fh.write(chunk)
                downloaded += len(chunk)
        if total:
            print(f"    downloaded {downloaded / 1e6:.1f} MB / {total / 1e6:.1f} MB")
        else:
            print(f"    downloaded {downloaded / 1e6:.1f} MB")
    tmp.replace(dest)


def _verify_md5(file_path: Path, url: str) -> None:
    md5_url = url + ".md5"
    try:
        req = urllib_request.Request(md5_url, headers={"User-Agent": _USER_AGENT})
        with urllib_request.urlopen(req, timeout=30) as resp:
            expected = resp.read().decode("utf-8").strip().split()[0]
    except URLError as exc:
        print(f"    (could not fetch .md5 manifest - skipping checksum: {exc})")
        return
    h = hashlib.md5()  # nosec B324 - MD5 mandated by Geofabrik's own .md5 checksum spec
    with open(file_path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    if h.hexdigest().lower() != expected.lower():
        file_path.unlink(missing_ok=True)
        raise RuntimeError(f"MD5 mismatch for {file_path.name} - deleted, retry the run.")
    print("    MD5 OK")


def download_osm(geofabrik_path: str) -> Path:
    """Returns the local .osm.pbf path, downloading (with cache) as needed."""
    area_slug = geofabrik_path.rstrip("/").split("/")[-1]
    dest = OSM_DIR / f"{area_slug}.osm.pbf"
    if dest.exists() and (time.time() - dest.stat().st_mtime) < OSM_CACHE_DAYS * 86400:
        print(f"  OSM: using cached {dest}")
        return dest

    OSM_DIR.mkdir(parents=True, exist_ok=True)
    url = f"https://download.geofabrik.de/{geofabrik_path}-latest.osm.pbf"
    print(f"  OSM: downloading {url}")
    _download_file(url, dest)
    _verify_md5(dest, url)
    print(f"  OSM: saved {dest}")
    return dest


def ensure_gtfs(city_id: str, variant: str) -> Path:
    """variant is 'static' or 'rt'. Returns the folder containing exactly one
    GTFS zip - GenerateIsochronesOverTime's GTFS_FILES param globs *.zip in a
    folder, so each variant must be isolated (see module docstring).
    """
    if variant == "static":
        asset_name = f"{city_id}_static_gtfs_{ANALYSIS_DATE}.zip"
    else:
        asset_name = f"{city_id}_realized_{ANALYSIS_DATE}_p50.zip"

    dest_dir = GTFS_DIR / city_id / variant
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_zip = dest_dir / asset_name
    if dest_zip.exists():
        print(f"  GTFS ({variant}): using cached {dest_zip}")
        return dest_dir

    tag = f"{city_id}-realized-{ANALYSIS_DATE}-phone"
    print(f"  GTFS ({variant}): downloading {asset_name} from release {tag}")
    result = subprocess.run(
        [GH_EXE, "release", "download", tag, "-R", GTFS_REPO,
         "-p", asset_name, "--dir", str(dest_dir), "--clobber"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"gh release download failed for {tag}/{asset_name}: {result.stderr.strip()}"
        )
    print(f"  GTFS ({variant}): saved {dest_zip}")
    return dest_dir


def run_isochrones_over_time(
    city_id: str, origin_lon: float, origin_lat: float,
    variant: str, osm_pbf: Path, gtfs_dir: Path,
) -> None:
    layer_name = f"isochrones_{city_id}_{variant}"
    csv_path = str(OUTPUT_DIR / f"{city_id}_{variant}_area.csv")
    gpkg_path = str(OUTPUT_DIR / f"{city_id}_{variant}_isochrones.gpkg")

    print(f"  Running GenerateIsochronesOverTime ({variant}) -> {gpkg_path}")
    start = time.monotonic()
    processing.run("easyotp:generateisochronesovertime", {
        "ORIGIN_POINT": f"{origin_lon},{origin_lat} [EPSG:4326]",
        "OSM_PBF": str(osm_pbf),
        "GTFS_FILES": str(gtfs_dir),
        "MODE": MODE,
        "DIRECTION": DIRECTION,
        "CUTOFFS_MIN": CUTOFFS_MIN,
        "TIME_START": TIME_START,
        "TIME_END": TIME_END,
        "INTERVAL": INTERVAL_MIN,
        "ANALYSIS_DATE": ANALYSIS_DATE,
        "WORK_DIR": str(WORK_DIR),
        "OUTPUT_ISOCHRONES": gpkg_path,
        "OUTPUT_AREA_CSV": csv_path,
        "USE_SAVED_JAVA": True,
        "OTP_JAR_PATH": OTP_JAR_PATH,
        "OTP_XMX_BUILD": OTP_XMX_BUILD,
        "OTP_XMX_SERVE": OTP_XMX_SERVE,
        "OTP_PORT": OTP_PORT,
        "KEEP_SERVER_ALIVE": False,
    })
    elapsed_min = (time.monotonic() - start) / 60.0
    print(f"  Done ({variant}) in {elapsed_min:.1f} min")

    layer = QgsVectorLayer(gpkg_path, layer_name, "ogr")
    if layer.isValid():
        QgsProject.instance().addMapLayer(layer)
        print(f"  Loaded into project: {layer_name} ({layer.featureCount()} features)")
    else:
        print(f"  WARNING: {layer_name} did not load as a valid layer after the run.")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    results: list[tuple[str, str, str]] = []  # (city_id, variant, "ok" or error message)

    for city_id, display_name, geofabrik_path, origin_lon, origin_lat in CITIES:
        print(f"\n=== {display_name} ({city_id}) ===")
        try:
            osm_pbf = download_osm(geofabrik_path)
        except Exception as exc:  # noqa: BLE001 - one city's failure must not abort the run
            print(f"  FAILED (OSM download): {exc}")
            results.append((city_id, "static", f"FAILED (OSM): {exc}"))
            results.append((city_id, "rt", f"FAILED (OSM): {exc}"))
            continue

        for variant in ("static", "rt"):
            try:
                gtfs_dir = ensure_gtfs(city_id, variant)
                run_isochrones_over_time(
                    city_id, origin_lon, origin_lat,
                    variant, osm_pbf, gtfs_dir,
                )
                results.append((city_id, variant, "ok"))
            except Exception as exc:  # noqa: BLE001
                print(f"  FAILED ({variant}): {exc}")
                results.append((city_id, variant, f"FAILED: {exc}"))

    print("\n=== Summary ===")
    for city_id, variant, outcome in results:
        print(f"  {city_id:10s} {variant:6s} {outcome}")
    print(f"\nOutput folder: {OUTPUT_DIR} (one <city>_<variant>_isochrones.gpkg per run)")


main()
