"""Aggregate pop_20_29 (student-age proxy, GUS NSP2021) from obwod-spisowy
centroids onto the 500m hex grid (point-in-polygon, same method as
export_hex_origins.py's SES aggregation) -- filtered to Lodz city obwody only
(the source xlsx covers the whole Lodzkie voivodeship).

Usage: py export_hex_students.py
Output: lodz_hex_students.csv (hex_id, pop_20_29, n_obwody)
"""
import geopandas as gpd
import pandas as pd

BASE_DIR = __file__.rsplit("\\", 1)[0]

hex_grid = gpd.read_file(f"{BASE_DIR}/lodz_hex500.gpkg", layer="hex500")
obwody = gpd.read_file(f"{BASE_DIR}/../ses_income_lodz/lodz.gpkg", layer="obwody_spisowe")
age = pd.read_csv(f"{BASE_DIR}/lodz_age2029_population.csv", dtype={"OBWOD": str})

obwody["OBWOD"] = obwody["OBWOD"].astype(str)
obwody = obwody.merge(age, on="OBWOD", how="left")
missing = obwody["pop_20_29"].isna().sum()
print(f"obwody matched to age2029: {len(obwody) - missing}/{len(obwody)} ({missing} missing -> treated as 0)")
obwody["pop_20_29"] = obwody["pop_20_29"].fillna(0)

obwody_pts = obwody.copy()
obwody_pts["geometry"] = obwody.geometry.centroid
joined = gpd.sjoin(obwody_pts, hex_grid[["hex_id", "geometry"]], predicate="within", how="inner")

agg = joined.groupby("hex_id").agg(pop_20_29=("pop_20_29", "sum"), n_obwody=("OBWOD", "count")).reset_index()
agg.to_csv(f"{BASE_DIR}/lodz_hex_students.csv", index=False)
print(f"wrote student-population aggregates for {len(agg)}/{len(hex_grid)} hexes "
      f"(total pop_20_29 in city: {obwody['pop_20_29'].sum():.0f})")
