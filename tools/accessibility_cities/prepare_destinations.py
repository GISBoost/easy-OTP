"""Turn a city's services/universities long-format CSV into the wide destinations
table r5r::accessibility() wants (id, lon, lat, one 0/1 column per category/uni,
total). Generalized version of accessibility_lodz's prepare_destinations.py /
prepare_uni_destinations.py.

Usage: py prepare_destinations.py <city> services
       py prepare_destinations.py <city> universities
"""
import csv
import sys
from pathlib import Path

from cities_config import CITIES

CITY = sys.argv[1]
KIND = sys.argv[2]  # "services" or "universities"
BASE = Path(__file__).parent
CITY_DIR = BASE / CITY

if KIND == "services":
    IN_CSV = CITY_DIR / f"{CITY}_services.csv"
    OUT_CSV = CITY_DIR / f"{CITY}_service_destinations.csv"
    CATEGORIES = ["education", "health", "culture", "groceries"]
    key_col = "category"
elif KIND == "universities":
    IN_CSV = CITY_DIR / f"{CITY}_universities.csv"
    OUT_CSV = CITY_DIR / f"{CITY}_uni_destinations.csv"
    CATEGORIES = list(CITIES[CITY]["universities"].keys())
    key_col = "university"
else:
    raise SystemExit(f"unknown kind {KIND!r}, expected services|universities")

with open(IN_CSV, encoding="utf-8", newline="") as f:
    rows = list(csv.DictReader(f))

# short, ascii-safe column names for r5r (Polish diacritics in column names
# have caused no observed issue so far, but keep it simple/consistent -- slug
# by position instead of by name to avoid any encoding surprises in R)
slug_by_cat = {cat: f"opp{i}" for i, cat in enumerate(CATEGORIES)}

with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["id", "lon", "lat"] + [slug_by_cat[c] for c in CATEGORIES] + ["total"])
    for i, r in enumerate(rows):
        flags = [1 if r[key_col] == c else 0 for c in CATEGORIES]
        w.writerow([f"{KIND[:3]}_{i}", r["lon"], r["lat"]] + flags + [1])

print(f"{CITY}/{KIND}: wrote {len(rows)} destination points to {OUT_CSV}")
print(f"  category slug map: {slug_by_cat}")

import json
slugmap_path = OUT_CSV.with_name(OUT_CSV.stem.replace("_destinations", "") + "_destinations_slugmap.json")
with open(slugmap_path, "w", encoding="utf-8") as f:
    json.dump(slug_by_cat, f, ensure_ascii=False, indent=2)
