"""Write the 20 accessibility columns (lodz_accessibility_wide.csv) into
lodz_accessibility.gpkg's obwody_spisowe table, matched on OBWOD = id.
Same GPKG-via-sqlite3 pattern as ses_income_lodz/join_family_household_stats.py
(needs mod_spatialite loaded -- GPKG triggers call ST_IsEmpty on UPDATE).

Usage: py join_accessibility.py
"""
import csv
import sqlite3
from pathlib import Path

BASE = Path(__file__).parent
GPKG = BASE / "lodz_accessibility.gpkg"
WIDE_CSV = BASE / "lodz_accessibility_wide.csv"
SPATIALITE_DLL = r"C:\Program Files\QGIS 3.44.11\bin\mod_spatialite.dll"

with open(WIDE_CSV, encoding="utf-8", newline="") as f:
    rows = list(csv.DictReader(f))

acc_cols = [c for c in rows[0].keys() if c not in ("id", "income_index_pln", "population", "fam_pct_matki_samotne")]

conn = sqlite3.connect(GPKG)
conn.enable_load_extension(True)
conn.load_extension(SPATIALITE_DLL)
cur = conn.cursor()

existing = {r[1] for r in cur.execute("PRAGMA table_info(obwody_spisowe)")}
for col in acc_cols:
    if col not in existing:
        cur.execute(f"ALTER TABLE obwody_spisowe ADD COLUMN {col} INTEGER")
conn.commit()

updated = 0
for row in rows:
    vals = [int(row[c]) for c in acc_cols]
    cur.execute(
        f"UPDATE obwody_spisowe SET {', '.join(c + ' = ?' for c in acc_cols)} WHERE CAST(OBWOD AS TEXT) = ?",
        vals + [row["id"]],
    )
    updated += cur.rowcount
conn.commit()

total = cur.execute("SELECT COUNT(*) FROM obwody_spisowe").fetchone()[0]
matched = cur.execute("SELECT COUNT(*) FROM obwody_spisowe WHERE total_30min IS NOT NULL").fetchone()[0]
conn.close()
print(f"updated {updated} rows, total_30min matched {matched}/{total}")
