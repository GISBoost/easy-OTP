"""Fetch public-service POIs (education/health/culture/groceries) for a city
from OSM via Overpass -- generalized version of accessibility_lodz's script,
parameterized by city display_name and admin_level (cities_config.py).

Usage: py fetch_osm_services.py <city>
Output: <city>/<city>_services.csv
"""
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from cities_config import CITIES

CITY = sys.argv[1]
CFG = CITIES[CITY]
BASE = Path(__file__).parent
OUT_CSV = BASE / CITY / f"{CITY}_services.csv"

CATEGORY_MAP = {
    "school": "education", "kindergarten": "education",
    "hospital": "health", "clinic": "health", "doctors": "health", "pharmacy": "health",
    "library": "culture", "community_centre": "culture",
    "supermarket": "groceries",
}


def query_for(admin_level):
    return f"""
[out:json][timeout:180];
area["name"="{CFG['display_name']}"]["admin_level"="{admin_level}"]["boundary"="administrative"]->.a;
(
  node["amenity"~"^(school|kindergarten|hospital|clinic|doctors|pharmacy|library|community_centre)$"](area.a);
  way["amenity"~"^(school|kindergarten|hospital|clinic|doctors|pharmacy|library|community_centre)$"](area.a);
  node["shop"="supermarket"](area.a);
  way["shop"="supermarket"](area.a);
);
out center tags;
"""


def run_query(query):
    body = urllib.parse.urlencode({"data": query}).encode()
    req = urllib.request.Request("https://overpass-api.de/api/interpreter", data=body,
                                  headers={"User-Agent": "easy-OTP-accessibility-research/1.0"})
    with urllib.request.urlopen(req, timeout=200) as resp:
        return json.load(resp)


result = run_query(query_for(CFG["osm_admin_level"]))
if not result["elements"]:
    print(f"admin_level={CFG['osm_admin_level']} returned 0 elements, trying 8")
    result = run_query(query_for("8"))

rows = []
for el in result["elements"]:
    tags = el.get("tags", {})
    key = tags.get("amenity") or tags.get("shop")
    category = CATEGORY_MAP.get(key)
    if category is None:
        continue
    if el["type"] == "node":
        lon, lat = el["lon"], el["lat"]
    else:
        center = el.get("center")
        if not center:
            continue
        lon, lat = center["lon"], center["lat"]
    rows.append((category, el["type"], el["id"], tags.get("name", ""), lon, lat))

import csv
with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["category", "osm_type", "osm_id", "name", "lon", "lat"])
    w.writerows(rows)

by_cat = {}
for r in rows:
    by_cat[r[0]] = by_cat.get(r[0], 0) + 1
print(f"{CITY}: wrote {len(rows)} POIs to {OUT_CSV}: {by_cat}")
