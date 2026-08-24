"""Turn lodz_universities.csv (long, one row per OSM building) into the wide
destinations table r5r::accessibility() wants: id, lon, lat, plus one 0/1
opportunity column per university (+ a "total" column).

Usage: py prepare_uni_destinations.py <in_csv=lodz_universities.csv> <out_csv>
"""
import csv
import sys

IN_CSV = sys.argv[1]
OUT_CSV = sys.argv[2]
UNIVERSITIES = ["Politechnika Łódzka", "Uniwersytet Łódzki", "Uniwersytet Medyczny"]
COL_NAMES = {"Politechnika Łódzka": "politechnika", "Uniwersytet Łódzki": "uniwersytet",
             "Uniwersytet Medyczny": "medyczny"}

with open(IN_CSV, encoding="utf-8", newline="") as f:
    rows = list(csv.DictReader(f))

with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["id", "lon", "lat", "politechnika", "uniwersytet", "medyczny", "total"])
    for i, r in enumerate(rows):
        flags = [1 if r["university"] == u else 0 for u in UNIVERSITIES]
        w.writerow([f"uni_{i}", r["lon"], r["lat"]] + flags + [1])

print(f"wrote {len(rows)} university destination points to {OUT_CSV}")
