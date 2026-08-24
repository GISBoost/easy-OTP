"""Cross-city comparison charts (5 cities + Lodz reused from accessibility_lodz's
own results) -- answers the two headline questions: (1) is income a predictor of
service accessibility deprivation, city by city; (2) which university is most
advantageous to live near, per city, and what fraction of students have no
30-min access to any of the configured universities.

Reads each city's out/{city}_income_decile_accessibility.csv and
out/{city}_uni_summary.csv (+ Lodz's equivalent files from accessibility_lodz,
reused not recomputed). Read-only.

Usage: py analyze_cross_city.py
Output: out/cross_city_income_correlation.png, out/cross_city_uni_no_access.png
"""
import csv
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

BASE = Path(__file__).parent
LODZ_DIR = BASE.parent / "accessibility_lodz"
OUT = BASE / "out"
OUT.mkdir(exist_ok=True)

CITIES = ["lodz", "warszawa", "krakow", "gdansk", "poznan", "szczecin"]
DISPLAY = {"lodz": "Łódź", "warszawa": "Warszawa", "krakow": "Kraków",
           "gdansk": "Gdańsk", "poznan": "Poznań", "szczecin": "Szczecin"}
# fixed categorical color per city, used consistently across every comparison
# chart in this analysis (dataviz-skill principle: assign hue by identity,
# never re-cycle when a chart's series subset changes)
# muted (desaturated) so the chart isn't garish -- redundant with marker+line
CITY_COLOR = {"lodz": "#5B7DB1", "warszawa": "#C98A5E", "krakow": "#6FA287",
              "gdansk": "#B4696D", "poznan": "#8D7AA6", "szczecin": "#8C7B6B"}
# kept simple on purpose: only 3 marker shapes x 2 line styles (6 combos,
# exactly enough for 6 cities) so the chart stays legible in grayscale/B&W
# print without needing a marker/dash per city that nobody can memorize
CITY_MARKER = {"lodz": "o", "warszawa": "s", "krakow": "^",
               "gdansk": "o", "poznan": "s", "szczecin": "^"}
CITY_LINESTYLE = {"lodz": "-", "warszawa": "-", "krakow": "-",
                   "gdansk": "--", "poznan": "--", "szczecin": "--"}

plt.rcParams.update({
    "figure.facecolor": "white", "axes.facecolor": "white",
    "axes.spines.top": False, "axes.spines.right": False, "font.size": 11,
})


def city_dir(city):
    return LODZ_DIR if city == "lodz" else BASE / city


def city_out(city):
    return city_dir(city) / "out"


