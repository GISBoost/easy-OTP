"""RQ (multi-city Etap 5, POI-services study): is lower per-capita income a
predictor of lower accessibility to public services? Bin hexes into income
deciles, compute mean accessibility per decile, correlate, and plot -- one
city, reusable per-city. Read-only.

Usage: py analyze_services_income.py <city>
Output: <city>/out/{city}_income_decile_accessibility.csv,
        <city>/out/{city}_income_decile_bars.png
"""
import csv
import statistics
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

CITY = sys.argv[1]
BASE = Path(__file__).parent
# Lodz's pilot data lives in the sibling accessibility_lodz/ folder with its
# own (pre-multi-city) file naming -- reused here, not recomputed, so Lodz can
# sit in the same cross-city comparison as the 5 new cities.
CITY_DIR = BASE.parent / "accessibility_lodz" if CITY == "lodz" else BASE / CITY
OUT = CITY_DIR / "out"
OUT.mkdir(exist_ok=True)

CUTOFF = 30  # primary threshold, consistent with the Lodz pilot

# 1. pivot the long r5r output to wide (id -> {opp}_{cutoff}min)
acc_csv = CITY_DIR / f"{CITY}_service_accessibility.csv"
if not acc_csv.exists() and CITY == "lodz":
    acc_csv = CITY_DIR / "lodz_hex_accessibility.csv"  # Etap 3's hex-level services run
wide = {}
with open(acc_csv, encoding="utf-8", newline="") as f:
    for row in csv.DictReader(f):
        if int(row["cutoff"]) != CUTOFF:
            continue
        wide.setdefault(row["id"], {})[row["opportunity"]] = int(row["accessibility"])

# 2. join with hex-level income
ses = pd.read_csv(CITY_DIR / f"{CITY}_hex_ses.csv", dtype={"hex_id": str})
ses = ses.dropna(subset=["income_index_pln"])

rows = []
for _, r in ses.iterrows():
    acc = wide.get(r["hex_id"])
    if acc is None:
        continue
    rows.append({"hex_id": r["hex_id"], "income_index_pln": r["income_index_pln"],
                 "total_access": acc.get("total", 0)})
df = pd.DataFrame(rows)
if len(df) < 20:
    print(f"{CITY}: only {len(df)} hexes with both income and access data -- too few, aborting")
    sys.exit(1)

# 3. income deciles (D1 poorest -> D10 richest)
df["decile"] = pd.qcut(df["income_index_pln"], 10, labels=False, duplicates="drop") + 1
decile_stats = df.groupby("decile")["total_access"].agg(["mean", "median", "count"]).reset_index()
decile_stats.to_csv(OUT / f"{CITY}_income_decile_accessibility.csv", index=False)

r = statistics.correlation(df["income_index_pln"].tolist(), df["total_access"].tolist())
print(f"{CITY}: n={len(df)} hexes, income vs total_access({CUTOFF}min) r={r:+.3f}")
print(decile_stats.to_string(index=False))

# 4. plot -- sequential single-hue bars (magnitude by decile), dataviz-skill
# principle: sequential = one hue light->dark, not a categorical rainbow.
plt.rcParams.update({
    "figure.facecolor": "white", "axes.facecolor": "white",
    "axes.spines.top": False, "axes.spines.right": False, "font.size": 11,
})
fig, ax = plt.subplots(figsize=(8, 5.5))
cmap = plt.get_cmap("Blues")
colors = [cmap(0.3 + 0.6 * (d - 1) / 9) for d in decile_stats["decile"]]
ax.bar(decile_stats["decile"], decile_stats["mean"], color=colors, edgecolor="white")
ax.set_xticks(range(1, 11))
ax.set_xlabel("decyl dochodu (D1 = najbiedniejsze, D10 = najbogatsze obwody)")
ax.set_ylabel(f"średnia dostępność (liczba usług w {CUTOFF} min)")
ax.set_title(f"{CITY.capitalize()} · dochód a dostępność usług publicznych\n"
             f"r={r:+.3f} (n={len(df)} heksagonów, próg {CUTOFF} min)", fontsize=12)
for d, m, n in zip(decile_stats["decile"], decile_stats["mean"], decile_stats["count"]):
    ax.text(d, m, f"n={n}", ha="center", va="bottom", fontsize=7, color="#555")
fig.tight_layout()
fig.savefig(OUT / f"{CITY}_income_decile_bars.png", dpi=150)
plt.close(fig)
print(f"wrote {OUT / f'{CITY}_income_decile_bars.png'}")
