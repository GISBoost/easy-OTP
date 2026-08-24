"""Download the latest realized GTFS (P50) for a city from GISBoost/easy-GTFS-RT
releases via gh CLI, into <city>/<city>_gtfs.zip (ready to sit alongside the
clipped .osm.pbf in the same data_path folder for r5r's build_network()).

Usage: py download_gtfs.py <city> <release_tag>
  (release_tag e.g. "warszawa-realized-2026-08-22-phone", found via
   gh release list --repo GISBoost/easy-GTFS-RT)
"""
import subprocess
import sys
from pathlib import Path

CITY = sys.argv[1]
TAG = sys.argv[2]
BASE = Path(__file__).parent
CITY_DIR = BASE / CITY
CITY_DIR.mkdir(exist_ok=True)
GH = r"C:\Program Files\GitHub CLI\gh.exe"

out = CITY_DIR / f"{CITY}_gtfs.zip"
subprocess.run([GH, "release", "download", TAG, "--repo", "GISBoost/easy-GTFS-RT",
                 "--pattern", "*_p50.zip", "--output", str(out), "--clobber"], check=True)
print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")
