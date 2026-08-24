"""Compute the income index directly from wybory.it's parl_2023 tileset attributes.

The tileset (https://wybory.it, source: michalpazur/obwody-wyborcze on GitHub,
built from PKW's official boundary descriptions + GUS statistical-district shapes
via address tokenization + Voronoi tessellation) already carries per-precinct vote
counts for every committee as GeoJSON properties, so no separate PKW CSV parsing
is needed here -- just apply the same CBOS income weights used for Lodz/Krakow.

Usage: python compute_income_from_tileset.py <precincts_geojson> <out_csv>
Reads GeoJSON features with properties matching the parl_2023 tilejson schema
(pis, ko, p2050_psl, nl, konfederacja, bs, pjj, mn, rdip, nk, ap, rnp, all_votes,
number, teryt, gmina, powiat) and writes nr_obwodu,gmina,valid_votes,income_index_pln.
"""
import csv
import json
import sys
from pathlib import Path

IN_GEOJSON = Path(sys.argv[1])
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


def mean_income(pcts):
    valid = sum(pcts)
    return sum(p * m for p, m in zip(pcts, MIDPOINTS)) / valid


INCOME_BY_PARTY = {k: mean_income(v) for k, v in CBOS_INCOME_PCT.items()}

# tileset field name -> CBOS party key; anything else (bs, pjj, mn, rdip, nk, ap, rnp) -> fallback
FIELD_TO_PARTY = {
    "pis": "PiS",
    "ko": "KO",
    "p2050_psl": "TrzeciaDroga",
    "nl": "Lewica",
    "konfederacja": "Konfederacja",
}
MINOR_FIELDS = ["bs", "pjj", "mn", "rdip", "nk", "ap", "rnp"]

data = json.loads(IN_GEOJSON.read_text(encoding="utf-8"))
features = data["features"]
print(f"features: {len(features)}")

rows_out = []
for feat in features:
    p = feat["properties"]
    # "total" = sum of party-list vote fields (verified against official PKW data);
    # "all_votes" is a different, slightly larger PKW field (likely "valid ballot
    # cards") and must NOT be used as the vote-share denominator.
    total_valid = p.get("total")
    if not total_valid:
        continue
    weighted_sum = 0.0
    for field, party in FIELD_TO_PARTY.items():
        votes = p.get(field) or 0
        weighted_sum += votes * INCOME_BY_PARTY[party]
    for field in MINOR_FIELDS:
        votes = p.get(field) or 0
        weighted_sum += votes * INCOME_BY_PARTY["Ogolem"]
    income_index = weighted_sum / total_valid
    district_key = f"{p.get('teryt')}_{int(p.get('number'))}"
    rows_out.append((p.get("number"), p.get("gmina"), total_valid, round(income_index, 1), p.get("teryt"), district_key))

print(f"precincts processed: {len(rows_out)}")

with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["nr_obwodu", "gmina", "valid_votes", "income_index_pln", "teryt", "district"])
    w.writerows(rows_out)
print(f"wrote {OUT_CSV}")
