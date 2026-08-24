"""Per-city bivariate legend key (dominant university x population tercile),
generalized from accessibility_lodz's plot_bivariate_legend.py. Reads the
palette straight out of the city's hex500.gpkg (dominant_university/pop_tercile/
biv_color columns) so labels always match what's actually in the map.

Usage: py plot_bivariate_legend.py <city>
"""
import sys
import textwrap
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt

CITY = sys.argv[1]
BASE = Path(__file__).parent
CITY_DIR = BASE.parent / "accessibility_lodz" if CITY == "lodz" else BASE / CITY
OUT = CITY_DIR / "out"
OUT.mkdir(exist_ok=True)

gpkg = CITY_DIR / f"{CITY}_hex500.gpkg" if CITY != "lodz" else CITY_DIR / "lodz_hex500.gpkg"
g = gpd.read_file(gpkg, layer="hex500")
g = g.dropna(subset=["dominant_university", "biv_color"])

unis = sorted(u for u in g["dominant_university"].unique() if u and u != "none")
TIERS = ["niska", "średnia", "wysoka"]

none_col = len(unis)
n_cols = none_col + 1
# width scales with column count so wrapped labels get real horizontal room --
# a fixed 5.2" figure was fine for Lodz's 3 short one-word labels but collapsed
# into overlapping text once composited at ~32% width for cities with long
# multi-word university names (e.g. "Uniwersytet Medyczny im. K. Marcinkowskiego")
fig, ax = plt.subplots(figsize=(2.3 * n_cols + 0.9, 3.4))
for col, uni in enumerate(unis):
    for tier in (0, 1, 2):
        rows = g[(g["dominant_university"] == uni) & (g["pop_tercile"] == tier)]
        color = rows.iloc[0]["biv_color"] if len(rows) else "#ffffff"
        ax.add_patch(plt.Rectangle((col, tier), 1, 1, facecolor=color, edgecolor="white", linewidth=0.5))

for tier in (0, 1, 2):
    ax.add_patch(plt.Rectangle((none_col, tier), 1, 1, facecolor="none", edgecolor="#c80000",
                                hatch="xxx", linewidth=1.2))

ax.set_xlim(0, none_col + 1)
ax.set_ylim(0, 3)
ax.set_xticks([c + 0.5 for c in range(none_col + 1)])
# wrap on word boundaries (not just the first space) so a 4-word name breaks
# into several short lines instead of one long line that bleeds into the
# neighboring column once the legend is scaled down for map compositing
labels = ["\n".join(textwrap.wrap(u, width=13, break_long_words=False, break_on_hyphens=False))
          for u in unis] + ["brak\ndostępu"]
ax.set_xticklabels(labels, fontsize=8.5)
ax.set_yticks([0.5, 1.5, 2.5])
ax.set_yticklabels(TIERS, fontsize=8.5)
ax.set_ylabel("populacja 20-29", fontsize=8.5)
ax.set_title("dominująca uczelnia (≤30 min) x populacja studencka", fontsize=8.5)
ax.set_aspect("equal")
for spine in ax.spines.values():
    spine.set_visible(False)
fig.patch.set_alpha(0)
fig.tight_layout(pad=0.1)
out_path = OUT / f"{CITY}_bivariate_legend.png"
fig.savefig(out_path, dpi=350, transparent=True, bbox_inches="tight", pad_inches=0.03)
print(f"wrote {out_path}")
