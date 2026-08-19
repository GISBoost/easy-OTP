"""Unit tests for chart_lab.widgets, run with `chart_lab`'s own venv.

    cd tools\\chart_lab
    .venv\\Scripts\\python.exe -m pytest tests -q
"""
from __future__ import annotations

import pandas as pd

from chart_lab import paths  # noqa: F401 - side effect: wires sys.path
from chart_lab import data_sources
from chart_lab.widgets import (
    _route_choices,
    build_chart_ui,
    render_chart,
    reset_for_chart,
    validate_active_tables,
)
from transit_charts.registry import build_registry

_REGISTRY = build_registry()


def _fabricate_table(**overrides) -> pd.DataFrame:
    """A minimal one-row tidy table, just enough for city/day grouping checks."""
    base = dict(city="lodz", service_date="2026-07-23", day_type="weekday",
                route_short_name="11", route_group="11", direction_id="0")
    base.update(overrides)
    return pd.DataFrame([base])


def test_d15_needs_at_least_three_tables():
    spec = _REGISTRY["D15"]
    assert validate_active_tables(spec, [_fabricate_table()]) is not None
    assert validate_active_tables(spec, [_fabricate_table()] * 2) is not None
    assert validate_active_tables(spec, [_fabricate_table()] * 3) is None


def test_e20_and_j39_need_at_least_two_cities():
    for key in ("E20", "J39"):
        spec = _REGISTRY[key]
        one_city = [_fabricate_table(city="lodz"), _fabricate_table(city="lodz")]
        assert validate_active_tables(spec, one_city) is not None
        two_cities = [_fabricate_table(city="lodz"), _fabricate_table(city="warsaw")]
        assert validate_active_tables(spec, two_cities) is None


def test_single_day_series_chart_has_no_multi_day_requirement():
    spec = _REGISTRY["C9"]
    assert validate_active_tables(spec, [_fabricate_table()]) is None


def test_route_choices_union_short_name_and_group():
    table = pd.DataFrame([
        dict(route_short_name="10A", route_group="10"),
        dict(route_short_name="10B", route_group="10"),
        dict(route_short_name="11", route_group="11"),
    ])
    assert _route_choices([table]) == ["10", "10A", "10B", "11"]


def test_every_registry_chart_has_a_matching_widget_rule():
    # Every field build_chart_ui/reset_for_chart reads off a ChartSpec must exist - this is
    # the "does the registry contract actually cover what the UI needs" check the PRD asks for.
    required_fields = {
        "route_mode", "supports_direction", "bucket_minutes_default", "min_n_default",
        "min_trip_coverage_default", "supports_combine", "supports_annotate",
        "annotate_default", "supports_threshold", "threshold_default",
        "exclude_route_aware", "interactive_capable", "multi_day_by_design",
    }
    for spec in _REGISTRY.values():
        for field in required_fields:
            assert hasattr(spec, field), f"{spec.key} missing {field}"


def test_build_chart_ui_is_importable_and_callable():
    assert callable(build_chart_ui)


# --- reset_for_chart: widget visibility/defaults must match PRD §3's table exactly --------


def _get_example_tables():
    return [data_sources.load_example_table()]


def _reset_dict(chart_key: str) -> dict:
    (route, direction, bucket, min_n, min_trip_cov, combine, annotate, threshold,
     exclude_route, html) = reset_for_chart(_REGISTRY, _get_example_tables, chart_key)
    return dict(route=route, direction=direction, bucket=bucket, min_n=min_n,
                min_trip_cov=min_trip_cov, combine=combine, annotate=annotate,
                threshold=threshold, exclude_route=exclude_route, html=html)


def test_c9_widget_visibility_single_route_chart():
    r = _reset_dict("C9")
    assert r["route"]["visible"] is True
    assert r["direction"]["visible"] is True
    assert r["bucket"]["visible"] is False  # C9 has no bucket_minutes
    assert r["min_n"]["visible"] is True and r["min_n"]["value"] == 20
    assert r["min_trip_cov"]["visible"] is True
    assert r["combine"]["visible"] is False
    assert r["annotate"]["visible"] is False
    assert r["threshold"]["visible"] is False
    assert r["exclude_route"]["visible"] is False
    assert r["html"]["visible"] is True  # C9 is interactive-capable


def test_c10_widget_visibility_multi_route_chart():
    r = _reset_dict("C10")
    assert r["route"]["value"] == []  # multi-route: no forced default
    assert r["exclude_route"]["visible"] is True
    assert r["bucket"]["visible"] is True and r["bucket"]["value"] == 15
    assert r["direction"]["visible"] is False  # C10 does not support_direction


def test_c11_combine_flag_visible_only_for_c11():
    assert _reset_dict("C11")["combine"]["visible"] is True
    assert _reset_dict("C9")["combine"]["visible"] is False


def test_b8_threshold_visible_only_for_threshold_charts():
    assert _reset_dict("B8")["threshold"]["visible"] is True
    assert _reset_dict("C9")["threshold"]["visible"] is False


def test_d15_annotate_visible_and_route_widget_hidden_for_e20_j39():
    assert _reset_dict("D15")["annotate"]["visible"] is True
    for key in ("E20", "J39"):
        assert _reset_dict(key)["route"]["visible"] is False


def test_grid_chart_min_n_resets_to_its_own_default_not_the_series_default():
    # The exact historical bug the PRD calls out: switching series -> grid must reset the
    # min_n VALUE (not just visibility) to the grid chart's own low default.
    series = _reset_dict("C9")
    assert series["min_n"]["value"] == 20
    grid = _reset_dict("D14")
    assert grid["min_n"]["visible"] is True
    assert grid["min_n"]["value"] == 3  # D14's own min_n_default, not the series' 20
    grid_b5 = _reset_dict("B5")
    assert grid_b5["min_n"]["value"] == 3


def test_a2_hides_min_n_entirely():
    # A2's min_n_default is None: the slider must not just default oddly, it must be hidden.
    assert _reset_dict("A2")["min_n"]["visible"] is False


# --- render_chart: end-to-end against the CL-2 bundled example table ----------------------


def test_render_c9_route11_produces_a_real_png():
    image_update, message, downloads_update = render_chart(
        _REGISTRY, _get_example_tables, "C9", ["11"], None, 60, 20, 0.6,
        False, 6, 0.25, [], False,
    )
    assert message == ""
    assert image_update["visible"] is True
    from pathlib import Path
    png_path = Path(image_update["value"])
    assert png_path.exists() and png_path.stat().st_size > 0
    assert downloads_update["visible"] is True
    assert len(downloads_update["value"]) == 3  # png, csv, json (no html requested)


def test_render_c9_with_zero_routes_shows_inline_message_not_a_crash():
    image_update, message, downloads_update = render_chart(
        _REGISTRY, _get_example_tables, "C9", [], None, 60, 20, 0.6,
        False, 6, 0.25, [], False,
    )
    assert image_update["visible"] is False
    assert "exactly one route" in message
    assert downloads_update["visible"] is False


def test_render_d15_with_only_one_table_active_shows_validation_message_not_a_crash():
    image_update, message, downloads_update = render_chart(
        _REGISTRY, _get_example_tables, "D15", ["11"], None, 60, 20, 0.6,
        False, 6, 0.25, [], False,
    )
    assert image_update["visible"] is False
    assert "D15 needs at least 3 tables" in message
    assert downloads_update["visible"] is False
