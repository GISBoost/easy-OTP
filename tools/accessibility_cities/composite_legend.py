"""Paste a city's bivariate legend PNG into the bottom-left corner of its map
render, with a semi-opaque white backing so it stays legible over the basemap.

Usage: py composite_legend.py <city> <map_png> <legend_png> <out_png>
"""
import sys

from PIL import Image

CITY = sys.argv[1]
map_path, legend_path, out_path = sys.argv[2], sys.argv[3], sys.argv[4]

base = Image.open(map_path).convert("RGBA")
legend = Image.open(legend_path).convert("RGBA")

# scale legend to ~32% of map width
target_w = int(base.width * 0.32)
scale = target_w / legend.width
legend = legend.resize((target_w, int(legend.height * scale)), Image.LANCZOS)

pad = 14
x = pad
y = base.height - legend.height - pad

backing = Image.new("RGBA", (legend.width + 2 * pad, legend.height + 2 * pad), (255, 255, 255, 235))
base.alpha_composite(backing, (x - pad, y - pad))
base.alpha_composite(legend, (x, y))
base.convert("RGB").save(out_path)
print(f"wrote {out_path}")
