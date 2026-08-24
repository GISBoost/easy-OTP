"""Estimate a per-capita income index for each Lodz voting precinct (obwod glosowania).

Method: area-level MRP-lite. Party vote shares from Sejm 2023 results (PKW/KBW,
precinct level) are used as area-level weights over a party -> income-bracket
profile taken from CBOS "Kim sa wyborcy partii politycznych w Polsce?"
(Komunikat z badan nr 98/2023, Tabela 1, "Dochody na jedna osobe").

This is NOT a survey of actual precinct residents' income. It is an ecological/
area-level estimate: income_index(precinct) = sum_party( vote_share_party *
mean_income_party ), where mean_income_party comes from a national CBOS survey,
not from Lodz specifically. See caveats in the accompanying report.
"""
import csv
from pathlib import Path

BASE = Path(r"C:\Users\Michal\Desktop\easy\easy-OTP\tools\ses_income_lodz")
RESULTS_CSV = BASE / "lodz_wyniki_listy_with_header.csv"
OUT_CSV = BASE / "lodz_precinct_income.csv"

# CBOS K_098_23, Tabela 1 "Dochody na jedna osobe" (% of party electorate),
# columns: <1499 | 1500-1999 | 2000-2999 | 3000-3999 | 4000+  (trudno powiedziec /
# odmowa odpowiedzi excluded, distribution renormalized over the valid share).
# Bracket midpoints (zl/month per capita); top bracket "4000 zl i wiecej" is
# open-ended -- treated as 4500 zl, a judgment call, not a CBOS-reported value.
MIDPOINTS = [1250, 1750, 2500, 3500, 4500]

CBOS_INCOME_PCT = {
    "PiS": [12, 18, 26, 12, 12],
    "KO": [4, 9, 26, 16, 25],
    "Lewica": [3, 5, 27, 15, 23],
    "Konfederacja": [4, 5, 18, 17, 25],
    "TrzeciaDroga": [6, 5, 20, 7, 25],
    "Ogolem": [7, 13, 23, 14, 17],  # "ogol zdeklarowanych wyborcow" -- fallback for minor committees
}


def mean_income(pcts: list[float]) -> float:
    valid = sum(pcts)
    return sum(p * m for p, m in zip(pcts, MIDPOINTS)) / valid


INCOME_BY_PARTY = {k: mean_income(v) for k, v in CBOS_INCOME_PCT.items()}
FALLBACK_INCOME = INCOME_BY_PARTY["Ogolem"]

# Map PKW committee column header (exact CSV header text) -> CBOS party key.
# Anything not listed here (minor/fringe committees) falls back to FALLBACK_INCOME.
COMMITTEE_TO_PARTY = {
    "KOALICYJNY KOMITET WYBORCZY TRZECIA DROGA POLSKA 2050 SZYMONA HOŁOWNI - POLSKIE STRONNICTWO LUDOWE": "TrzeciaDroga",
    "KOMITET WYBORCZY NOWA LEWICA": "Lewica",
    "KOMITET WYBORCZY PRAWO I SPRAWIEDLIWOŚĆ": "PiS",
    "KOMITET WYBORCZY KONFEDERACJA WOLNOŚĆ I NIEPODLEGŁOŚĆ": "Konfederacja",
    "KOALICYJNY KOMITET WYBORCZY KOALICJA OBYWATELSKA PO .N IPL ZIELONI": "KO",
}

print("Estimated mean income per capita by party electorate (PLN/month):")
for k, v in INCOME_BY_PARTY.items():
    print(f"  {k:14s} {v:8.1f}")

with open(RESULTS_CSV, encoding="utf-8-sig", newline="") as f:
    reader = csv.reader(f, delimiter=";")
    header = next(reader)
    committee_cols = [i for i, h in enumerate(header) if h.strip('"').isupper() and "KOMITET" in h]
    idx_precinct = 0  # "Nr komisji"
    idx_valid_votes = 31  # "Liczba glosow waznych oddanych lacznie na wszystkie listy kandydatow" (0-based)
    idx_gmina = 3
    idx_teryt = 2

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
            committee_name = header[ci].strip('"')
            party_key = COMMITTEE_TO_PARTY.get(committee_name, "Ogolem")
            weighted_sum += votes * INCOME_BY_PARTY[party_key]
        income_index = weighted_sum / total_valid
        rows_out.append((precinct_nr, gmina, total_valid, round(income_index, 1)))

print(f"\nprecincts processed: {len(rows_out)}")

with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["nr_obwodu", "gmina", "valid_votes", "income_index_pln"])
    w.writerows(rows_out)
print(f"wrote {OUT_CSV}")
