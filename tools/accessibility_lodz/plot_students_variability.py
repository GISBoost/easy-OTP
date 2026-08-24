"""Boxplot-style charts for the student->university reliability analysis,
transit_charts style (self-explanatory titles, PNG+CSV). Two panels:
Method C (P50 vs P85 % impact) grouped by dominant university zone, and
Method A (relative intra-day spread) vs distance-to-center scatter.

Usage: py plot_students_variability.py
"""
import csv
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

BASE = Path(__file__).parent
OUT = BASE / "out"
OUT.mkdir(exist_ok=True)

plt.rcParams.update({
    "figure.facecolor": "white", "axes.facecolor": "white",
    "axes.spines.top": False, "axes.spines.right": False, "font.size": 11,
})

# join method C impact with dominant university
dominant = {}
with open(BASE / "lodz_students_wide.csv", encoding="utf-8", newline="") as f:
    for row in csv.DictReader(f):
        dominant[row["hex_id"]] = row["dominant_university"]

impacts_by_dom = {"politechnika": [], "uniwersytet": [], "medyczny": [], "none": []}
with open(OUT / "lodz_students_method_C_p50_vs_p85.csv", encoding="utf-8", newline="") as f:
    for row in csv.DictReader(f):
        v = row["total_pct_impact"]
        if v == "" or v is None:
            continue
        dom = dominant.get(row["hex_id"], "none")
        impacts_by_dom.setdefault(dom, []).append(float(v))

labels_pl = {"politechnika": "Politechnika\nŁódzka", "uniwersytet": "Uniwersytet\nŁódzki",
             "medyczny": "Uniwersytet\nMedyczny", "none": "brak dostępu\n(P50, 30min)"}
groups = [g for g in ("politechnika", "uniwersytet", "medyczny", "none") if impacts_by_dom[g]]

fig, ax = plt.subplots(figsize=(7.5, 5.5))
bp = ax.boxplot([impacts_by_dom[g] for g in groups], tick_labels=[labels_pl[g] for g in groups],
                patch_artist=True, showfliers=True)
colors = {"politechnika": "#7ea6d6", "uniwersytet": "#e8996b", "medyczny": "#7fbf7f", "none": "#bdbdbd"}
for patch, g in zip(bp["boxes"], groups):
    patch.set_facecolor(colors[g])
ax.axhline(0, color="black", linewidth=0.8)
ax.set_ylabel("zmiana dostępności P85 vs P50 (%)")
ax.set_title("Metoda C · wpływ 'złego dnia' (P85) na dostępność studencką, wg strefy dominującej uczelni\n"
             "próg 30 min, wszystkie uczelnie razem ('total')", fontsize=11)
ns = [f"n={len(impacts_by_dom[g])}" for g in groups]
for i, n in enumerate(ns, start=1):
    ax.text(i, ax.get_ylim()[0], n, ha="center", va="top", fontsize=8, color="#555")
fig.tight_layout()
fig.savefig(OUT / "lodz_students_method_C_boxplot.png", dpi=150)
plt.close(fig)
print(f"wrote {OUT / 'lodz_students_method_C_boxplot.png'}")

# Method A: relative spread vs distance
dist, rel = [], []
with open(OUT / "lodz_students_method_A_percentile_spread.csv", encoding="utf-8", newline="") as f:
    for row in csv.DictReader(f):
        if row["p50"] == "0" or row["p50"] == "":
            continue
        dist.append(float(row["dist_km"]))
        rel.append(float(row["spread_p5_p95"]) / float(row["p50"]))

fig, ax = plt.subplots(figsize=(7.5, 5.5))
ax.scatter(dist, rel, alpha=0.5, s=18, color="#4C72B0")
z = np.polyfit(dist, rel, 1)
xs_fit = np.linspace(min(dist), max(dist), 50)
ax.plot(xs_fit, np.polyval(z, xs_fit), color="red", linestyle="--", linewidth=1.5)
ax.set_xlabel("odległość od centrum miasta (km)")
ax.set_ylabel("względny rozrzut dostępności w ciągu dnia\n(p5-p95)/mediana, 06:00-22:00")
ax.set_title("Metoda A · zmienność dostępności studenckiej w ciągu dnia vs odległość od centrum\n"
             "próg 30 min, 'total' (wszystkie 3 uczelnie)", fontsize=11)
fig.tight_layout()
fig.savefig(OUT / "lodz_students_method_A_scatter.png", dpi=150)
plt.close(fig)
print(f"wrote {OUT / 'lodz_students_method_A_scatter.png'}")
