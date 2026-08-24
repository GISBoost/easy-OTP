"""Fetch MVT vector tiles from the wybory.it Martin server directly over HTTP and
write a proper MBTiles (SQLite, TMS y-flip, with vector_layers schema metadata)
-- avoids relying on QGIS's own (crash-prone, observed twice) vector-tile downloader.

Usage: python fetch_tiles_mbtiles.py <xmin> <ymin> <xmax> <ymax> <zoom> <out_mbtiles>
Bbox in EPSG:4326 (lon/lat), zoom is a single integer (e.g. 14).
"""
import math
import sqlite3
import sys
import time
import urllib.request

TILE_URL = "https://wybory.it/api/martin/parl_2023/{z}/{x}/{y}"

VECTOR_LAYERS_JSON = """{"vector_layers": [{"id": "parl_2023", "fields": {
"all_votes": "Number","ap": "Number","ap_proc": "Number","bs": "Number","bs_proc": "Number",
"constituency": "Number","district": "String","gmina": "String","ko": "Number","ko_proc": "Number",
"konfederacja": "Number","konfederacja_proc": "Number","mn": "Number","mn_proc": "Number",
"nk": "Number","nk_proc": "Number","nl": "Number","nl_proc": "Number","number": "Number",
"p2050_psl": "Number","p2050_psl_proc": "Number","pis": "Number","pis_proc": "Number",
"pjj": "Number","pjj_proc": "Number","powiat": "String","rdip": "Number","rdip_proc": "Number",
"rnp": "Number","rnp_proc": "Number","teryt": "String","total": "Number","turnout": "Number",
"voters": "Number","winner": "String","winner_proc": "Number"}, "minzoom": 0, "maxzoom": 14}]}"""


def deg2tile(lon, lat, zoom):
    lat_rad = math.radians(lat)
    n = 2.0 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def main():
    xmin, ymin, xmax, ymax, zoom, out_path = sys.argv[1:7]
    xmin, ymin, xmax, ymax = float(xmin), float(ymin), float(xmax), float(ymax)
    zoom = int(zoom)

    tx0, ty1 = deg2tile(xmin, ymin, zoom)  # bottom-left -> larger y
    tx1, ty0 = deg2tile(xmax, ymax, zoom)  # top-right -> smaller y
    tx_min, tx_max = min(tx0, tx1), max(tx0, tx1)
    ty_min, ty_max = min(ty0, ty1), max(ty0, ty1)
    n_tiles = (tx_max - tx_min + 1) * (ty_max - ty_min + 1)
    print(f"zoom={zoom} tiles x=[{tx_min},{tx_max}] y=[{ty_min},{ty_max}] count={n_tiles}")

    conn = sqlite3.connect(out_path)
    cur = conn.cursor()
    cur.execute("CREATE TABLE metadata (name TEXT, value TEXT)")
    cur.execute("CREATE TABLE tiles (zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER, tile_data BLOB)")
    cur.execute("CREATE UNIQUE INDEX tile_index ON tiles (zoom_level, tile_column, tile_row)")
    for k, v in [
        ("format", "pbf"), ("name", "parl_2023"), ("minzoom", str(zoom)), ("maxzoom", str(zoom)),
        ("bounds", f"{xmin},{ymin},{xmax},{ymax}"), ("json", VECTOR_LAYERS_JSON),
    ]:
        cur.execute("INSERT INTO metadata (name, value) VALUES (?, ?)", (k, v))
    conn.commit()

    fetched = empty = failed = 0
    n_rows = ty_max - ty_min + 1
    for x in range(tx_min, tx_max + 1):
        for y in range(ty_min, ty_max + 1):
            url = TILE_URL.format(z=zoom, x=x, y=y)
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept": "*/*",
            })
            for attempt in range(3):
                try:
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        data = resp.read()
                    break
                except Exception as e:
                    if attempt == 2:
                        print(f"  FAILED {x},{y}: {e}")
                        data = None
                        failed += 1
                    time.sleep(0.5)
            if data is None:
                continue
            if len(data) == 0:
                empty += 1
                continue
            tms_row = (2 ** zoom - 1) - y  # MBTiles spec uses TMS (y flipped)
            cur.execute(
                "INSERT OR REPLACE INTO tiles (zoom_level, tile_column, tile_row, tile_data) VALUES (?,?,?,?)",
                (zoom, x, tms_row, sqlite3.Binary(data)),
            )
            fetched += 1
    conn.commit()
    conn.close()
    print(f"fetched={fetched} empty={empty} failed={failed} -> {out_path}")


if __name__ == "__main__":
    main()
