"""Fetch public-service POIs for Lodz from OSM via Overpass API, as accessibility
destinations (stand-in for "jobs", since bulk REGON employment data isn't readily
downloadable with addresses -- see HANDOFF.md). Categories chosen to match the
"15-minute city" / public-service accessibility literature: education, health,
culture, groceries.

Usage: py fetch_osm_services.py <out_csv>
Output columns: category, osm_type, osm_id, name, lon, lat
"""
import csv
import json
import sys
import urllib.request

OUT_CSV = sys.argv[1]

# area(3600...) = OSM relation id for Lodz city boundary, resolved via Overpass area
# search by name+admin_level to avoid guessing the relation id.
QUERY = """
[out:json][timeout:120];
area["name"="Łódź"]["admin_level"="6"]["boundary"="administrative"]->.a;
(
  node["amenity"~"^(school|kindergarten|hospital|clinic|doctors|pharmacy|library|community_centre)$"](area.a);
  way["amenity"~"^(school|kindergarten|hospital|clinic|doctors|pharmacy|library|community_centre)$"](area.a);
  node["shop"="supermarket"](area.a);
  way["shop"="supermarket"](area.a);
);
out center tags;
"""

CATEGORY_MAP = {
    "school": "education", "kindergarten": "education",
    "hospital": "health", "clinic": "health", "doctors": "health", "pharmacy": "health",
    "library": "culture", "community_centre": "culture",
    "supermarket": "groceries",
}

import urllib.parse
body = urllib.parse.urlencode({"data": QUERY}).encode()
req = urllib.request.Request("https://overpass-api.de/api/interpreter", data=body,
                              headers={"User-Agent": "easy-OTP-accessibility-research/1.0"})
with urllib.request.urlopen(req, timeout=180) as resp:
    result = json.load(resp)

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

with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["category", "osm_type", "osm_id", "name", "lon", "lat"])
    w.writerows(rows)

by_cat = {}
for r in rows:
    by_cat[r[0]] = by_cat.get(r[0], 0) + 1
print(f"wrote {len(rows)} POIs to {OUT_CSV}: {by_cat}")
