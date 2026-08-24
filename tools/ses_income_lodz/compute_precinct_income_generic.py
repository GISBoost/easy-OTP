"""Estimate a per-capita income index for each voting precinct in a given city.

Generalized version of compute_precinct_income.py (same CBOS-derived weights,
same method) — takes the city's pre-filtered PKW results CSV and writes the
per-precinct income index CSV. See compute_precinct_income.py's docstring for
full methodology notes.

Usage: python compute_precinct_income_generic.py <results_csv> <out_csv>
"""
import csv
import sys
from pathlib import Path

RESULTS_CSV = Path(sys.argv[1])
OUT_CSV = Path(sys.argv[2])

MIDPOINTS = [1250, 1750, 2500, 3500, 4500]

CBOS_INCOME_PCT = {
    "PiS": [12, 18, 26, 12, 12],
    "KO": [4, 9, 26, 16, 25],
    "Lewica": [3, 5, 27, 15, 23],
    "Konfederacja": [4, 5, 18, 17, 25],
    "TrzeciaDroga": [6, 5, 20, 7, 25],
    "Ogolem": [7, 13, 23, 14, 17],
}


def mean_income(pcts: list[float]) -> float:
    valid = sum(pcts)
    return sum(p * m for p, m in zip(pcts, MIDPOINTS)) / valid


INCOME_BY_PARTY = {k: mean_income(v) for k, v in CBOS_INCOME_PCT.items()}

COMMITTEE_TO_PARTY = {
    "KOALICYJNY KOMITET WYBORCZY TRZECIA DROGA POLSKA 2050 SZYMONA HOŁOWNI - POLSKIE STRONNICTWO LUDOWE": "TrzeciaDroga",
    "KOMITET WYBORCZY NOWA LEWICA": "Lewica",
    "KOMITET WYBORCZY PRAWO I SPRAWIEDLIWOŚĆ": "PiS",
    "KOMITET WYBORCZY KONFEDERACJA WOLNOŚĆ I NIEPODLEGŁOŚĆ": "Konfederacja",
    "KOALICYJNY KOMITET WYBORCZY KOALICJA OBYWATELSKA PO .N IPL ZIELONI": "KO",
}

with open(RESULTS_CSV, encoding="utf-8-sig", newline="") as f:
    reader = csv.reader(f, delimiter=";")
    header = next(reader)
    committee_cols = [i for i, h in enumerate(header) if h.strip('"').isupper() and "KOMITET" in h]
    matched = {header[i].strip('"'): COMMITTEE_TO_PARTY.get(header[i].strip('"'), "FALLBACK") for i in committee_cols}
    n_major = sum(1 for v in matched.values() if v != "FALLBACK")
    print(f"committee columns: {len(committee_cols)}, matched to named party: {n_major} (expect 5)")
    if n_major != 5:
        raise SystemExit(f"expected 5 major-party committee matches, got {n_major} -- header text may differ, check COMMITTEE_TO_PARTY")

    idx_precinct = 0
    idx_valid_votes = 31
    idx_gmina = 3

    rows_out = []
    for row in reader:
        if not row or not row[0]:
            continue
        precinct_nr = row[idx_precinct]
        gmina = row[idx_gmina].strip('"')
        total_valid = int(row[idx_valid_votes])
        if total_valid == 0:
            continue
        weighted_sum = 0.0
        for ci in committee_cols:
            raw = row[ci].strip()
            votes = int(raw) if raw else 0
            if votes == 0:
                continue
            party_key = COMMITTEE_TO_PARTY.get(header[ci].strip('"'), "Ogolem")
            weighted_sum += votes * INCOME_BY_PARTY[party_key]
        income_index = weighted_sum / total_valid
        rows_out.append((precinct_nr, gmina, total_valid, round(income_index, 1)))

print(f"precincts processed: {len(rows_out)}")

with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["nr_obwodu", "gmina", "valid_votes", "income_index_pln"])
    w.writerows(rows_out)
print(f"wrote {OUT_CSV}")
