"""Join r5r accessibility results back to SES fields (income, single-motherhood
share, population) per obwod spisowy, pivot to wide, test the core hypothesis:
does lower income correlate with worse transport accessibility?

Read-only. Usage: py analyze_accessibility.py
"""
import csv
import sqlite3
import statistics
from pathlib import Path

BASE = Path(__file__).parent
ACC_CSV = BASE / "lodz_accessibility.csv"
SES_GPKG = BASE.parent / "ses_income_lodz" / "lodz.gpkg"
OUT_WIDE = BASE / "lodz_accessibility_wide.csv"

# 1. pivot long -> wide (id, {opportunity}_{cutoff}min)
wide = {}
with open(ACC_CSV, encoding="utf-8", newline="") as f:
    for row in csv.DictReader(f):
        d = wide.setdefault(row["id"], {})
        d[f"{row['opportunity']}_{row['cutoff']}min"] = int(row["accessibility"])

# 2. pull SES fields keyed by OBWOD
conn = sqlite3.connect(f"file:{SES_GPKG}?mode=ro", uri=True)
ses = {}
for obwod, income, pop, single_moms in conn.execute(
    "SELECT OBWOD, income_index_pln, population, fam_pct_matki_samotne FROM obwody_spisowe"
):
    ses[str(obwod)] = (income, pop, single_moms)
conn.close()

fieldnames = ["id", "income_index_pln", "population", "fam_pct_matki_samotne"] + sorted(
    next(iter(wide.values())).keys()
)
rows = []
for oid, acc in wide.items():
    income, pop, single_moms = ses.get(oid, (None, None, None))
    row = {"id": oid, "income_index_pln": income, "population": pop,
           "fam_pct_matki_samotne": single_moms}
    row.update(acc)
    rows.append(row)

with open(OUT_WIDE, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(rows)
print(f"wrote {len(rows)} rows to {OUT_WIDE}")

# 3. correlation: income vs accessibility, for each opportunity type at 30min cutoff
# (30 min = realistic day-to-day threshold; also report 15min as a stricter check)
print("\nH6: income_index_pln vs cumulative-opportunity accessibility (Pearson r)")
for cutoff in ("15min", "30min", "60min"):
    print(f"\n-- cutoff {cutoff} --")
    for opp in ("education", "health", "culture", "groceries", "total"):
        col = f"{opp}_{cutoff}"
        pairs = [(r["income_index_pln"], r[col]) for r in rows
                 if r["income_index_pln"] is not None and r[col] is not None]
        xs, ys = zip(*pairs)
        r = round(statistics.correlation(list(xs), list(ys)), 3)
        print(f"  {opp:10s} r={r:+.3f}  (n={len(pairs)}, mean {opp}={sum(ys)/len(ys):.1f})")

# 4. same, for single-motherhood share vs accessibility (double-deprivation check)
print("\nSecondary: %matek samotnych vs accessibility_total (30min)")
pairs = [(r["fam_pct_matki_samotne"], r["total_30min"]) for r in rows
         if r["fam_pct_matki_samotne"] is not None and r["total_30min"] is not None]
xs, ys = zip(*pairs)
print(f"  r={round(statistics.correlation(list(xs), list(ys)), 3):+.3f} (n={len(pairs)})")
