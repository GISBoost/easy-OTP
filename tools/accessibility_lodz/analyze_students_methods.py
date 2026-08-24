"""Analyze Method A (widened-window percentiles) and Method C (P50 vs P85
reliability comparison) for the student -> university accessibility results.

Method A: intra-day variability -- spread (p95-p5) of accessibility across the
whole recorded service day (06:00-22:00), at each hex. High spread = this
origin's access to universities is inconsistent depending on when you leave;
low spread = consistently good (or consistently bad).

Method C: day-to-day reliability (Braga et al. 2026 style) -- % difference in
accessibility between the P85 ("bad day") and P50 ("typical day") realized
GTFS, same params otherwise. Negative = bad day hurts you.

Both correlated against distance-to-city-center (no income in this analysis --
dropped per this stage's scope, replaced by student population as the SES-
adjacent variable of interest). Read-only except for writing results into
lodz_hex500.gpkg.

Usage: py analyze_students_methods.py
"""
import csv
import sqlite3
import statistics
from pathlib import Path

import geopandas as gpd

BASE = Path(__file__).parent
OUT = BASE / "out"
OUT.mkdir(exist_ok=True)
GPKG = BASE / "lodz_hex500.gpkg"
SPATIALITE_DLL = r"C:\Program Files\QGIS 3.44.11\bin\mod_spatialite.dll"

CUTOFF = 30  # primary cutoff per Michal's instruction
UNIS = ["politechnika", "uniwersytet", "medyczny", "total"]

# ---------------------------------------------------------------------------
# distance-to-center per hex (reuse the same city-centroid definition as
# plot_correlations.py in the earlier obwod-level analysis)
# ---------------------------------------------------------------------------
hexes = gpd.read_file(GPKG, layer="hex500")
obwody = gpd.read_file(BASE.parent / "ses_income_lodz" / "lodz.gpkg", layer="obwody_spisowe")
city_center = obwody.geometry.union_all().centroid
hexes["dist_km"] = hexes.geometry.centroid.distance(city_center) / 1000.0
dist_by_hex = dict(zip(hexes["hex_id"].astype(str), hexes["dist_km"]))