# ---------------------------------------------------------------------------
# Chart 1: income decile -> mean accessibility, one line per city (normalized
# to city's own D1 so cities with very different POI densities are comparable)
# ---------------------------------------------------------------------------
fig, ax = plt.subplots(figsize=(9, 6.8))
corr_by_city = {}
for city in CITIES:
    f = city_out(city) / f"{city}_income_decile_accessibility.csv"
    if not f.exists():
        print(f"skip {city}: {f} not found")
        continue
    deciles, means = [], []
    with open(f, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            deciles.append(int(row["decile"]))
            means.append(float(row["mean"]))
    if not means or means[0] == 0:
        continue
    normalized = [m / means[0] for m in means]
    ax.plot(deciles, normalized, marker=CITY_MARKER[city], linestyle=CITY_LINESTYLE[city],
            color=CITY_COLOR[city], label=DISPLAY[city], linewidth=1.8, markersize=7,
            markeredgecolor="black", markeredgewidth=0.5)

ax.grid(True, alpha=0.25, linewidth=0.6)  # transit_charts GRID_KW convention
ax.set_axisbelow(True)
# closed-box frame (all 4 spines), E20-style -- this chart's global rcParams
# drops top/right spines by default, override just here
for spine in ax.spines.values():
    spine.set_visible(True)
    spine.set_color("black")
    spine.set_linewidth(0.8)
ax.axhline(1.0, color="black", linewidth=0.9, alpha=0.6)  # E20-style zero/reference line
ax.set_xlabel("decyl dochodu (D1 = najniższy dochód, D10 = najwyższy dochód)")
ax.set_ylabel("dostępność usług względem D1 (D1 = 1,0)")
ax.set_title("Dochód a dostępność usług publicznych, 6 miast\n"
             "próg 30 min, znormalizowane do D1 każdego miasta", fontsize=12)
ax.legend(loc="upper left", fontsize=9, ncol=2)
# caption lives below the axes (not overlaid on the data) so it never covers a
# line/marker regardless of which city's curve happens to pass through a given
# corner -- an in-plot text box was tried first and clipped several points
fig.text(0.5, 0.01,
         "Jak czytać: każda linia to jedno miasto, D1=1,0 zawsze (punkt odniesienia). Linia powyżej 1,0 w Dx = ten decyl ma więcej usług w zasięgu\n"
         "niż decyl o najniższym dochodzie TEGO SAMEGO miasta -- linie nie są porównywalne między miastami co do poziomu (różna gęstość usług), tylko co do\n"
         "KSZTAŁTU/trendu. Płaska linia = dochód nie różnicuje dostępności. Rosnąca = różnicuje.",
         ha="center", va="bottom", fontsize=8, color="#444444")
fig.tight_layout(rect=[0, 0.085, 1, 1])
fig.savefig(OUT / "cross_city_income_correlation.png", dpi=150)
plt.close(fig)
print(f"wrote {OUT / 'cross_city_income_correlation.png'}")

# ---------------------------------------------------------------------------
# Chart 2: % of student-population hexes with no university access in 30 min
# ---------------------------------------------------------------------------
pct_no_access = {}
pct_pop_no_access = {}
for city in CITIES:
    f = city_out(city) / f"{city}_uni_summary.csv"
    if not f.exists():
        continue
    with open(f, encoding="utf-8", newline="") as fh:
        for row in csv.reader(fh):
            if row[0] == "PCT_no_access_30min":
                pct_no_access[city] = float(row[1])
            elif row[0] == "PCT_POP_no_access_30min":
                pct_pop_no_access[city] = float(row[1])

# grouped bars: hex-count metric (unweighted, can be noise-dominated by
# near-empty edge hexes) vs population-weighted metric (Michal's instruction --
# a hex with 1 student and a hex with 500 students shouldn't count equally)
cities_present = [c for c in CITIES if c in pct_no_access and c in pct_pop_no_access]
fig, ax = plt.subplots(figsize=(9, 5.5))
x = np.arange(len(cities_present))
width = 0.35
vals_hex = [pct_no_access[c] for c in cities_present]
vals_pop = [pct_pop_no_access[c] for c in cities_present]
ax.bar(x - width / 2, vals_hex, width, label="% heksagonów (nieważone)", color="#bdbdbd")
ax.bar(x + width / 2, vals_pop, width, label="% populacji 20-29 (ważone)",
       color=[CITY_COLOR[c] for c in cities_present])
ax.set_xticks(x)
ax.set_xticklabels([DISPLAY[c] for c in cities_present])
ax.set_ylabel("% bez dostępu do żadnej uczelni (≤30 min)")
ax.set_title("Deprywacja dostępu do uczelni, 6 miast (próg 30 min)\n"
             "nieważone (po heksagonach) vs ważone liczbą mieszkańców 20-29 lat", fontsize=12)
ax.legend(loc="upper right", fontsize=9)
for i, v in enumerate(vals_hex):
    ax.text(i - width / 2, v, f"{v:.0f}%", ha="center", va="bottom", fontsize=8)
for i, v in enumerate(vals_pop):
    ax.text(i + width / 2, v, f"{v:.0f}%", ha="center", va="bottom", fontsize=8)
fig.tight_layout()
fig.savefig(OUT / "cross_city_uni_no_access.png", dpi=150)
plt.close(fig)
print(f"wrote {OUT / 'cross_city_uni_no_access.png'}")
print("hex-count:", pct_no_access)
print("population-weighted:", pct_pop_no_access)
