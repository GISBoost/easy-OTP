"""Loading tidy tables for chart_lab.

CL-2 only needs the bundled example; user-supplied files and the online catalogue are
later milestones (CL-4, CL-5).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from chart_lab import paths  # noqa: F401 - side effect: wires sys.path

from transit_charts import extract as extract_mod

EXAMPLE_DATA_DIR = Path(__file__).resolve().parent.parent / "example_data"
EXAMPLE_TABLE_PATH = EXAMPLE_DATA_DIR / "lodz_2026-07-23_example.csv.gz"


def load_example_table() -> pd.DataFrame:
    """Read the bundled Łódź 2026-07-23 example tidy table."""
    return extract_mod.read_table(EXAMPLE_TABLE_PATH)
