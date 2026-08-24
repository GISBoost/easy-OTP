"""Fetch academic buildings for Lodz's 3 largest universities from OSM (Overpass),
as accessibility destinations for a student-accessibility analysis. Multiple
buildings per university (campuses spread across the city), not one point per
institution -- classified by regex match on name/operator tags.

Usage: py fetch_universities.py <out_csv>
Output columns: university, osm_type, osm_id, name, lon, lat
"""
import csv
import re
import sys
import urllib.parse
import urllib.request

OUT_CSV = sys.argv[1]

QUERY = """
[out:json][timeout:120];
area["name"="Łódź"]["admin_level"="6"]["boundary"="administrative"]->.a;
(
  node["amenity"~"^(university|college)$"](area.a);
  way["amenity"~"^(university|college)$"](area.a);
  node["building"="university"](area.a);
  way["building"="university"](area.a);
);
out center tags;
"""

UNIVERSITY_PATTERNS = [
    ("Politechnika Łódzka", re.compile(r"politechnik[ai]\s*ł[oó]dzk", re.I)),
    ("Uniwersytet Medyczny", re.compile(r"uniwersytet\w*\s*medyczn", re.I)),
    ("Uniwersytet Łódzki", re.compile(r"uniwersytet\w*\s*ł[oó]dzk", re.I)),
]

body = urllib.parse.urlencode({"data": QUERY}).encode()
req = urllib.request.Request("https://overpass-api.de/api/interpreter", data=body,
                              headers={"User-Agent": "easy-OTP-accessibility-research/1.0"})
import json
with urllib.request.urlopen(req, timeout=180) as resp:
    result = json.load(resp)


def classify(tags):
    text = " ".join(str(tags.get(k, "")) for k in ("name", "operator", "name:pl"))
    for uni_name, pattern in UNIVERSITY_PATTERNS:
        if pattern.search(text):
            return uni_name
    return None


rows = []
skipped_unmatched = 0
for el in result["elements"]:
    tags = el.get("tags", {})
    uni = classify(tags)
    if uni is None:
        skipped_unmatched += 1
        continue
    if el["type"] == "node":
        lon, lat = el["lon"], el["lat"]
    else:
        center = el.get("center")
        if not center:
            continue
        lon, lat = center["lon"], center["lat"]
    rows.append((uni, el["type"], el["id"], tags.get("name", ""), lon, lat))

with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["university", "osm_type", "osm_id", "name", "lon", "lat"])
    w.writerows(rows)

by_uni = {}
for r in rows:
    by_uni[r[0]] = by_uni.get(r[0], 0) + 1
print(f"wrote {len(rows)} buildings to {OUT_CSV}: {by_uni} (skipped {skipped_unmatched} unmatched university/college nodes)")
