"""Extract per-obwod-spisowy category breakdowns (family types, household composition,
children counts, household size) from the flat national GUS NSP2021 xlsx files dropped
into docs/gis/, for all 6 cities in one pass per file.

Unlike ludnosc_nsp_2021.xlsx (per-voivodeship sheets, needs prepare_student_layer-style
parsing), these files are already flat national tables with a "Dane - obwody spisowe"
sheet: (Kod TERYT gminy, Nazwa gminy, Numer rejonu statystyczny, Numer obwodu spisowego,
<category column>, <count column>) -- "Numer obwodu spisowego" already matches the OBWOD
key used in the *.gpkg layers (SU_BREC_2021_OBW's OBWOD field), no key-building needed.

Usage: python extract_family_household_stats.py <input_xlsx> <out_dir> <out_prefix>
Writes one CSV per city: <out_dir>/<city>_<out_prefix>.csv
"""
import csv
import sys
from pathlib import Path

import openpyxl

IN_XLSX = Path(sys.argv[1])
OUT_DIR = Path(sys.argv[2])
OUT_PREFIX = sys.argv[3]

CITY_GMINA = {
    "lodz": ["1061029", "1061039", "1061049", "1061059", "1061069"],
    "warszawa": [f"1465{n:03d}" for n in range(28, 199, 10)],
    "krakow": ["1261029", "1261039", "1261049", "1261059"],
    "poznan": ["3064029", "3064039", "3064049", "3064059", "3064069"],
    "gdansk": ["2261011"],
    "szczecin": ["3262011"],
}
GMINA_TO_CITY = {g: city for city, gs in CITY_GMINA.items() for g in gs}

wb = openpyxl.load_workbook(IN_XLSX, read_only=True, data_only=True)
ws = wb["Dane - obwody spisowe"]

rows_iter = ws.iter_rows(values_only=True)
header = next(rows_iter)
print("header:", header)
# columns: Kod TERYT gminy, Nazwa gminy, Numer rejonu statystycznego, Numer obwodu spisowego, <category>, <count>
assert header[0] == "Kod TERYT gminy" and header[3] == "Numer obwodu spisowego"
category_col_name = header[4]

# city -> obwod -> category -> count
data: dict[str, dict[str, dict[str, int]]] = {c: {} for c in CITY_GMINA}
all_categories: dict[str, set[str]] = {c: set() for c in CITY_GMINA}
n_rows = 0

for row in rows_iter:
    n_rows += 1
    gmina = row[0]
    city = GMINA_TO_CITY.get(gmina)
    if city is None:
        continue
    obwod = str(row[3]).strip()
    category = str(row[4]).strip()
    count = row[5] or 0
    data[city].setdefault(obwod, {})[category] = int(count)
    all_categories[city].add(category)

wb.close()
print(f"scanned {n_rows} rows")

OUT_DIR.mkdir(parents=True, exist_ok=True)
for city, obwod_map in data.items():
    if not obwod_map:
        print(f"  {city}: no rows found (check GMINA codes)")
        continue
    categories = sorted(all_categories[city])
    out_path = OUT_DIR / f"{city}_{OUT_PREFIX}.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["OBWOD", *categories, "total", *[f"pct_{c}" for c in categories], "dominant"])
        for obwod, cat_counts in sorted(obwod_map.items()):
            counts = [cat_counts.get(c, 0) for c in categories]
            total = sum(counts)
            pcts = [round(100.0 * c / total, 1) if total else 0.0 for c in counts]
            dominant = categories[counts.index(max(counts))] if total else ""
            w.writerow([obwod, *counts, total, *pcts, dominant])
    print(f"  {city}: {len(obwod_map)} obwody -> {out_path}")
