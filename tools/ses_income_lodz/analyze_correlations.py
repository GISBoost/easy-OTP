"""Test 5 SES/demographic correlation hypotheses across all 6 cities, at obwod-spisowy
(census tract) granularity. Read-only -- does not modify the .gpkg files.

H1: income_index_pln vs fam_pct_matki_samotne       (expect negative: poorer -> more single mothers)
H2: income_index_pln vs hh_avg_size                  (expect negative: richer areas = smaller households)
H3: income_index_pln vs fam_avg_children             (expect negative: richer areas = fewer children)
H4: pis_proc vs fam_avg_children                     (expect positive: PiS-leaning areas = larger families)
H5: income_index_pln vs hh_pct_jednoosobowe          (expect positive: richer areas = more single-person households)

H4 needs raw party vote share. For warszawa/poznan/gdansk/szczecin (tileset source) it's read
from obwody_glosowania and joined in-memory via precinct_nr = number (verified unique per
city). For lodz/krakow it's backfilled directly onto obwody_spisowe by backfill_pis_share.py
(re-derived from the official PKW CSV, since the original pipeline didn't retain it).

Usage: python analyze_correlations.py
"""
import sqlite3
import statistics
from pathlib import Path

BASE = Path(__file__).parent
CITIES = ["lodz", "krakow", "warszawa", "poznan", "gdansk", "szczecin"]
# pis_proc now backfilled for lodz/krakow too (backfill_pis_share.py, 2026-08-22) and stored
# directly on obwody_spisowe -- no in-memory join needed for those two any more.
HAS_PIS_DIRECT_ON_SPISOWE = {"lodz", "krakow"}
HAS_PIS_ON_GLOSOWANIA = {"warszawa", "poznan", "gdansk", "szczecin"}

HYPOTHESES = [
    ("H1", "income_index_pln", "fam_pct_matki_samotne", "dochód vs %matek samotnych", "-"),
    ("H2", "income_index_pln", "hh_avg_size", "dochód vs śr. wielkość gospodarstwa", "-"),
    ("H3", "income_index_pln", "fam_avg_children", "dochód vs śr. liczba dzieci", "-"),
    ("H4", "pis_proc", "fam_avg_children", "%PiS vs śr. liczba dzieci", "+"),
    ("H5", "income_index_pln", "hh_pct_jednoosobowe", "dochód vs %gosp. jednoosobowych", "+"),
]


def corr(xs, ys):
    if len(xs) < 3 or len(set(xs)) < 2 or len(set(ys)) < 2:
        return None, len(xs)
    return round(statistics.correlation(xs, ys), 3), len(xs)


results = {h[0]: {} for h in HYPOTHESES}
pooled = {h[0]: ([], []) for h in HYPOTHESES}

for city in CITIES:
    conn = sqlite3.connect(f"file:{BASE / (city + '.gpkg')}?mode=ro", uri=True)
    cur = conn.cursor()
    has_pis_col = city in HAS_PIS_DIRECT_ON_SPISOWE
    cols = ["OBWOD", "precinct_nr", "income_index_pln", "fam_pct_matki_samotne",
            "hh_avg_size", "fam_avg_children", "hh_pct_jednoosobowe"]
    if has_pis_col:
        cols.append("pis_proc")
    rows = cur.execute(f"SELECT {','.join(cols)} FROM obwody_spisowe").fetchall()
    numeric_cols = {"income_index_pln", "fam_pct_matki_samotne", "hh_avg_size",
                     "fam_avg_children", "hh_pct_jednoosobowe", "pis_proc"}
    data = []
    for r in rows:
        row = dict(zip(cols, r))
        for c in numeric_cols & row.keys():
            if row[c] is not None:
                row[c] = float(row[c])
        data.append(row)

    if city in HAS_PIS_ON_GLOSOWANIA:
        pis_by_precinct = {}
        for number, pis_proc in cur.execute("SELECT number, pis_proc FROM obwody_glosowania"):
            pis_by_precinct[str(int(number))] = pis_proc
        for row in data:
            pn = row["precinct_nr"]
            row["pis_proc"] = pis_by_precinct.get(str(int(float(pn)))) if pn is not None else None
    elif not has_pis_col:
        for row in data:
            row["pis_proc"] = None
    conn.close()

    for hid, xcol, ycol, _, _ in HYPOTHESES:
        pairs = [(r[xcol], r[ycol]) for r in data if r[xcol] is not None and r[ycol] is not None]
        if not pairs:
            results[hid][city] = (None, 0)
            continue
        xs, ys = zip(*pairs)
        results[hid][city] = corr(list(xs), list(ys))
        pooled[hid][0].extend(xs)
        pooled[hid][1].extend(ys)

COLW = 16
print(f"{'Hipoteza':42s} " + " ".join(f"{c:>{COLW}s}" for c in CITIES))
for hid, xcol, ycol, label, expected in HYPOTHESES:
    line = f"{hid} {label:38s} "
    cells = []
    for city in CITIES:
        r, n = results[hid][city]
        cell = "n/a" if r is None else f"{r:+.3f} (n={n})"
        cells.append(cell.rjust(COLW))
    print(line + " ".join(cells) + f"   [oczekiwany kierunek: {expected}]")

print()
print("Pooled (wszystkie miasta razem, UWAGA: myli różnice między-miastowe z efektem")
print("wewnątrz-miejskim -- traktować jako ciekawostkę, nie dowód; wiarygodne są wiersze per-miasto):")
for hid, xcol, ycol, label, expected in HYPOTHESES:
    xs, ys = pooled[hid]
    r, n = corr(xs, ys) if xs else (None, 0)
    cell = "n/a" if r is None else f"{r:+.3f} (n={n})"
    print(f"  {hid} {label:38s} {cell}")
