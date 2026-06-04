"""Optional statistics export for RunTemporalAccessibility (A-3)."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qgis.core import QgsVectorLayer

from .zonal import _CATEGORY_ORDER

_COL_KEYS = {
    "constantly accessible": "constantly_accessible",
    "regularly accessible": "regularly_accessible",
    "periodically accessible": "periodically_accessible",
    "episodically accessible": "episodically_accessible",
    "": "inaccessible",
}


def write_report(
    hex_layer: "QgsVectorLayer",
    params_dict: dict,
    output_path: str,
) -> str:
    """Write per-category statistics to .xlsx or .csv; return the path written.

    One wide row per call; appends to an existing file.
    Falls back to .csv (changing the suffix) if openpyxl is unavailable.

    params_dict keys: analysis_date, destination_lat, destination_lon,
    threshold_min, window_start, window_end, interval_min.
    """
    from .dependencies import ensure_openpyxl  # noqa: PLC0415

    class_idx = hex_layer.fields().indexOf("st_class")
    counts: dict[str, int] = {}
    total = 0
    for feature in hex_layer.getFeatures():
        total += 1
        val = feature[class_idx]
        key = val if isinstance(val, str) else ""
        counts[key] = counts.get(key, 0) + 1

    row: dict[str, object] = {
        "analysis_date": params_dict.get("analysis_date", ""),
        "destination_lat": params_dict.get("destination_lat", ""),
        "destination_lon": params_dict.get("destination_lon", ""),
        "threshold_min": params_dict.get("threshold_min", ""),
        "window_start": params_dict.get("window_start", ""),
        "window_end": params_dict.get("window_end", ""),
        "interval_min": params_dict.get("interval_min", ""),
    }
    for cat in _CATEGORY_ORDER:
        col = _COL_KEYS[cat]
        cnt = counts.get(cat, 0)
        pct = round(cnt / total * 100, 2) if total > 0 else 0.0
        row[f"count_{col}"] = cnt
        row[f"pct_{col}"] = pct
    row["count_total"] = total

    path = Path(output_path)
    use_xlsx = ensure_openpyxl() and path.suffix.lower() == ".xlsx"
    if not use_xlsx and path.suffix.lower() == ".xlsx":
        path = path.with_suffix(".csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    if use_xlsx:
        _write_xlsx(row, path)
    else:
        _write_csv(row, path)
    return str(path)


def _write_xlsx(row: dict, path: Path) -> None:
    import openpyxl  # noqa: PLC0415

    if path.exists():
        wb = openpyxl.load_workbook(path)
        ws = wb.active
    else:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "easy-OTP report"
        ws.append(list(row.keys()))
    ws.append(list(row.values()))
    wb.save(path)


def _write_csv(row: dict, path: Path) -> None:
    write_header = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        if write_header:
            writer.writeheader()
        writer.writerow(row)
