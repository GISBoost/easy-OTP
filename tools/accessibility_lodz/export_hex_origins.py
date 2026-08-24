"""Export 500m hex centroids (id, lon/lat WGS84) as r5r origins, and separately
aggregate SES fields (population-weighted income, single-motherhood share, total
population) from obwody_spisowe onto each hex via point-in-polygon (obwod centroid
-> containing hex). Read-only.

Usage: py export_hex_origins.py
Output: lodz_hex_origins.csv (for r5r), lodz_hex_ses.csv (SES aggregates per hex_id)
"""
import geopandas as gpd
import pandas as pd

BASE_DIR = __file__.rsplit("\\", 1)[0]

hex_grid = gpd.read_file(f"{BASE_DIR}/lodz_hex500.gpkg", layer="hex500")
obwody = gpd.read_file(f"{BASE_DIR}/../ses_income_lodz/lodz.gpkg", layer="obwody_spisowe")

# 1. hex centroids -> r5r origins CSV (WGS84)
centroids_wgs84 = hex_grid.geometry.centroid.to_crs(4326)
origins = pd.DataFrame({
    "id": hex_grid["hex_id"].astype(str),
    "lon": centroids_wgs84.x,
    "lat": centroids_wgs84.y,
})
origins.to_csv(f"{BASE_DIR}/lodz_hex_origins.csv", index=False)
print(f"wrote {len(origins)} hex origins")

# 2. obwod centroids -> point-in-hex join -> population-weighted SES aggregates
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
        "income_index_pln": weighted_mean(g, "income_index_pln"),
        "fam_pct_matki_samotne": weighted_mean(g, "fam_pct_matki_samotne"),
        "n_obwody": len(g),
    })
ses = pd.DataFrame(rows)
ses.to_csv(f"{BASE_DIR}/lodz_hex_ses.csv", index=False)
print(f"wrote SES aggregates for {len(ses)} hexes (of {len(hex_grid)} total, "
      f"{len(hex_grid) - len(ses)} hexes have no obwod centroid inside -- edge/outer hexes)")
