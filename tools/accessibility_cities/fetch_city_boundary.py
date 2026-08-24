"""Fetch a city's administrative boundary polygon (Nominatim, not Overpass --
much simpler than reassembling a relation's geometry by hand) so maps can draw
a real city outline instead of leaving the reader to mistake the hex grid's
own red borders for the city edge.

Usage: py fetch_city_boundary.py <city>
Output: <city>/<city>_boundary.geojson
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from cities_config import CITIES

CITY = sys.argv[1]
display_name = "Łódź" if CITY == "lodz" else CITIES[CITY]["display_name"]
BASE = Path(__file__).parent
CITY_DIR = (BASE.parent / "accessibility_lodz") if CITY == "lodz" else (BASE / CITY)
OUT = CITY_DIR / f"{CITY}_boundary.geojson"

params = {
    "q": f"{display_name}, Poland",
    "format": "jsonv2",
    "polygon_geojson": "1",
    "limit": "5",
    "addressdetails": "1",
}
url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(params)
req = urllib.request.Request(url, headers={"User-Agent": "easy-OTP-accessibility-research/1.0"})
with urllib.request.urlopen(req, timeout=60) as resp:
    results = json.load(resp)

# prefer the city-level administrative boundary (addresstype "city") over the
# surrounding powiat/county relation that Nominatim also returns for match
candidates = [r for r in results if r.get("osm_type") == "relation" and r.get("category") == "boundary"]
best = next((r for r in candidates if r.get("addresstype") == "city"), candidates[0] if candidates else results[0])

feature = {
    "type": "Feature",
    "properties": {"name": display_name, "osm_id": best["osm_id"]},
    "geometry": best["geojson"],
}
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(feature, f)
print(f"{CITY}: wrote boundary ({best['geojson']['type']}) from osm_type={best['osm_type']}/addresstype={best.get('addresstype')} to {OUT}")
time.sleep(1.1)  # Nominatim usage policy: max 1 req/s
