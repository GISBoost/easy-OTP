"""Backfill pis_proc (% PiS votes) for Lodz/Krakow, whose obwody_glosowania layer lost the
raw committee vote fields during the original pipeline (only valid_votes + income_index_pln
were retained -- see HANDOFF.md). Re-derives it from the official nationwide PKW CSV
(same file used originally for income_index_pln, re-downloaded since the local copy was
deleted during cleanup) and writes it into both obwody_glosowania and obwody_spisowe
(joined via precinct_nr = Nr komisji, same key used for the original income join).

Usage: python backfill_pis_share.py
"""
import csv
import sqlite3
from pathlib import Path

BASE = Path(__file__).parent
PKW_CSV = BASE / "tmp_pkw" / "wyniki_gl_na_listy_po_obwodach_sejm_utf8.csv"
SPATIALITE_DLL = r"C:\Program Files\QGIS 3.44.11\bin\mod_spatialite.dll"

IDX_PRECINCT, IDX_TERYT_GMINA, IDX_VALID_VOTES, IDX_PIS = 0, 2, 31, 35
CITY_TERYT = {"lodz": "106101", "krakow": "126101"}

for city, teryt in CITY_TERYT.items():
    rows_by_precinct = {}
    with open(PKW_CSV, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f, delimiter=";")
        header = next(reader)
        assert "PRAWO I SPRAWIEDLIWO" in header[IDX_PIS], header[IDX_PIS]
        for row in reader:
            if not row or row[IDX_TERYT_GMINA] != teryt:
                continue
            total_valid = int(row[IDX_VALID_VOTES])
            if total_valid == 0:
                continue
            pis_votes = int(row[IDX_PIS]) if row[IDX_PIS] else 0
            rows_by_precinct[row[IDX_PRECINCT]] = round(100.0 * pis_votes / total_valid, 2)
    print(f"{city}: {len(rows_by_precinct)} precincts with pis_proc")

    conn = sqlite3.connect(BASE / f"{city}.gpkg")
    conn.enable_load_extension(True)
    conn.load_extension(SPATIALITE_DLL)
    cur = conn.cursor()

    for table, key_col in [("obwody_glosowania", None), ("obwody_spisowe", "precinct_nr")]:
        cols = {r[1] for r in cur.execute(f"PRAGMA table_info({table})")}
        if "pis_proc" not in cols:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN pis_proc REAL")
    # figure out the precinct-number column actually present in obwody_glosowania
    glos_cols = {r[1] for r in cur.execute("PRAGMA table_info(obwody_glosowania)")}
    glos_precinct_col = "NR_OBWODU" if "NR_OBWODU" in glos_cols else "nr_obwodu"
    conn.commit()

    updated_glos = 0
    for precinct_nr, pis_proc in rows_by_precinct.items():
        cur.execute(f"UPDATE obwody_glosowania SET pis_proc = ? WHERE CAST({glos_precinct_col} AS TEXT) = ?", (pis_proc, precinct_nr))
        updated_glos += cur.rowcount
    updated_spis = 0
    for precinct_nr, pis_proc in rows_by_precinct.items():
        cur.execute("UPDATE obwody_spisowe SET pis_proc = ? WHERE CAST(precinct_nr AS TEXT) = ?", (pis_proc, precinct_nr))
        updated_spis += cur.rowcount
    conn.commit()

    total_glos = cur.execute("SELECT COUNT(*) FROM obwody_glosowania").fetchone()[0]
    total_spis = cur.execute("SELECT COUNT(*) FROM obwody_spisowe").fetchone()[0]
    matched_spis = cur.execute("SELECT COUNT(*) FROM obwody_spisowe WHERE pis_proc IS NOT NULL").fetchone()[0]
    conn.close()
    print(f"{city}: obwody_glosowania updated {updated_glos}/{total_glos}, "
          f"obwody_spisowe matched {matched_spis}/{total_spis}")
