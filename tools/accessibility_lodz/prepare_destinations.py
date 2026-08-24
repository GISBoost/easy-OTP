"""Turn lodz_services.csv (long, one row per OSM POI) into the wide destinations
table r5r::accessibility() wants: id, lon, lat, plus one 0/1 opportunity column
per category (+ a "total" column summing all categories).

Usage: py prepare_destinations.py <in_csv=lodz_services.csv> <out_csv>
"""
import csv
import sys

IN_CSV = sys.argv[1]
OUT_CSV = sys.argv[2]
CATEGORIES = ["education", "health", "culture", "groceries"]

with open(IN_CSV, encoding="utf-8", newline="") as f:
    rows = list(csv.DictReader(f))

with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["id", "lon", "lat"] + CATEGORIES + ["total"])
    for i, r in enumerate(rows):
        flags = [1 if r["category"] == c else 0 for c in CATEGORIES]
        w.writerow([f"poi_{i}", r["lon"], r["lat"]] + flags + [1])

print(f"wrote {len(rows)} destination points to {OUT_CSV}")
