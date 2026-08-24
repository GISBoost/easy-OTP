"""Extract total population per obwod spisowy (Lodz) from GUS NSP 2021 Excel.

Standalone replica of easy_otp/algorithms/prepare_student_layer.py's key-building
logic (same join-key convention as the plugin, so it stays consistent with the
GUS geometry's OBWOD field), run outside QGIS since the plugin isn't loaded in
this session. Run with the QGIS-bundled python3.exe (has openpyxl).
"""
import csv
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(r"C:\Users\Michal\Desktop\easy\easy-OTP")
READER = REPO / "easy_otp" / "core" / "xlsx_reader.py"
EXCEL = REPO / "docs" / "gis" / "ludnosc_nsp_2021.xlsx"
OUT_CSV = REPO / "tools" / "ses_income_lodz" / "lodz_population.csv"

args_json = json.dumps({"path": str(EXCEL), "sheet": "Łódzkie"}, ensure_ascii=False)
proc = subprocess.run(
    [sys.executable, str(READER), args_json],
    capture_output=True, text=True, encoding="utf-8", timeout=120,
)
if proc.returncode != 0:
    raise SystemExit(f"reader failed: {proc.stderr[-2000:]}")
data = json.loads(proc.stdout)
rows = data["rows"]

struct_row_idx = pop_row_idx = None
col_symbol = col_struktura = col_population = None
POP_COL = "Ogółem"

for i, row in enumerate(rows[:30]):
    row_strs = [str(v).strip() if v is not None else "" for v in row]
    if struct_row_idx is None and "Struktura" in row_strs:
        sym_col = next((j for j, s in enumerate(row_strs) if s == "Symbol" or s.startswith("Symbol ")), None)
        if sym_col is not None:
            struct_row_idx = i
            col_symbol = sym_col
            col_struktura = row_strs.index("Struktura")
    if col_population is None and POP_COL in row_strs:
        pop_row_idx = i
        col_population = row_strs.index(POP_COL)
    if struct_row_idx is not None and col_population is not None:
        break

assert struct_row_idx is not None and col_population is not None, "header not found"
header_row_idx = max(struct_row_idx, pop_row_idx)
print(f"header row={header_row_idx} symbol_col={col_symbol} struktura_col={col_struktura} pop_col={col_population}")

excel_data: dict[str, float] = {}
current_rejon = None
tract_count = 0

for row in rows[header_row_idx + 1:]:
    val_symbol = row[col_symbol] if col_symbol < len(row) else None
    val_struktura = row[col_struktura] if col_struktura < len(row) else None
    val_pop = row[col_population] if col_population < len(row) else None
    if val_struktura is None:
        continue
    s = str(val_struktura).strip()

    if s == "rejon statystyczny":
        if isinstance(val_symbol, (int, float)):
            rej_str = str(int(val_symbol))
            current_rejon = rej_str.zfill(6) if len(rej_str) == 5 else rej_str
        else:
            current_rejon = str(val_symbol).strip() if val_symbol is not None else ""
        continue

    if s != "obwód spisowy":
        continue

    if isinstance(val_symbol, (int, float)):
        sym_str = str(int(val_symbol))
        sym = sym_str.zfill(7) if len(sym_str) == 6 else sym_str
    else:
        sym = str(val_symbol).strip() if val_symbol is not None else ""
    key = sym if len(sym) >= 7 else (current_rejon + sym)

    if val_pop is None:
        pop_value = 0.0
    elif isinstance(val_pop, (int, float)):
        pop_value = float(val_pop)
    else:
        sp = str(val_pop).strip()
        pop_value = 0.0 if sp in ("-", "") else float(sp)

    tract_count += 1
    excel_data[key] = excel_data.get(key, 0.0) + pop_value

print(f"tract rows: {tract_count}, unique keys: {len(excel_data)}")

with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["OBWOD", "pop_total"])
    for k, v in sorted(excel_data.items()):
        w.writerow([k, v])
print(f"wrote {OUT_CSV}")
