"""Extract population aged 20-29 (student-age proxy) per obwod spisowy from a
pre-dumped GUS NSP 2021 sheet JSON -- same source file as ses_income_lodz's
population extraction (docs/gis/ludnosc_nsp_2021.xlsx), different column
("20-29" under the "10-letnie grupy wieku" sub-header, not "Ogolem").

Same key-building / struktura-filtering logic as
ses_income_lodz/extract_population_generic.py, copied rather than imported
(that script lives in a sibling tool folder with its own lifecycle).

Usage: python extract_age2029_generic.py <rows_json_path> <out_csv_path>
"""
import csv
import json
import sys
from pathlib import Path

rows_json_path = Path(sys.argv[1])
out_csv_path = Path(sys.argv[2])

data = json.loads(rows_json_path.read_text(encoding="utf-8"))
rows = data["rows"]

struct_row_idx = age_row_idx = None
col_symbol = col_struktura = col_age2029 = None

for i, row in enumerate(rows[:30]):
    row_strs = [str(v).strip() if v is not None else "" for v in row]
    if struct_row_idx is None and "Struktura" in row_strs:
        sym_col = next((j for j, s in enumerate(row_strs) if s == "Symbol" or s.startswith("Symbol ")), None)
        if sym_col is not None:
            struct_row_idx = i
            col_symbol = sym_col
            col_struktura = row_strs.index("Struktura")
    if col_age2029 is None and "20-29" in row_strs:
        age_row_idx = i
        col_age2029 = row_strs.index("20-29")
    if struct_row_idx is not None and col_age2029 is not None:
        break

assert struct_row_idx is not None and col_age2029 is not None, "header not found"
header_row_idx = max(struct_row_idx, age_row_idx)
print(f"header row={header_row_idx} symbol_col={col_symbol} struktura_col={col_struktura} age2029_col={col_age2029}")

excel_data: dict[str, float] = {}
current_rejon = None
tract_count = 0

for row in rows[header_row_idx + 1:]:
    val_symbol = row[col_symbol] if col_symbol < len(row) else None
    val_struktura = row[col_struktura] if col_struktura < len(row) else None
    val_age = row[col_age2029] if col_age2029 < len(row) else None
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

    if s != "obwod spisowy" and s != "obwód spisowy":
        continue

    if isinstance(val_symbol, (int, float)):
        sym_str = str(int(val_symbol))
        sym = sym_str.zfill(7) if len(sym_str) == 6 else sym_str
    else:
        sym = str(val_symbol).strip() if val_symbol is not None else ""
    key = sym if len(sym) >= 7 else (current_rejon + sym)

    if val_age is None:
        age_value = 0.0
    elif isinstance(val_age, (int, float)):
        age_value = float(val_age)
    else:
        sa = str(val_age).strip()
        age_value = 0.0 if sa in ("-", "") else float(sa)

    tract_count += 1
    excel_data[key] = excel_data.get(key, 0.0) + age_value

print(f"tract rows: {tract_count}, unique keys: {len(excel_data)}")

with open(out_csv_path, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["OBWOD", "pop_20_29"])
    for k, v in sorted(excel_data.items()):
        w.writerow([k, v])
print(f"wrote {out_csv_path}")
