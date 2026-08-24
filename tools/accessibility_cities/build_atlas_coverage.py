"""Atlas coverage layer: one feature per city (name + dissolved hex outline
geometry), used to drive the QGIS print-layout atlas (one page per feature,
extent from the feature's own geometry).

Usage: py build_atlas_coverage.py
Output: atlas_cities_coverage.geojson
"""
from pathlib import Path

import geopandas as gpd

BASE = Path(__file__).parent
CITIES = {
    "lodz": (BASE.parent / "accessibility_lodz" / "lodz_hex_boundary.geojson", "Ł\xf3dź"),
    "warszawa": (BASE / "warszawa" / "warszawa_hex_boundary.geojson", "Warszawa"),
    "krakow": (BASE / "krakow" / "krakow_hex_boundary.geojson", "Krak\xf3w"),
    "gdansk": (BASE / "gdansk" / "gdansk_hex_boundary.geojson", "Gdańsk"),
    "poznan": (BASE / "poznan" / "poznan_hex_boundary.geojson", "Poznań"),
    "szczecin": (BASE / "szczecin" / "szczecin_hex_boundary.geojson", "Szczecin"),
}
rows = []
for slug, (path, display) in CITIES.items():
    g = gpd.read_file(path)
    rows.append({"city_slug": slug, "city_name": display, "geometry": g.geometry.iloc[0]})
out = gpd.GeoDataFrame(rows, crs="EPSG:4326")
out_path = BASE / "atlas_cities_coverage.geojson"
out.to_file(out_path, driver="GeoJSON")
print(f"wrote {out_path}")
for r in rows:
    print(" ", r["city_slug"], r["city_name"])
