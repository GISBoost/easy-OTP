"""Chart the income-vs-accessibility hypothesis (H6) for Lodz, transit_charts style:
self-explanatory titles, one clear question per panel, PNG + CSV sidecar with the
numbers on the plot. Read-only against lodz_accessibility.gpkg / lodz_accessibility_wide.csv.

Usage: py plot_correlations.py
Output: out/lodz_H6_correlation_bars.png, out/lodz_H6_income_scatter.png,
        out/lodz_H6_distance_scatter.png (+ matching .csv)
"""
import csv
import statistics
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np

BASE = Path(__file__).parent
OUT = BASE / "out"
OUT.mkdir(exist_ok=True)

plt.rcParams.update({
    "figure.facecolor": "white", "axes.facecolor": "white",
    "axes.spines.top": False, "axes.spines.right": False,
    "font.size": 11,
})

CATEGORIES = ["education", "health", "culture", "groceries", "total"]
CUTOFFS = [15, 30, 45, 60]
CAT_COLORS = {"education": "#4C72B0", "health": "#DD8452", "culture": "#55A868",
              "groceries": "#C44E52", "total": "#333333"}

# 1. load wide table
rows = []
with open(BASE / "lodz_accessibility_wide.csv", encoding="utf-8", newline="") as f:
    for r in csv.DictReader(f):
        rows.append(r)

# ---------------------------------------------------------------------------
# Panel A: Pearson r of income vs accessibility, grouped bar by cutoff x category
# ---------------------------------------------------------------------------
corr = {cat: [] for cat in CATEGORIES}
for cat in CATEGORIES:
    for cutoff in CUTOFFS:
        col = f"{cat}_{cutoff}min"
        pairs = [(float(r["income_index_pln"]), float(r[col])) for r in rows
                 if r["income_index_pln"] and r[col]]
        xs, ys = zip(*pairs)
        corr[cat].append(statistics.correlation(list(xs), list(ys)))

fig, ax = plt.subplots(figsize=(9, 5.5))
x = np.arange(len(CUTOFFS))
width = 0.16
for i, cat in enumerate(CATEGORIES):
    ax.bar(x + (i - 2) * width, corr[cat], width, label=cat, color=CAT_COLORS[cat])
ax.axhline(0, color="black", linewidth=0.8)
ax.set_xticks(x)
ax.set_xticklabels([f"{c} min" for c in CUTOFFS])
ax.set_ylabel("współczynnik korelacji Pearsona (r)")
ax.set_title("H6 · dochód (income_index_pln) vs dostępność do usług — Łódź\n"
              "korelacja słaba wszędzie (|r|<0.13) i zmienia znak z progiem czasowym", fontsize=12)
ax.legend(ncol=5, loc="upper center", bbox_to_anchor=(0.5, -0.12), frameon=False)
ax.text(0.01, 0.02,
        "ujemna przy 15 min = biedniejsze obwody mają WIĘCEJ usług w zasięgu spaceru+tramwaju,\n"
        "nie mniej — patrz wykres dystansu od centrum obok",
        transform=ax.transAxes, fontsize=8.5, color="#555555", va="bottom")
fig.tight_layout()
fig.savefig(OUT / "lodz_H6_correlation_bars.png", dpi=150)
plt.close(fig)

with open(OUT / "lodz_H6_correlation_bars.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["category", "cutoff_min", "pearson_r"])
    for cat in CATEGORIES:
        for cutoff, r in zip(CUTOFFS, corr[cat]):
            w.writerow([cat, cutoff, round(r, 4)])

# ---------------------------------------------------------------------------
# Panel B: income vs total_60min scatter (hexbin, n=3854 -> overplotting)
# ---------------------------------------------------------------------------
income = np.array([float(r["income_index_pln"]) for r in rows if r["income_index_pln"]])
total60 = np.array([float(r["total_60min"]) for r in rows if r["income_index_pln"]])
r60 = statistics.correlation(list(income), list(total60))

fig, ax = plt.subplots(figsize=(7.5, 6))
hb = ax.hexbin(income, total60, gridsize=40, cmap="viridis", mincnt=1)
fig.colorbar(hb, ax=ax, label="liczba obwodów spisowych")
z = np.polyfit(income, total60, 1)
xs_fit = np.linspace(income.min(), income.max(), 50)
ax.plot(xs_fit, np.polyval(z, xs_fit), color="red", linewidth=1.5, linestyle="--")
ax.set_xlabel("income_index_pln (szacowany dochód obwodu głosowania)")
ax.set_ylabel("liczba usług osiągalnych w 60 min (walk+transit)")
ax.set_title(f"H6 · dochód vs dostępność (próg 60 min) — Łódź\nr = {r60:+.3f}, n = {len(income)}"
             " — praktycznie brak związku", fontsize=12)
fig.tight_layout()
fig.savefig(OUT / "lodz_H6_income_scatter.png", dpi=150)
plt.close(fig)

with open(OUT / "lodz_H6_income_scatter.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["id", "income_index_pln", "total_60min"])
    for r in rows:
        if r["income_index_pln"]:
            w.writerow([r["id"], r["income_index_pln"], r["total_60min"]])

# ---------------------------------------------------------------------------
# Panel C: distance-to-center vs accessibility -- the actual driver
# ---------------------------------------------------------------------------
g = gpd.read_file(BASE.parent / "ses_income_lodz" / "lodz.gpkg", layer="obwody_spisowe")
city_center = g.geometry.union_all().centroid  # EPSG:2180, metres
g["dist_km"] = g.geometry.centroid.distance(city_center) / 1000.0
g["OBWOD"] = g["OBWOD"].astype(str)
dist_by_id = dict(zip(g["OBWOD"], g["dist_km"]))

dist = np.array([dist_by_id[r["id"]] for r in rows if r["id"] in dist_by_id])
total30 = np.array([float(r["total_30min"]) for r in rows if r["id"] in dist_by_id])
r_dist = statistics.correlation(list(dist), list(total30))

fig, ax = plt.subplots(figsize=(7.5, 6))
hb = ax.hexbin(dist, total30, gridsize=40, cmap="viridis", mincnt=1)
fig.colorbar(hb, ax=ax, label="liczba obwodów spisowych")
z = np.polyfit(dist, total30, 1)
xs_fit = np.linspace(dist.min(), dist.max(), 50)
ax.plot(xs_fit, np.polyval(z, xs_fit), color="red", linewidth=1.5, linestyle="--")
ax.set_xlabel("odległość centroidu obwodu od centrum miasta (km)")
ax.set_ylabel("liczba usług osiągalnych w 30 min (walk+transit)")
ax.set_title(f"Odległość od centrum vs dostępność (próg 30 min) — Łódź\nr = {r_dist:+.3f}, n = {len(dist)}"
             " — to jest prawdziwy sterownik dostępności, nie dochód", fontsize=12)
fig.tight_layout()
fig.savefig(OUT / "lodz_H6_distance_scatter.png", dpi=150)
plt.close(fig)

with open(OUT / "lodz_H6_distance_scatter.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["id", "dist_km", "total_30min"])
    for r in rows:
        if r["id"] in dist_by_id:
            w.writerow([r["id"], round(dist_by_id[r["id"]], 3), r["total_30min"]])

print(f"income vs total_60min: r={r60:+.3f}")
print(f"distance vs total_30min: r={r_dist:+.3f}")
print(f"wrote 3 charts + 3 CSVs to {OUT}")
