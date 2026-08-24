"""Passive accessibility metric: population with access to at least one facility
of a given category within a given time cutoff -- distinct from the existing
cumulative-opportunity-count metric (see COLUMNS.md for the difference).

For each (category, cutoff): a census tract "has access" iff its POI count for
that category/cutoff is >= 1. Population coverage = sum of population over
tracts that have access. Read lodz_accessibility_wide.csv (obwod-level, already
joined to population), write per-tract coverage flags + a city-wide summary.

Usage: py compute_population_coverage.py
Output: lodz_accessibility_wide.csv gets 20 new has_access_* columns (rewritten),
         out/lodz_population_coverage_summary.csv (city-wide %)
"""
import csv
from pathlib import Path

BASE = Path(__file__).parent
WIDE_CSV = BASE / "lodz_accessibility_wide.csv"
OUT = BASE / "out"
OUT.mkdir(exist_ok=True)

CATEGORIES = ["education", "health", "culture", "groceries", "total"]
CUTOFFS = [15, 30, 45, 60]

with open(WIDE_CSV, encoding="utf-8", newline="") as f:
    rows = list(csv.DictReader(f))

has_access_cols = []
for cat in CATEGORIES:
    for cutoff in CUTOFFS:
        col = f"{cat}_{cutoff}min"
        flag_col = f"has_access_{cat}_{cutoff}min"
        has_access_cols.append(flag_col)
        for r in rows:
            r[flag_col] = "1" if r.get(col) and int(r[col]) >= 1 else "0"

fieldnames = list(rows[0].keys())
with open(WIDE_CSV, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(rows)
print(f"added {len(has_access_cols)} has_access_* columns to {WIDE_CSV}")

# city-wide population coverage %
total_pop = sum(float(r["population"]) for r in rows if r["population"])
summary = []
for cat in CATEGORIES:
    for cutoff in CUTOFFS:
        flag_col = f"has_access_{cat}_{cutoff}min"
        covered_pop = sum(float(r["population"]) for r in rows
                           if r["population"] and r[flag_col] == "1")
        summary.append({
            "category": cat, "cutoff_min": cutoff,
            "population_covered": round(covered_pop),
            "population_total": round(total_pop),
            "pct_covered": round(100 * covered_pop / total_pop, 1),
        })

with open(OUT / "lodz_population_coverage_summary.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=["category", "cutoff_min", "population_covered",
                                        "population_total", "pct_covered"])
    w.writeheader()
    w.writerows(summary)

print("\nPopulacja z dostępem do >=1 placówki danej kategorii, w progu czasu:")
print(f"{'kategoria':10s} {'15min':>10s} {'30min':>10s} {'45min':>10s} {'60min':>10s}")
for cat in CATEGORIES:
    vals = [s for s in summary if s["category"] == cat]
    cells = " ".join(f"{v['pct_covered']:>9.1f}%" for v in vals)
    print(f"{cat:10s} {cells}")