# ---------------------------------------------------------------------------
# Method C: P50 vs P85, % difference at 30 min
# ---------------------------------------------------------------------------
def load_wide(csv_path):
    wide = {}
    with open(csv_path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if int(row["cutoff"]) != CUTOFF:
                continue
            if "percentile" in row and row["percentile"] not in ("50", ""):
                continue
            wide.setdefault(row["id"], {})[row["opportunity"]] = int(row["accessibility"])
    return wide

p50 = load_wide(BASE / "lodz_students_accessibility_P50.csv")
p85 = load_wide(BASE / "lodz_students_accessibility_C_p85.csv")

method_c_rows = []
for hex_id in p50:
    if hex_id not in p85:
        continue
    row = {"hex_id": hex_id, "dist_km": dist_by_hex.get(hex_id)}
    for u in UNIS:
        v50 = p50[hex_id].get(u, 0)
        v85 = p85[hex_id].get(u, 0)
        pct = None if v50 == 0 else round(100.0 * (v85 - v50) / v50, 1)
        row[f"{u}_p50"] = v50
        row[f"{u}_p85"] = v85
        row[f"{u}_pct_impact"] = pct
    method_c_rows.append(row)

with open(OUT / "lodz_students_method_C_p50_vs_p85.csv", "w", newline="", encoding="utf-8") as f:
    fieldnames = list(method_c_rows[0].keys())
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(method_c_rows)

impacts = [r["total_pct_impact"] for r in method_c_rows if r["total_pct_impact"] is not None]
print(f"Method C (30 min, 'total'): n={len(impacts)} hexes with nonzero P50 access")
print(f"  mean impact: {statistics.mean(impacts):+.1f}%  median: {statistics.median(impacts):+.1f}%")
dist_pairs = [(r["dist_km"], r["total_pct_impact"]) for r in method_c_rows if r["total_pct_impact"] is not None]
if len(dist_pairs) > 3:
    dxs, dys = zip(*dist_pairs)
    r_dist = statistics.correlation(list(dxs), list(dys))
    print(f"  correlation vs distance-to-center: r={r_dist:+.3f} (n={len(dist_pairs)})")

# ---------------------------------------------------------------------------
# Method A: intra-day spread (p95-p5) at 30 min, "total" opportunity
# ---------------------------------------------------------------------------
perc_by_hex = {}
with open(BASE / "lodz_students_accessibility_A_percentiles.csv", encoding="utf-8", newline="") as f:
    for row in csv.DictReader(f):
        if row["opportunity"] != "total" or int(row["cutoff"]) != CUTOFF:
            continue
        perc_by_hex.setdefault(row["id"], {})[row["percentile"]] = int(row["accessibility"])

method_a_rows = []
for hex_id, p in perc_by_hex.items():
    if not all(k in p for k in ("5", "25", "50", "75", "95")):
        continue
    # r5r percentiles are of TRAVEL TIME, not of accessibility: p5 = fastest
    # 5% of departures -> shortest travel time -> HIGHEST accessibility; p95 =
    # slowest departures -> LOWEST accessibility. So "good case" is p5, "bad
    # case" is p95 -- spread is p5-p95 (positive), not p95-p5.
    spread = p["5"] - p["95"]
    rel_spread = None if p["50"] == 0 else round(spread / p["50"], 3)
    method_a_rows.append({
        "hex_id": hex_id, "dist_km": dist_by_hex.get(hex_id),
        "p5": p["5"], "p25": p["25"], "p50": p["50"], "p75": p["75"], "p95": p["95"],
        "spread_p5_p95": spread, "rel_spread": rel_spread,
    })

with open(OUT / "lodz_students_method_A_percentile_spread.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(method_a_rows[0].keys()))
    w.writeheader()
    w.writerows(method_a_rows)

spreads = [(r["dist_km"], r["spread_p5_p95"]) for r in method_a_rows if r["p50"] > 0]
rel_spreads = [(r["dist_km"], r["rel_spread"]) for r in method_a_rows if r["p50"] > 0]
print(f"\nMethod A (30 min, 'total'): n={len(spreads)} hexes with nonzero p50 access")
if spreads:
    sxs, sys_ = zip(*spreads)
    print(f"  mean absolute spread (p5-p95): {statistics.mean(sys_):.1f} buildings")
    if len(spreads) > 3:
        r_a = statistics.correlation(list(sxs), list(sys_))
        print(f"  correlation (absolute spread) vs distance-to-center: r={r_a:+.3f}")
    rxs, rys = zip(*rel_spreads)
    print(f"  mean relative spread (spread/median): {statistics.mean(rys):.2f}")
    if len(rel_spreads) > 3:
        r_rel = statistics.correlation(list(rxs), list(rys))
        print(f"  correlation (relative spread) vs distance-to-center: r={r_rel:+.3f}")

# ---------------------------------------------------------------------------
# write both into the gpkg for mapping
# ---------------------------------------------------------------------------
conn = sqlite3.connect(GPKG)
conn.enable_load_extension(True)
conn.load_extension(SPATIALITE_DLL)
cur = conn.cursor()
existing = {r[1] for r in cur.execute("PRAGMA table_info(hex500)")}
for col in ("total_pct_impact_30min", "spread_p5_p95_30min", "rel_spread_30min"):
    if col not in existing:
        cur.execute(f"ALTER TABLE hex500 ADD COLUMN {col} REAL")
conn.commit()

impact_by_hex = {r["hex_id"]: r["total_pct_impact"] for r in method_c_rows}
spread_by_hex = {r["hex_id"]: r["spread_p5_p95"] for r in method_a_rows}
rel_spread_by_hex = {r["hex_id"]: r["rel_spread"] for r in method_a_rows}
updated = 0
for hex_id in set(impact_by_hex) | set(spread_by_hex):
    cur.execute(
        "UPDATE hex500 SET total_pct_impact_30min = ?, spread_p5_p95_30min = ?, rel_spread_30min = ? WHERE hex_id = ?",
        (impact_by_hex.get(hex_id), spread_by_hex.get(hex_id), rel_spread_by_hex.get(hex_id), int(hex_id)),
    )
    updated += cur.rowcount
conn.commit()
conn.close()
print(f"\nupdated {updated} hex500 rows with Method A/C columns")
