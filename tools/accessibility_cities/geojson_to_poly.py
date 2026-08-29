"""Convert a single-polygon GeoJSON boundary (WGS84) to Osmosis .poly format,
for osmosis --bounding-polygon (irregular-shape OSM clip, vs. the simpler
--bounding-box path prepare_osm_pbf.py uses for compact single cities).

Osmosis .poly format: https://wiki.openstreetmap.org/wiki/Osmosis/Polygon_Filter_File_Format
No holes supported here (fine -- GZM's boundary relation has 0 inner ways).

Usage: py geojson_to_poly.py <in.geojson> <out.poly> <name>
"""
import json
import sys

in_path, out_path, name = sys.argv[1], sys.argv[2], sys.argv[3]

with open(in_path, encoding="utf-8") as f:
    data = json.load(f)

geom = data["geometry"] if data.get("type") == "Feature" else data
if geom["type"] == "Polygon":
    rings = [geom["coordinates"][0]]  # outer ring only
elif geom["type"] == "MultiPolygon":
    rings = [poly[0] for poly in geom["coordinates"]]
else:
    raise ValueError(f"unsupported geometry type: {geom['type']}")

with open(out_path, "w", encoding="utf-8") as f:
    f.write(f"{name}\n")
    for i, ring in enumerate(rings, start=1):
        f.write(f"{i}\n")
        for lon, lat in ring:
            f.write(f"  {lon:.7f}  {lat:.7f}\n")
        f.write("END\n")
    f.write("END\n")

print(f"wrote {out_path} ({len(rings)} ring(s), {sum(len(r) for r in rings)} points)")
