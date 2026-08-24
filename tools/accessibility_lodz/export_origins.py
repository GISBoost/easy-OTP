"""Export obwody_spisowe centroids (id, lon/lat WGS84, income, population) as the
r5r accessibility() origins/destinations points table. Read-only against lodz.gpkg.

Usage: py export_origins.py <out_csv>
"""
import sys

import geopandas as gpd

OUT_CSV = sys.argv[1]
SES_GPKG = "../ses_income_lodz/lodz.gpkg"

g = gpd.read_file(SES_GPKG, layer="obwody_spisowe")
centroids = g.geometry.centroid.to_crs(4326)
out = g[["OBWOD", "income_index_pln", "population", "fam_pct_matki_samotne"]].copy()
out["id"] = out["OBWOD"].astype(str)
out["lon"] = centroids.x
out["lat"] = centroids.y
out = out[["id", "lon", "lat", "income_index_pln", "population", "fam_pct_matki_samotne"]]
out = out.dropna(subset=["lon", "lat"])
out.to_csv(OUT_CSV, index=False)
print(f"wrote {len(out)} origin points to {OUT_CSV}")
