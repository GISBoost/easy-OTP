"""Rebuild each city's QGIS-facing layers as small, purpose-built GPKG tables
with only the fields actually needed for viewing/editing, instead of two
QGIS layers that both point at the same 30-73-column hex500 table (with a
subset filter as the only difference) -- clutters the attribute table and,
for the 5 non-Lodz cities, `uslugi_30min` was rendered on `total_30min`,
which is the university-accessibility total (never overwritten with real
services data), not services at all. This script fixes that by deriving a
real services table from `{city}_service_accessibility.csv` for those cities.

Lodz already has correctly-named service columns in hex500 (education/health/
culture/groceries_Xmin) from the pre-multi-city pilot, plus Method A/C
variability columns the other cities don't have -- so Lodz gets 4 output
layers instead of 2.

Usage: py build_clean_layers.py <city>
Writes into <city>/<city>_hex500.gpkg (or accessibility_lodz/lodz_hex500.gpkg):
  hex_services      -- all cities
  hex_universities   -- all cities
  hex_variability     -- lodz only (Method A: daily spread)
  hex_p85_impact      -- lodz only (Method C: bad-day/P85 impact)
"""
import csv
import json
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

CITY = sys.argv[1]
BASE = Path(__file__).parent
CITY_DIR = (BASE.parent / "accessibility_lodz") if CITY == "lodz" else (BASE / CITY)
gpkg = CITY_DIR / (f"lodz_hex500.gpkg" if CITY == "lodz" else f"{CITY}_hex500.gpkg")
CUTOFF = 30

hex_all = gpd.read_file(gpkg, layer="hex500")
hex_geom = hex_all[["hex_id", "geometry"]].copy()
hex_geom["hex_id"] = hex_geom["hex_id"].astype(int)


def write_layer(gdf, name):
    gdf.to_file(gpkg, layer=name, driver="GPKG")
    print(f"{CITY}: wrote layer '{name}' ({len(gdf)} features, fields: {[c for c in gdf.columns if c != 'geometry']})")


# ---------------------------------------------------------------------------
# services
# ---------------------------------------------------------------------------
if CITY == "lodz":
    services = hex_all[["hex_id", "education_30min", "health_30min", "culture_30min",
                         "groceries_30min", "total_30min", "population", "income_index_pln"]].copy()
    services = services[services["total_30min"].notna()]
else:
    acc_csv = CITY_DIR / f"{CITY}_service_accessibility.csv"
    with open(CITY_DIR / f"{CITY}_service_destinations_slugmap.json", encoding="utf-8") as f:
        slug_to_name = json.load(f)  # {"education": "opp0", ...}
    name_by_slug = {v: k for k, v in slug_to_name.items()}

    wide = {}
    with open(acc_csv, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if int(row["cutoff"]) != CUTOFF:
                continue
            cat = name_by_slug.get(row["opportunity"], row["opportunity"])
            wide.setdefault(int(row["id"]), {})[cat] = int(row["accessibility"])
    categories = sorted(name_by_slug.values())
    rows = []
    for hex_id, acc in wide.items():
        r = {"hex_id": hex_id}
        for cat in categories:
            r[f"{cat}_30min"] = acc.get(cat, 0)
        r["total_30min"] = sum(acc.get(cat, 0) for cat in categories)
        rows.append(r)
    services = pd.DataFrame(rows)

    ses = pd.read_csv(CITY_DIR / f"{CITY}_hex_ses.csv")
    ses["hex_id"] = ses["hex_id"].astype(int)
    services = services.merge(ses[["hex_id", "population", "income_index_pln"]], on="hex_id", how="left")

services = gpd.GeoDataFrame(services.merge(hex_geom, on="hex_id", how="inner"), geometry="geometry", crs=hex_geom.crs)
write_layer(services, "hex_services")

# ---------------------------------------------------------------------------
# universities
# ---------------------------------------------------------------------------
if CITY == "lodz":
    uni = hex_all[["hex_id", "pop_20_29", "pop_tercile", "dominant_university", "biv_color",
                   "politechnika_30min", "uniwersytet_30min", "medyczny_30min"]].copy()
    uni["total_30min"] = (uni["politechnika_30min"].fillna(0) + uni["uniwersytet_30min"].fillna(0)
                           + uni["medyczny_30min"].fillna(0))
    uni = uni[uni["pop_20_29"].notna()]
else:
    uni_wide = pd.read_csv(CITY_DIR / "out" / f"{CITY}_uni_wide.csv")
    uni_wide["hex_id"] = uni_wide["hex_id"].astype(int)
    uni = uni_wide[["hex_id", "pop_20_29", "pop_tercile", "dominant_university", "biv_color", "total_30min"]].copy()

uni = gpd.GeoDataFrame(uni.merge(hex_geom, on="hex_id", how="inner"), geometry="geometry", crs=hex_geom.crs)
write_layer(uni, "hex_universities")

# ---------------------------------------------------------------------------
# lodz-only: Method A (daily variability) / Method C (P85 bad-day impact)
# ---------------------------------------------------------------------------
if CITY == "lodz":
    variability = hex_all[["hex_id", "spread_p5_p95_30min", "rel_spread_30min"]].copy()
    variability = variability[variability["spread_p5_p95_30min"].notna()]
    variability = gpd.GeoDataFrame(variability.merge(hex_geom, on="hex_id", how="inner"),
                                    geometry="geometry", crs=hex_geom.crs)
    write_layer(variability, "hex_variability")

    p85_impact = hex_all[["hex_id", "total_pct_impact_30min"]].copy()
    p85_impact = p85_impact[p85_impact["total_pct_impact_30min"].notna()]
    p85_impact = gpd.GeoDataFrame(p85_impact.merge(hex_geom, on="hex_id", how="inner"),
                                   geometry="geometry", crs=hex_geom.crs)
    write_layer(p85_impact, "hex_p85_impact")
