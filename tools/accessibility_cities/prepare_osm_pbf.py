"""Download the Geofabrik voivodeship .osm.pbf for a city (if not already cached
in pbf_regions/) and clip it to the city's bounding box (+ margin) with osmosis
(already installed locally at C:\\Users\\Michal\\josm\\osmosis -- no new tool
needed). Produces a city-sized .osm.pbf instead of a 100-300MB voivodeship-wide
one, matching the size class of Lodz's existing tools/family_a_reconstruction pbf.

Usage: py prepare_osm_pbf.py <city>
"""
import subprocess
import sys
import urllib.request
from pathlib import Path

import geopandas as gpd

from cities_config import CITIES

CITY = sys.argv[1]
CFG = CITIES[CITY]
BASE = Path(__file__).parent
REGIONS_DIR = BASE / "pbf_regions"
CITY_DIR = BASE / CITY
CITY_DIR.mkdir(exist_ok=True)
OSMOSIS = r"C:\Users\Michal\josm\osmosis\bin\osmosis.bat"

region = CFG["geofabrik_region"]
region_pbf = REGIONS_DIR / f"{region}.osm.pbf"
if not region_pbf.exists():
    url = f"https://download.geofabrik.de/europe/poland/{region}-latest.osm.pbf"
    print(f"downloading {url} -> {region_pbf}")
    urllib.request.urlretrieve(url, region_pbf)
else:
    print(f"reusing cached {region_pbf}")

# bbox from the city's own SES geometry (more precise than guessing), + margin
ses_gpkg = BASE.parent / "ses_income_lodz" / f"{CITY}.gpkg"
g = gpd.read_file(ses_gpkg, layer="obwody_spisowe")
bounds = g.to_crs(4326).total_bounds  # minx, miny, maxx, maxy
margin = 0.02
left, bottom, right, top = (bounds[0] - margin, bounds[1] - margin,
                             bounds[2] + margin, bounds[3] + margin)
print(f"{CITY} bbox (WGS84, +{margin} margin): {left:.4f},{bottom:.4f},{right:.4f},{top:.4f}")

out_pbf = CITY_DIR / f"{CITY}.osm.pbf"
cmd = [
    OSMOSIS,
    "--read-pbf", f"file={region_pbf}",
    # completeWays=yes pulls in every node referenced by a way that crosses
    # the bbox edge -- without it, ways get truncated with dangling node refs
    # that R5's park-and-ride-area builder NPEs on (found live: Krakow's clip
    # crashed setup_r5 with "Cannot invoke Node.getLon() because n is null").
    "--bounding-box", f"left={left}", f"bottom={bottom}", f"right={right}", f"top={top}",
    "completeWays=yes",
    "--write-pbf", f"file={out_pbf}",
]
print("running:", " ".join(cmd))
subprocess.run(cmd, check=True)
print(f"wrote {out_pbf} ({out_pbf.stat().st_size / 1e6:.1f} MB, "
      f"region was {region_pbf.stat().st_size / 1e6:.1f} MB)")
