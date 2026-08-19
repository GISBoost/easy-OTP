"""Unit tests for chart_lab.data_sources (CL-4: user-uploaded tables + active-table state).

    cd tools\\chart_lab
    .venv\\Scripts\\python.exe -m pytest tests -q
"""
from __future__ import annotations

from pathlib import Path

import pytest

from chart_lab import paths  # noqa: F401 - side effect: wires sys.path
from chart_lab import data_sources
from transit_charts import sources

_MATCHED_CSV = Path(
    "../family_a_reconstruction/gtfs-manual-test/out_fa18/matched_lodz_2026-07-23.csv"
)
_STATIC_GTFS_ZIP = Path(
    "../family_a_reconstruction/gtfs-manual-test/static_gtfs/lodz_static_gtfs_2026-07-23.zip"
)


@pytest.fixture(autouse=True)
def _isolated_registry():
    """Each test gets a clean loaded-tables registry - it's module-level global state by
    design (chart_lab is a single-user local app, see data_sources.py's module docstring),
    but that means tests must reset it themselves rather than relying on process isolation.
    """
    saved_loaded = dict(data_sources._LOADED)
    saved_active = list(data_sources._ACTIVE_IDS)
    data_sources._LOADED.clear()
    data_sources._ACTIVE_IDS.clear()
    yield
    data_sources._LOADED.clear()
    data_sources._LOADED.update(saved_loaded)
    data_sources._ACTIVE_IDS[:] = saved_active


def test_load_user_table_rejects_missing_file():
    with pytest.raises(sources.InputError, match="not found"):
        data_sources.load_user_table(Path("does_not_exist.csv.gz"))


def test_load_user_table_rejects_empty_file(tmp_path):
    empty = tmp_path / "empty.csv"
    empty.write_text("")
    with pytest.raises(sources.InputError, match="empty"):
        data_sources.load_user_table(empty)


def test_load_user_table_rejects_a_matched_csv_with_a_clear_message():
    with pytest.raises(sources.InputError, match="missing column"):
        data_sources.load_user_table(_MATCHED_CSV)


def test_load_user_table_rejects_a_static_gtfs_zip_with_a_clear_message():
    with pytest.raises(sources.InputError, match="does not look like a tidy table"):
        data_sources.load_user_table(_STATIC_GTFS_ZIP)


def test_load_user_table_accepts_the_bundled_example_file():
    table = data_sources.load_user_table(data_sources.EXAMPLE_TABLE_PATH)
    assert not table.empty


def test_register_example_table_is_idempotent():
    first = data_sources.register_example_table()
    second = data_sources.register_example_table()
    assert first == second
    assert len(data_sources._LOADED) == 1


def test_register_user_table_is_additive_not_deduplicated():
    # Explicit design choice (see data_sources.py docstring): the same file registered twice
    # gets two ids, not merged - simpler than content-hash dedup, and harmless since active
    # tables are just concatenated for rendering.
    id_a = data_sources.register_user_table(data_sources.EXAMPLE_TABLE_PATH)
    id_b = data_sources.register_user_table(data_sources.EXAMPLE_TABLE_PATH)
    assert id_a != id_b
    assert len(data_sources.loaded_table_choices()) == 2


def test_active_ids_ignores_unknown_ids():
    example_id = data_sources.register_example_table()
    data_sources.set_active_ids([example_id, "nonexistent"])
    assert data_sources.get_active_ids() == [example_id]


def test_get_active_tables_returns_only_active_ones():
    example_id = data_sources.register_example_table()
    user_id = data_sources.register_user_table(data_sources.EXAMPLE_TABLE_PATH)
    data_sources.set_active_ids([example_id])
    assert len(data_sources.get_active_tables()) == 1
    data_sources.set_active_ids([example_id, user_id])
    assert len(data_sources.get_active_tables()) == 2
