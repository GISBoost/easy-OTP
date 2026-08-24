"""Join the P50 student->university accessibility result with hex student
population, determine the dominant university per hex (by 30-min accessible-
building count) and a population tercile, assign a bivariate color (hue =
dominant university, shade = population tercile), and write everything into
lodz_hex500.gpkg's hex500 layer as new columns.

Usage: py join_students_results.py
"""
import csv
import sqlite3
import statistics
from pathlib import Path

BASE = Path(__file__).parent
ACC_CSV = BASE / "lodz_students_accessibility_P50.csv"
POP_CSV = BASE / "lodz_hex_students.csv"
GPKG = BASE / "lodz_hex500.gpkg"
OUT = BASE / "out"
OUT.mkdir(exist_ok=True)
SPATIALITE_DLL = r"C:\Program Files\QGIS 3.44.11\bin\mod_spatialite.dll"

UNIS = ["politechnika", "uniwersytet", "medyczny"]
CUTOFFS = [15, 30, 45, 60]
PRIMARY_CUTOFF = 30

# bivariate palette: hue per university (3 shades each, light->dark = low->high pop tercile)
PALETTE = {
    "politechnika": ["#dbe8f7", "#7ea6d6", "#2c5c94"],   # blue family
    "uniwersytet":  ["#fbe3d4", "#e8996b", "#b8531f"],   # orange family
    "medyczny":     ["#dcefdc", "#7fbf7f", "#2f7d2f"],   # green family
    "none":         ["#e6e6e6", "#bdbdbd", "#8a8a8a"],   # grey = no access to any within cutoff
}

# 1. pivot long -> wide
wide = {}
with open(ACC_CSV, encoding="utf-8", newline="") as f:
    for row in csv.DictReader(f):
        d = wide.setdefault(row["id"], {})
        d[f"{row['opportunity']}_{row['cutoff']}min"] = int(row["accessibility"])

# 2. population per hex
pop = {}
with open(POP_CSV, encoding="utf-8", newline="") as f:
    for row in csv.DictReader(f):
        pop[row["hex_id"]] = float(row["pop_20_29"])

rows = []
for hex_id, acc in wide.items():
    p = pop.get(hex_id, 0.0)
    counts = {u: acc.get(f"{u}_{PRIMARY_CUTOFF}min", 0) for u in UNIS}
    best_u = max(counts, key=counts.get)
    dominant = best_u if counts[best_u] > 0 else "none"
    # how many of the 3 universities are reachable at all (set-overlap magnitude,
    # not identity) -- the print-map-friendly encoding of "how much do zones overlap"
    uni_count = sum(1 for u in UNIS if counts[u] > 0)
    row = {"hex_id": hex_id, "pop_20_29": p, "dominant_university": dominant,
           "uni_count_30min": uni_count}
    row.update(acc)
    rows.append(row)

# 3. population terciles, computed only over hexes with pop_20_29 > 0
pop_vals = sorted(r["pop_20_29"] for r in rows if r["pop_20_29"] > 0)
if pop_vals:
    t1 = pop_vals[len(pop_vals) // 3]
    t2 = pop_vals[2 * len(pop_vals) // 3]
else:
    t1 = t2 = 0

def pop_tercile(p):
    if p <= 0:
        return 0
    return 0 if p <= t1 else (1 if p <= t2 else 2)

for row in rows:
    tier = pop_tercile(row["pop_20_29"])
    row["pop_tercile"] = tier
    row["biv_color"] = PALETTE[row["dominant_university"]][tier]

acc_cols = [f"{u}_{t}min" for u in UNIS + ["total"] for t in CUTOFFS]
wide_csv = BASE / "lodz_students_wide.csv"
fieldnames = ["hex_id", "pop_20_29", "pop_tercile", "dominant_university", "biv_color",
              "uni_count_30min"] + acc_cols
with open(wide_csv, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(rows)
print(f"wrote {len(rows)} rows to {wide_csv}")

by_dom = {}
for r in rows:
    if r["pop_20_29"] > 0:
        by_dom[r["dominant_university"]] = by_dom.get(r["dominant_university"], 0) + 1
print(f"dominant university (of {len(pop_vals)} hexes with students): {by_dom}")

# 4. write to gpkg
conn = sqlite3.connect(GPKG)
conn.enable_load_extension(True)
conn.load_extension(SPATIALITE_DLL)
cur = conn.cursor()
existing = {r[1] for r in cur.execute("PRAGMA table_info(hex500)")}
new_int_cols = acc_cols + ["pop_tercile", "uni_count_30min"]
new_real_cols = ["pop_20_29"]
new_text_cols = ["dominant_university", "biv_color"]
for col in new_int_cols:
    if col not in existing:
        cur.execute(f"ALTER TABLE hex500 ADD COLUMN {col} INTEGER")
for col in new_real_cols:
    if col not in existing:
        cur.execute(f"ALTER TABLE hex500 ADD COLUMN {col} REAL")
for col in new_text_cols:
    if col not in existing:
        cur.execute(f"ALTER TABLE hex500 ADD COLUMN {col} TEXT")
conn.commit()

all_cols = new_int_cols + new_real_cols + new_text_cols
updated = 0
for row in rows:
    vals = [row.get(c) for c in all_cols]
    cur.execute(f"UPDATE hex500 SET {', '.join(c + ' = ?' for c in all_cols)} WHERE hex_id = ?",
                vals + [int(row["hex_id"])])
    updated += cur.rowcount
conn.commit()
total = cur.execute("SELECT COUNT(*) FROM hex500").fetchone()[0]
conn.close()
print(f"updated {updated}/{total} hex500 rows")
