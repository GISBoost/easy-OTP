"""Pivot lodz_hex_accessibility.csv to wide, merge with lodz_hex_ses.csv (population-
weighted income etc. per hex), compute has_access_* flags + a hex-level population
coverage summary, and write the accessibility + SES columns into lodz_hex500.gpkg's
hex500 layer. Mirrors analyze_accessibility.py + compute_population_coverage.py +
join_accessibility.py, but for hex units instead of obwody spisowe.

Usage: py join_hex_results.py
"""
import csv
import sqlite3
import statistics
from pathlib import Path

BASE = Path(__file__).parent
ACC_CSV = BASE / "lodz_hex_accessibility.csv"
SES_CSV = BASE / "lodz_hex_ses.csv"
GPKG = BASE / "lodz_hex500.gpkg"
OUT = BASE / "out"
OUT.mkdir(exist_ok=True)
SPATIALITE_DLL = r"C:\Program Files\QGIS 3.44.11\bin\mod_spatialite.dll"

CATEGORIES = ["education", "health", "culture", "groceries", "total"]
CUTOFFS = [15, 30, 45, 60]

# 1. pivot long -> wide
wide = {}
with open(ACC_CSV, encoding="utf-8", newline="") as f:
    for row in csv.DictReader(f):
        d = wide.setdefault(row["id"], {})
        d[f"{row['opportunity']}_{row['cutoff']}min"] = int(row["accessibility"])

# 2. SES aggregates per hex
ses = {}
with open(SES_CSV, encoding="utf-8", newline="") as f:
    for row in csv.DictReader(f):
        ses[row["hex_id"]] = row

# 3. has_access flags + assemble rows
acc_cols = [f"{c}_{t}min" for c in CATEGORIES for t in CUTOFFS]
flag_cols = [f"has_access_{c}_{t}min" for c in CATEGORIES for t in CUTOFFS]
rows = []
for hex_id, acc in wide.items():
    row = {"hex_id": hex_id}
    row.update(acc)
    s = ses.get(hex_id)
    row["population"] = s["population"] if s else ""
    row["income_index_pln"] = s["income_index_pln"] if s else ""
    row["fam_pct_matki_samotne"] = s["fam_pct_matki_samotne"] if s else ""
    for c in CATEGORIES:
        for t in CUTOFFS:
            row[f"has_access_{c}_{t}min"] = 1 if acc.get(f"{c}_{t}min", 0) >= 1 else 0
    rows.append(row)

wide_csv = BASE / "lodz_hex_accessibility_wide.csv"
fieldnames = ["hex_id", "population", "income_index_pln", "fam_pct_matki_samotne"] + acc_cols + flag_cols
with open(wide_csv, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(rows)
print(f"wrote {len(rows)} rows to {wide_csv}")

# 4. city-wide population coverage (only hexes with matched SES/population)
total_pop = sum(float(r["population"]) for r in rows if r["population"])
summary = []
for c in CATEGORIES:
    for t in CUTOFFS:
        flag_col = f"has_access_{c}_{t}min"
        covered = sum(float(r["population"]) for r in rows if r["population"] and r[flag_col] == 1)
        summary.append({"category": c, "cutoff_min": t, "population_covered": round(covered),
                         "population_total": round(total_pop),
                         "pct_covered": round(100 * covered / total_pop, 1)})
with open(OUT / "lodz_hex_population_coverage_summary.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=["category", "cutoff_min", "population_covered",
                                        "population_total", "pct_covered"])
    w.writeheader()
    w.writerows(summary)

# 5. correlation check: income vs accessibility, hex-level (fewer, more uniform units)
print("\nH6 (hex500, n dotyczy hexow z dopasowanym SES): income vs total_Xmin")
for t in CUTOFFS:
    col = f"total_{t}min"
    pairs = [(float(r["income_index_pln"]), float(r[col])) for r in rows if r["income_index_pln"]]
    xs, ys = zip(*pairs)
    r = statistics.correlation(list(xs), list(ys))
    print(f"  {t:3d} min: r={r:+.3f} (n={len(pairs)})")

# 6. write accessibility + SES columns into hex500 gpkg
conn = sqlite3.connect(GPKG)
conn.enable_load_extension(True)
conn.load_extension(SPATIALITE_DLL)
cur = conn.cursor()
existing = {r[1] for r in cur.execute("PRAGMA table_info(hex500)")}
all_new_cols = acc_cols + flag_cols + ["population", "income_index_pln", "fam_pct_matki_samotne"]
for col in all_new_cols:
    if col not in existing:
        typ = "REAL" if col in ("population", "income_index_pln", "fam_pct_matki_samotne") else "INTEGER"
        cur.execute(f"ALTER TABLE hex500 ADD COLUMN {col} {typ}")
conn.commit()

updated = 0
for row in rows:
    cols = all_new_cols
    vals = [row.get(c) or None for c in cols]
    cur.execute(f"UPDATE hex500 SET {', '.join(c + ' = ?' for c in cols)} WHERE hex_id = ?",
                vals + [int(row["hex_id"])])
    updated += cur.rowcount
conn.commit()
total = cur.execute("SELECT COUNT(*) FROM hex500").fetchone()[0]
conn.close()
print(f"\nupdated {updated}/{total} hex500 rows in {GPKG}")
