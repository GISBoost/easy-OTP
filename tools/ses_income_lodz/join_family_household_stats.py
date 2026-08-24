"""Compute derived family/household summary fields per obwod spisowy from the 4 raw
stats/<city>_*.csv files (produced by extract_family_household_stats.py) and write them
directly into <city>.gpkg's obwody_spisowe layer via sqlite3 (GPKG is plain SQLite --
avoids the QGIS native:joinattributestable field-order bug hit earlier in this project).

Usage: python join_family_household_stats.py <city>
"""
import csv
import sqlite3
import sys
from pathlib import Path

CITY = sys.argv[1]
BASE = Path(__file__).parent
STATS = BASE / "stats"
GPKG = BASE / f"{CITY}.gpkg"

FAMILY_TYPE_COLS = {
    "malzenstwa_bez_dzieci": "małżeństwa bez dzieci",
    "malzenstwa_z_dziecmi": "małżeństwa z dziećmi",
    "matki_samotne": "samotne matki z dziećmi",
    "ojcowie_samotni": "samotni ojcowie z dziećmi",
    "kohabitacja_bez_dzieci": "związki niesformalizowane bez dzieci",
    "kohabitacja_z_dziecmi": "związki niesformalizowane z dziećmi",
}
HH_COMP_COLS = {
    "jednorodzinne": "jednorodzinne",
    "dwurodzinne": "dwurodzinne",
    "trzy_plus_rodzinne": "trzy i więcej rodzinne",
    "jednoosobowe": "nierodzinne - jednosobowe",
    "wieloosobowe_nierodzinne": "nierodzinne - wieloosobowe",
}
HH_SIZE_MIDPOINT = {"1": 1, "2": 2, "3": 3, "4": 4, "5 i więcej": 6}
CHILDREN_MIDPOINT = {"0": 0, "1": 1, "2": 2, "3": 3, "4 i więcej": 5}

NEW_FIELDS = {
    "fam_total": "INTEGER",
    "fam_pct_malzenstwa_bez_dzieci": "REAL",
    "fam_pct_malzenstwa_z_dziecmi": "REAL",
    "fam_pct_matki_samotne": "REAL",
    "fam_pct_ojcowie_samotni": "REAL",
    "fam_pct_kohabitacja_bez_dzieci": "REAL",
    "fam_pct_kohabitacja_z_dziecmi": "REAL",
    "fam_dominant_type": "TEXT",
    "hh_total": "INTEGER",
    "hh_pct_jednoosobowe": "REAL",
    "hh_pct_jednorodzinne": "REAL",
    "hh_pct_dwurodzinne_plus": "REAL",
    "hh_dominant_type": "TEXT",
    "hh_avg_size": "REAL",
    "hh_pct_5plus_osob": "REAL",
    "fam_avg_children": "REAL",
    "fam_pct_bez_dzieci": "REAL",
    "fam_pct_3plus_dzieci": "REAL",
}


def read_raw(path):
    """OBWOD -> {category: count}"""
    out = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            obwod = row["OBWOD"]
            out[obwod] = {k: int(v) for k, v in row.items() if k not in ("OBWOD", "total", "dominant") and not k.startswith("pct_")}
    return out


fam = read_raw(STATS / f"{CITY}_family_types.csv")
hhc = read_raw(STATS / f"{CITY}_hh_composition.csv")
hhs = read_raw(STATS / f"{CITY}_hh_size.csv")
chd = read_raw(STATS / f"{CITY}_children_count.csv")

all_obwody = set(fam) | set(hhc) | set(hhs) | set(chd)
print(f"{CITY}: {len(all_obwody)} obwody with any family/household data")

rows = {}
for obwod in all_obwody:
    r = {}

    f = fam.get(obwod, {})
    ftotal = sum(f.values())
    r["fam_total"] = ftotal
    for field, cat in FAMILY_TYPE_COLS.items():
        r[f"fam_pct_{field}"] = round(100.0 * f.get(cat, 0) / ftotal, 1) if ftotal else None
    r["fam_dominant_type"] = max(f, key=f.get) if f else None

    h = hhc.get(obwod, {})
    htotal = sum(h.values())
    r["hh_total"] = htotal
    r["hh_pct_jednoosobowe"] = round(100.0 * h.get("nierodzinne - jednosobowe", 0) / htotal, 1) if htotal else None
    r["hh_pct_jednorodzinne"] = round(100.0 * h.get("jednorodzinne", 0) / htotal, 1) if htotal else None
    two_plus = h.get("dwurodzinne", 0) + h.get("trzy i więcej rodzinne", 0)
    r["hh_pct_dwurodzinne_plus"] = round(100.0 * two_plus / htotal, 1) if htotal else None
    r["hh_dominant_type"] = max(h, key=h.get) if h else None

    s = hhs.get(obwod, {})
    stotal = sum(s.values())
    if stotal:
        r["hh_avg_size"] = round(sum(HH_SIZE_MIDPOINT[k] * v for k, v in s.items()) / stotal, 2)
        r["hh_pct_5plus_osob"] = round(100.0 * s.get("5 i więcej", 0) / stotal, 1)
    else:
        r["hh_avg_size"] = r["hh_pct_5plus_osob"] = None

    c = chd.get(obwod, {})
    ctotal = sum(c.values())
    if ctotal:
        r["fam_avg_children"] = round(sum(CHILDREN_MIDPOINT[k] * v for k, v in c.items()) / ctotal, 2)
        r["fam_pct_bez_dzieci"] = round(100.0 * c.get("0", 0) / ctotal, 1)
        r["fam_pct_3plus_dzieci"] = round(100.0 * (c.get("3", 0) + c.get("4 i więcej", 0)) / ctotal, 1)
    else:
        r["fam_avg_children"] = r["fam_pct_bez_dzieci"] = r["fam_pct_3plus_dzieci"] = None

    rows[obwod] = r

SPATIALITE_DLL = r"C:\Program Files\QGIS 3.44.11\bin\mod_spatialite.dll"

conn = sqlite3.connect(GPKG)
conn.enable_load_extension(True)
conn.load_extension(SPATIALITE_DLL)  # GPKG triggers call ST_IsEmpty etc. on UPDATE
cur = conn.cursor()
existing_cols = {row[1] for row in cur.execute("PRAGMA table_info(obwody_spisowe)")}
for field, sqltype in NEW_FIELDS.items():
    if field not in existing_cols:
        cur.execute(f"ALTER TABLE obwody_spisowe ADD COLUMN {field} {sqltype}")
conn.commit()

field_names = list(NEW_FIELDS)
set_clause = ", ".join(f"{f} = ?" for f in field_names)
updated = 0
for obwod, r in rows.items():
    values = [r[f] for f in field_names] + [obwod]
    cur.execute(f"UPDATE obwody_spisowe SET {set_clause} WHERE OBWOD = ?", values)
    updated += cur.rowcount

conn.commit()
total_features = cur.execute("SELECT COUNT(*) FROM obwody_spisowe").fetchone()[0]
matched = cur.execute("SELECT COUNT(*) FROM obwody_spisowe WHERE fam_total IS NOT NULL").fetchone()[0]
conn.close()
print(f"{CITY}: updated {updated} rows; {matched}/{total_features} obwody_spisowe features now have family/household data")
