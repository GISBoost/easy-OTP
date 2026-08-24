"""For one city: export 500m hex centroids (WGS84) as r5r origins, and aggregate
onto each hex (point-in-polygon, obwod-spisowy centroid -> containing hex, same
method as accessibility_lodz's Etap 3/4 scripts): population-weighted mean
income_index_pln, total population, and total population aged 20-29 (student
proxy). Generalized/combined version of export_hex_origins.py + export_hex_
students.py from accessibility_lodz.

Usage: py export_hex_data.py <city>
Output: <city>/<city>_hex_origins.csv, <city>/<city>_hex_ses.csv
"""
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

CITY = sys.argv[1]
BASE = Path(__file__).parent
CITY_DIR = BASE / CITY

hex_grid = gpd.read_file(CITY_DIR / f"{CITY}_hex500.gpkg", layer="hex500")
obwody = gpd.read_file(BASE.parent / "ses_income_lodz" / f"{CITY}.gpkg", layer="obwody_spisowe")
age = pd.read_csv(CITY_DIR / f"{CITY}_age2029_population.csv", dtype={"OBWOD": str})

# 1. hex centroids -> r5r origins CSV (WGS84)
centroids_wgs84 = hex_grid.geometry.centroid.to_crs(4326)
origins = pd.DataFrame({
    "id": hex_grid["hex_id"].astype(str),
    "lon": centroids_wgs84.x,
    "lat": centroids_wgs84.y,
})
origins.to_csv(CITY_DIR / f"{CITY}_hex_origins.csv", index=False)
print(f"{CITY}: wrote {len(origins)} hex origins")

# 2. obwod centroids -> point-in-hex join -> weighted SES aggregates
# income_index_pln/population are String-typed in the 4 tileset-sourced cities'
# gpkg (Warszawa/Poznan/Gdansk/Szczecin -- ogr2ogr consolidation artifact, see
# ses_income_lodz/HANDOFF.md) -- coerce explicitly or the weighted mean below
# fails on string*float multiplication.
obwody["OBWOD"] = obwody["OBWOD"].astype(str)
obwody["income_index_pln"] = pd.to_numeric(obwody["income_index_pln"], errors="coerce")
obwody["population"] = pd.to_numeric(obwody["population"], errors="coerce")
obwody = obwody.merge(age, on="OBWOD", how="left")
missing_age = obwody["pop_20_29"].isna().sum()
obwody["pop_20_29"] = obwody["pop_20_29"].fillna(0)
print(f"{CITY}: obwody matched to age2029: {len(obwody) - missing_age}/{len(obwody)}")

obwody_pts = obwody.copy()
obwody_pts["geometry"] = obwody.geometry.centroid
joined = gpd.sjoin(obwody_pts, hex_grid[["hex_id", "geometry"]], predicate="within", how="inner")


def weighted_mean(g, col, w="population"):
    valid = g.dropna(subset=[col, w])
    valid = valid[valid[w] > 0]
    if valid.empty:
        return None
    return (valid[col] * valid[w]).sum() / valid[w].sum()


rows = []
for hex_id, g in joined.groupby("hex_id"):
    rows.append({
        "hex_id": hex_id,
        "population": g["population"].sum(),
        "pop_20_29": g["pop_20_29"].sum(),
        "income_index_pln": weighted_mean(g, "income_index_pln"),
        "n_obwody": len(g),
    })
ses = pd.DataFrame(rows)
ses.to_csv(CITY_DIR / f"{CITY}_hex_ses.csv", index=False)
print(f"{CITY}: wrote SES aggregates for {len(ses)}/{len(hex_grid)} hexes "
      f"(pop_20_29 total: {obwody['pop_20_29'].sum():.0f}, "
      f"population total: {obwody['population'].sum():.0f})")
