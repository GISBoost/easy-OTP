"""Fetch academic buildings for a city's configured universities (cities_config.py)
from OSM via Overpass -- generalized version of accessibility_lodz's script.

Usage: py fetch_universities.py <city>
Output: <city>/<city>_universities.csv
"""
import csv
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from cities_config import CITIES

CITY = sys.argv[1]
CFG = CITIES[CITY]
BASE = Path(__file__).parent
OUT_CSV = BASE / CITY / f"{CITY}_universities.csv"


def query_for(admin_level):
    return f"""
[out:json][timeout:180];
area["name"="{CFG['display_name']}"]["admin_level"="{admin_level}"]["boundary"="administrative"]->.a;
(
  node["amenity"~"^(university|college)$"](area.a);
  way["amenity"~"^(university|college)$"](area.a);
  node["building"="university"](area.a);
  way["building"="university"](area.a);
);
out center tags;
"""


def run_query(query):
    body = urllib.parse.urlencode({"data": query}).encode()
    req = urllib.request.Request("https://overpass-api.de/api/interpreter", data=body,
                                  headers={"User-Agent": "easy-OTP-accessibility-research/1.0"})
    with urllib.request.urlopen(req, timeout=200) as resp:
        return json.load(resp)


patterns = [(name, re.compile(pat, re.I)) for name, pat in CFG["universities"].items()]


def classify(tags):
    text = " ".join(str(tags.get(k, "")) for k in ("name", "operator", "name:pl"))
    for uni_name, pattern in patterns:
        if pattern.search(text):
            return uni_name
    return None


result = run_query(query_for(CFG["osm_admin_level"]))
if not result["elements"]:
    print(f"admin_level={CFG['osm_admin_level']} returned 0 elements, trying 8")
    result = run_query(query_for("8"))

rows = []
skipped = 0
for el in result["elements"]:
    tags = el.get("tags", {})
    uni = classify(tags)
    if uni is None:
        skipped += 1
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
print(f"{CITY}: wrote {len(rows)} buildings to {OUT_CSV}: {by_uni} (skipped {skipped})")
