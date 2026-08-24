"""City 'boundary' as the jagged dissolved outline of the actual hex500 grid
used for accessibility, per qgis-hex-atlas-map skill's Step 3 -- replaces the
Nominatim admin boundary, which can disagree with the hex grid's own extent
(a hex layer built with a bounding-box buffer, or clipped differently, won't
line up with the legal city line). Dissolving the hexes themselves guarantees
the outline matches exactly what's rendered underneath it.

Usage: py build_hex_boundary.py <city>
Output: <city>/<city>_hex_boundary.geojson (single dissolved polygon/multipolygon)
"""
import sys
from pathlib import Path

import geopandas as gpd
from shapely.ops import unary_union

CITY = sys.argv[1]
BASE = Path(__file__).parent
CITY_DIR = (BASE.parent / "accessibility_lodz") if CITY == "lodz" else (BASE / CITY)
gpkg = CITY_DIR / (f"lodz_hex500.gpkg" if CITY == "lodz" else f"{CITY}_hex500.gpkg")
OUT = CITY_DIR / f"{CITY}_hex_boundary.geojson"

g = gpd.read_file(gpkg, layer="hex500")
# neighboring hex cells from native:creategrid don't always share exactly
# coincident vertices (floating-point rounding at 500m spacing), so a plain
# unary_union leaves micro-gaps that split the result into many slivers
# instead of one solid shape (seen live on Krakow: a "picket fence" of
# vertical seams cutting through the whole outline). Buffer out then back in
# by less than a millimeter to close those gaps before unioning.
eps = 0.01
buffered = [geom.buffer(eps) for geom in g.geometry.values]
dissolved = unary_union(buffered).buffer(-eps)
out = gpd.GeoDataFrame({"name": [CITY]}, geometry=[dissolved], crs=g.crs)
# GeoJSON (RFC7946) has no CRS member -- coordinates are always assumed WGS84,
# so writing the source CRS (a metric PL-1992 projection) unreprojected embeds
# meters mislabeled as degrees, which QGIS then "correctly" reads as WGS84 and
# misplaces off-canvas. Reproject explicitly before writing.
out.to_crs(epsg=4326).to_file(OUT, driver="GeoJSON")
print(f"{CITY}: dissolved {len(g)} hexes -> {OUT} ({dissolved.geom_type})")
