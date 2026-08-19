"""Loading tidy tables for chart_lab, and tracking which ones are currently active.

CL-2 added the bundled example. CL-4 adds user-uploaded files and the "which loaded tables
are active right now" registry both feed - a plain module-level dict/list, not `gr.State`:
chart_lab is a single-user local desktop app (PRD §1/§6 - Windows only, local, no hosting),
so there is exactly one session to track and per-session state would be pure overhead. The
online catalogue (CL-5) is the remaining data source.
"""
from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from chart_lab import paths  # noqa: F401 - side effect: wires sys.path

from transit_charts import extract as extract_mod, sources

EXAMPLE_DATA_DIR = Path(__file__).resolve().parent.parent / "example_data"
EXAMPLE_TABLE_PATH = EXAMPLE_DATA_DIR / "lodz_2026-07-23_example.csv.gz"
_EXAMPLE_ID = "example:lodz_2026-07-23"


def load_example_table() -> pd.DataFrame:
    """Read the bundled Łódź 2026-07-23 example tidy table."""
    return extract_mod.read_table(EXAMPLE_TABLE_PATH)


def load_user_table(path: Path) -> pd.DataFrame:
    """Read a user-supplied tidy table, failing with a clear message on anything else.

    `extract_mod.read_table` already rejects a tidy CSV missing columns (a stale table from
    an older transit_charts version), but a `matched.csv` or a static GTFS `.zip` fed in by
    mistake fails differently - wrong dtype, or not text/CSV at all - and pandas's own
    exceptions for that are not something a non-technical user should ever see verbatim.
    """
    if not path.exists():
        raise sources.InputError(f"{path.name}: file not found")
    if path.stat().st_size == 0:
        raise sources.InputError(f"{path.name}: file is empty")
    try:
        table = extract_mod.read_table(path)
    except sources.InputError:
        raise  # already a clear, specific message (e.g. missing tidy columns)
    except (UnicodeDecodeError, zipfile.BadZipFile, pd.errors.ParserError,
            pd.errors.EmptyDataError, ValueError, OSError) as exc:
        raise sources.InputError(
            f"{path.name} does not look like a tidy table from `transit_charts extract` "
            f"({type(exc).__name__}: {exc})"
        ) from exc
    if table.empty:
        raise sources.InputError(f"{path.name}: tidy table has zero rows")
    return table


@dataclass
class _LoadedTable:
    label: str
    table: pd.DataFrame
    path: Path


def _label_for(table: pd.DataFrame, fallback: str) -> str:
    city = str(table.city.iloc[0]) if "city" in table.columns and not table.empty else "?"
    dates = sorted(set(table.get("service_date", pd.Series(dtype=object)).dropna().astype(str)))
    if len(dates) == 1:
        date_part = dates[0]
    elif dates:
        date_part = f"{dates[0]}..{dates[-1]} ({len(dates)} days)"
    else:
        date_part = fallback
    return f"{city} {date_part}"


# id -> loaded table. A plain dict, not deduplicated by content hash: letting the user select
# the same data twice (e.g. the bundled example and a manually re-downloaded copy of it) is
# harmless - concatenation just repeats rows - and hashing every load to prevent that would be
# solving a problem that doesn't cost anything to leave alone. The label (city + date range)
# makes an accidental duplicate obvious to the user anyway.
_LOADED: dict[str, _LoadedTable] = {}
_ACTIVE_IDS: list[str] = []


def register_example_table() -> str:
    """Ensure the bundled example is loaded; returns its id. Idempotent."""
    if _EXAMPLE_ID not in _LOADED:
        table = load_example_table()
        _LOADED[_EXAMPLE_ID] = _LoadedTable(
            label=_label_for(table, "example"), table=table, path=EXAMPLE_TABLE_PATH,
        )
    return _EXAMPLE_ID


def register_user_table(path: Path) -> str:
    """Load and register a user-supplied file; returns its id. Raises sources.InputError."""
    table = load_user_table(path)
    table_id = f"user:{path.name}:{len(_LOADED)}"
    _LOADED[table_id] = _LoadedTable(label=_label_for(table, path.name), table=table, path=path)
    return table_id


def loaded_table_choices() -> list[tuple[str, str]]:
    """(label, id) pairs for every currently loaded table, in load order."""
    return [(lt.label, table_id) for table_id, lt in _LOADED.items()]


def get_active_ids() -> list[str]:
    return list(_ACTIVE_IDS)


def set_active_ids(ids: list[str]) -> None:
    _ACTIVE_IDS[:] = [i for i in ids if i in _LOADED]


def get_active_tables() -> list[pd.DataFrame]:
    """The `Callable[[], list[pandas.DataFrame]]` chart_lab.widgets.build_chart_ui expects."""
    return [_LOADED[i].table for i in _ACTIVE_IDS if i in _LOADED]


def get_active_paths() -> list[Path]:
    """Source file for each active table, in the same order as get_active_tables()."""
    return [_LOADED[i].path for i in _ACTIVE_IDS if i in _LOADED]
