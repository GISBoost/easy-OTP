"""Basemap mask per city: a generous rectangle minus the dissolved hex outline,
so the OSM basemap only shows inside the actual accessibility data extent and
everything else renders white -- matches the qgis-hex-atlas-map skill's Step 3
mask-from-dissolved-hex construction.

Usage: py build_hex_mask.py <city>
Output: <city>/<city>_hex_mask.geojson (WGS84, matches hex_boundary.geojson)
"""
import sys
from pathlib import Path

import geopandas as gpd
from shapely.geometry import box

CITY = sys.argv[1]
BASE = Path(__file__).parent
CITY_DIR = (BASE.parent / "accessibility_lodz") if CITY == "lodz" else (BASE / CITY)
boundary_path = CITY_DIR / f"{CITY}_hex_boundary.geojson"
OUT = CITY_DIR / f"{CITY}_hex_mask.geojson"

boundary = gpd.read_file(boundary_path)
# work in a local metric CRS (Web Mercator is fine for this purpose) so the
# padding is in real meters, then reproject back to WGS84 for consistency
# with every other layer built by this tool
b3857 = boundary.to_crs(3857)
minx, miny, maxx, maxy = b3857.total_bounds
pad = 2.0 * max(maxx - minx, maxy - miny)
outer = box(minx - pad, miny - pad, maxx + pad, maxy + pad)
mask_geom = outer.difference(b3857.geometry.union_all())

out = gpd.GeoDataFrame({"name": [CITY]}, geometry=[mask_geom], crs=3857).to_crs(4326)
out.to_file(OUT, driver="GeoJSON")
print(f"{CITY}: wrote {OUT}")
