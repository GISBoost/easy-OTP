"""Download the Geofabrik voivodeship .osm.pbf for a city (if not already cached
in pbf_regions/) and clip it to the city's boundary with osmosis (already
installed locally at C:\\Users\\Michal\\josm\\osmosis -- no new tool needed).
Produces a city-sized .osm.pbf instead of a 100-300MB voivodeship-wide one,
matching the size class of Lodz's existing tools/family_a_reconstruction pbf.

Boundary source: the city's own `ses_income_lodz/<city>.gpkg` (obwody_spisowe
layer) if it exists -- the original 6 cities all have this from an earlier
SES study. Cities added later (no SES data, e.g. gzm/kielce) fall back to
`<city>/<city>_boundary.geojson` (fetch_city_boundary.py's Nominatim output).

Clip method: rectangular --bounding-box (+ margin) for a normal compact city.
For an irregular multi-municipality area (GZM: 41 municipalities, nowhere
near convex) a bbox would pull in a lot of extra Śląskie territory --
CITIES[city].get("clip_method") == "polygon" switches to osmosis
--bounding-polygon on a .poly file generated from the boundary geometry
(see geojson_to_poly.py) instead.

Usage: py prepare_osm_pbf.py <city>
"""
import subprocess
import sys
import urllib.request
from pathlib import Path

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
    REGIONS_DIR.mkdir(exist_ok=True)
    url = f"https://download.geofabrik.de/europe/poland/{region}-latest.osm.pbf"
    print(f"downloading {url} -> {region_pbf}")
    urllib.request.urlretrieve(url, region_pbf)
else:
    print(f"reusing cached {region_pbf}")

out_pbf = CITY_DIR / f"{CITY}.osm.pbf"
ses_gpkg = BASE.parent / "ses_income_lodz" / f"{CITY}.gpkg"

# completeWays=yes (both clip paths below) pulls in every node referenced by
# a way that crosses the clip edge -- without it, ways get truncated with
# dangling node refs that R5's park-and-ride-area builder NPEs on (found
# live: Krakow's clip crashed setup_r5 with "Cannot invoke Node.getLon()
# because n is null").

if CFG.get("clip_method") == "polygon":
    boundary_geojson = CITY_DIR / f"{CITY}_boundary.geojson"
    poly_file = CITY_DIR / f"{CITY}.poly"
    subprocess.run(
        [sys.executable, str(BASE / "geojson_to_poly.py"), str(boundary_geojson), str(poly_file), CITY],
        check=True,
    )
    print(f"{CITY}: polygon clip from {boundary_geojson}")
    cmd = [
        OSMOSIS,
        "--read-pbf", f"file={region_pbf}",
        "--bounding-polygon", f"file={poly_file}",
        "completeWays=yes",
        "--write-pbf", f"file={out_pbf}",
    ]
else:
    import geopandas as gpd  # noqa: PLC0415 -- only needed on this path

    if ses_gpkg.exists():
        g = gpd.read_file(ses_gpkg, layer="obwody_spisowe")
    else:
        boundary_geojson = CITY_DIR / f"{CITY}_boundary.geojson"
        print(f"{CITY}: no {ses_gpkg}, falling back to {boundary_geojson}")
        g = gpd.read_file(boundary_geojson)
    bounds = g.to_crs(4326).total_bounds  # minx, miny, maxx, maxy
    margin = 0.02
    left, bottom, right, top = (bounds[0] - margin, bounds[1] - margin,
                                 bounds[2] + margin, bounds[3] + margin)
    print(f"{CITY} bbox (WGS84, +{margin} margin): {left:.4f},{bottom:.4f},{right:.4f},{top:.4f}")
    cmd = [
        OSMOSIS,
        "--read-pbf", f"file={region_pbf}",
        "--bounding-box", f"left={left}", f"bottom={bottom}", f"right={right}", f"top={top}",
        "completeWays=yes",
        "--write-pbf", f"file={out_pbf}",
    ]

print("running:", " ".join(cmd))
subprocess.run(cmd, check=True)
print(f"wrote {out_pbf} ({out_pbf.stat().st_size / 1e6:.1f} MB, "
      f"region was {region_pbf.stat().st_size / 1e6:.1f} MB)")
