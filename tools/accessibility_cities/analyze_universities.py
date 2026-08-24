"""Join uni_accessibility.csv (P50, r5r) with hex population, determine the
dominant university per hex (most buildings reachable in <=30 min) and a
population tercile, assign a bivariate color, write into <city>_hex500.gpkg.
Generalized version of accessibility_lodz/join_students_results.py -- works
for 2 or 3 universities (reads the slugmap, doesn't assume exactly 3).

Usage: py analyze_universities.py <city>
"""
import csv
import json
import sqlite3
import sys
from pathlib import Path

CITY = sys.argv[1]
BASE = Path(__file__).parent
CITY_DIR = BASE / CITY
OUT = CITY_DIR / "out"
OUT.mkdir(exist_ok=True)
GPKG = CITY_DIR / f"{CITY}_hex500.gpkg"
SPATIALITE_DLL = r"C:\Program Files\QGIS 3.44.11\bin\mod_spatialite.dll"
PRIMARY_CUTOFF = 30
CUTOFFS = [15, 30, 45, 60]

with open(CITY_DIR / f"{CITY}_uni_destinations_slugmap.json", encoding="utf-8") as f:
    slug_to_name = json.load(f)  # {"Politechnika Warszawska": "opp0", ...}
name_by_slug = {v: k for k, v in slug_to_name.items()}
UNI_SLUGS = list(slug_to_name.values())

# fixed hue order per slot (0,1,2 -> blue/orange/green), consistent with the
# Lodz pilot's palette -- categorical hue assigned by position, never cycled
HUE_FAMILIES = [
    ["#dbe8f7", "#7ea6d6", "#2c5c94"],   # blue
    ["#fbe3d4", "#e8996b", "#b8531f"],   # orange
    ["#dcefdc", "#7fbf7f", "#2f7d2f"],   # green
]
NONE_SHADES = ["#e6e6e6", "#bdbdbd", "#8a8a8a"]
PALETTE = {slug: HUE_FAMILIES[i] for i, slug in enumerate(UNI_SLUGS)}
PALETTE["none"] = NONE_SHADES

# 1. pivot long -> wide
wide = {}
with open(CITY_DIR / f"{CITY}_uni_accessibility.csv", encoding="utf-8", newline="") as f:
    for row in csv.DictReader(f):
        wide.setdefault(row["id"], {})[f"{row['opportunity']}_{row['cutoff']}min"] = int(row["accessibility"])

# 2. population per hex
pop = {}
with open(CITY_DIR / f"{CITY}_hex_ses.csv", encoding="utf-8", newline="") as f:
    for row in csv.DictReader(f):
        pop[row["hex_id"]] = float(row["pop_20_29"]) if row["pop_20_29"] else 0.0

rows = []
for hex_id, acc in wide.items():
    p = pop.get(hex_id, 0.0)
    counts = {slug: acc.get(f"{slug}_{PRIMARY_CUTOFF}min", 0) for slug in UNI_SLUGS}
    best = max(counts, key=counts.get)
    dominant = best if counts[best] > 0 else "none"
    row = {"hex_id": hex_id, "pop_20_29": p, "dominant_slug": dominant,
           "dominant_university": name_by_slug.get(dominant, "none")}
    row.update(acc)
    rows.append(row)

pop_vals = sorted(r["pop_20_29"] for r in rows if r["pop_20_29"] > 0)
t1 = pop_vals[len(pop_vals) // 3] if pop_vals else 0
t2 = pop_vals[2 * len(pop_vals) // 3] if pop_vals else 0


def tercile(p):
    if p <= 0:
        return 0
    return 0 if p <= t1 else (1 if p <= t2 else 2)


for row in rows:
    tier = tercile(row["pop_20_29"])
    row["pop_tercile"] = tier
    row["biv_color"] = PALETTE[row["dominant_slug"]][tier]

acc_cols = [f"{slug}_{t}min" for slug in UNI_SLUGS + ["total"] for t in CUTOFFS]
wide_csv = OUT / f"{CITY}_uni_wide.csv"
fieldnames = ["hex_id", "pop_20_29", "pop_tercile", "dominant_university", "biv_color"] + acc_cols
with open(wide_csv, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    for row in rows:
        w.writerow({k: row.get(k) for k in fieldnames})
print(f"{CITY}: wrote {len(rows)} rows to {wide_csv}")

by_dom = {}
pop_by_dom = {}
no_access = 0
pop_no_access = 0.0
for r in rows:
    if r["pop_20_29"] > 0:
        by_dom[r["dominant_university"]] = by_dom.get(r["dominant_university"], 0) + 1
        pop_by_dom[r["dominant_university"]] = pop_by_dom.get(r["dominant_university"], 0.0) + r["pop_20_29"]
        if r["dominant_university"] == "none":
            no_access += 1
            pop_no_access += r["pop_20_29"]
total_with_pop = sum(by_dom.values())
total_pop = sum(pop_by_dom.values())
pct_no_access = 100 * no_access / total_with_pop if total_with_pop else 0
# population-weighted version: a sparse edge hex with 1 student and a dense
# hex with 500 students should NOT count equally -- Michal's instruction,
# since the unweighted hex-count metric can be noise-dominated by near-empty
# peripheral hexes that happen to have >0 (but tiny) student population.
pct_pop_no_access = 100 * pop_no_access / total_pop if total_pop else 0
print(f"{CITY}: dominant university (of {total_with_pop} hexes with students): {by_dom}")
print(f"{CITY}: {pct_no_access:.1f}% of student-population HEXES have NO university within {PRIMARY_CUTOFF} min")
print(f"{CITY}: {pct_pop_no_access:.1f}% of the actual 20-29 POPULATION ({pop_no_access:.0f}/{total_pop:.0f}) has NO university within {PRIMARY_CUTOFF} min")

with open(OUT / f"{CITY}_uni_summary.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["dominant_university", "n_hexes", "pop_20_29"])
    for k, v in by_dom.items():
        w.writerow([k, v, round(pop_by_dom.get(k, 0))])
    w.writerow(["TOTAL_with_students", total_with_pop, round(total_pop)])
    w.writerow(["PCT_no_access_30min", round(pct_no_access, 1), ""])
    w.writerow(["PCT_POP_no_access_30min", round(pct_pop_no_access, 1), ""])

# 3. write to gpkg
conn = sqlite3.connect(GPKG)
conn.enable_load_extension(True)
conn.load_extension(SPATIALITE_DLL)
cur = conn.cursor()
existing = {r[1] for r in cur.execute("PRAGMA table_info(hex500)")}
for col in acc_cols + ["pop_tercile"]:
    if col not in existing:
        cur.execute(f"ALTER TABLE hex500 ADD COLUMN {col} INTEGER")
for col in ("pop_20_29",):
    if col not in existing:
        cur.execute(f"ALTER TABLE hex500 ADD COLUMN {col} REAL")
for col in ("dominant_university", "biv_color"):
    if col not in existing:
        cur.execute(f"ALTER TABLE hex500 ADD COLUMN {col} TEXT")
conn.commit()

all_cols = acc_cols + ["pop_tercile", "pop_20_29", "dominant_university", "biv_color"]
updated = 0
for row in rows:
    vals = [row.get(c) for c in all_cols]
    cur.execute(f"UPDATE hex500 SET {', '.join(c + ' = ?' for c in all_cols)} WHERE hex_id = ?",
                vals + [int(row["hex_id"])])
    updated += cur.rowcount
conn.commit()
conn.close()
print(f"{CITY}: updated {updated} hex500 rows")
