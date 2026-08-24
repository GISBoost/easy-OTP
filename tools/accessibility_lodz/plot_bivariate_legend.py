"""Draw a compact legend key for the student-accessibility bivariate map:
3 hues (dominant university, by 30-min accessible-building count) x 3 shades
(population 20-29 tercile), matching lodz_hex500.gpkg's biv_color palette
(see join_students_results.py). Style loosely inspired by Joshua Stevens'
bivariate choropleth guide, adapted for a categorical x quantitative pairing.

Usage: py plot_bivariate_legend.py
"""
from pathlib import Path

import matplotlib.pyplot as plt

BASE = Path(__file__).parent
OUT = BASE / "out"
OUT.mkdir(exist_ok=True)

PALETTE = {
    "Politechnika\nŁódzka": ["#dbe8f7", "#7ea6d6", "#2c5c94"],
    "Uniwersytet\nŁódzki":  ["#fbe3d4", "#e8996b", "#b8531f"],
    "Uniwersytet\nMedyczny": ["#dcefdc", "#7fbf7f", "#2f7d2f"],
}
TIERS = ["niska", "średnia", "wysoka"]

fig, ax = plt.subplots(figsize=(6, 5))
unis = list(PALETTE.keys())
for col, uni in enumerate(unis):
    for row, shade in enumerate(PALETTE[uni]):
        ax.add_patch(plt.Rectangle((col, row), 1, 1, facecolor=shade, edgecolor="white"))

ax.add_patch(plt.Rectangle((3, 0), 1, 1, facecolor="#e6e6e6", edgecolor="white"))
ax.add_patch(plt.Rectangle((3, 1), 1, 1, facecolor="#bdbdbd", edgecolor="white"))
ax.add_patch(plt.Rectangle((3, 2), 1, 1, facecolor="#8a8a8a", edgecolor="white"))

ax.set_xlim(0, 4)
ax.set_ylim(0, 3)
ax.set_xticks([0.5, 1.5, 2.5, 3.5])
ax.set_xticklabels(unis + ["brak dostępu\n(≤30 min)"], fontsize=9)
ax.set_yticks([0.5, 1.5, 2.5])
ax.set_yticklabels([f"{t}\npopulacja 20-29" for t in TIERS], fontsize=9)
ax.set_title("Legenda: dominująca uczelnia (kolor) x populacja studencka (odcień)\n"
              "dominacja = najwięcej budynków tej uczelni osiągalnych w ≤30 min", fontsize=10)
ax.set_aspect("equal")
for spine in ax.spines.values():
    spine.set_visible(False)
fig.tight_layout()
fig.savefig(OUT / "lodz_students_bivariate_legend.png", dpi=150)
print(f"wrote {OUT / 'lodz_students_bivariate_legend.png'}")
