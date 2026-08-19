"""Dynamic per-chart parameter widgets, driven entirely by transit_charts's registry.

The registry (`transit_charts/registry.py`, CL-0) is the single source of truth for which
flags apply to which chart. Adding a chart there must make it show up here with zero changes
to this file - that is the whole point of CL-0+CL-3 together (see PRD acceptance criteria).
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import pandas as pd

from chart_lab import data_sources, paths  # noqa: F401 - paths import is a sys.path side effect

import gradio as gr

from transit_charts import sources
from transit_charts.registry import ChartSpec, ResolvedChartInputs, build_registry

# Shown in the direction dropdown for "let the render function pick" (None on the wire).
_AUTO_DIRECTION = "(auto)"


def _route_choices(tables: list[pd.DataFrame]) -> list[str]:
    """Every route_short_name/route_group present across the active tables, sorted.

    Same union `registry._resolve_route_filter` uses, so the dropdown never offers a name
    the render call would then reject.
    """
    names: set[str] = set()
    for table in tables:
        if table.empty:
            continue
        names |= set(table.route_short_name.dropna()) | set(table.route_group.dropna())
    return sorted(names)


def _direction_choices(tables: list[pd.DataFrame]) -> list[str]:
    values: set[str] = set()
    for table in tables:
        if table.empty or "direction_id" not in table.columns:
            continue
        values |= set(table.direction_id.dropna().astype(str))
    return [_AUTO_DIRECTION, *sorted(values)]


def validate_active_tables(spec: ChartSpec, tables: list[pd.DataFrame]) -> str | None:
    """Whether `tables` satisfies `spec`'s multi-day/multi-city requirement.

    Returns a human-readable problem description, or None when the selection is fine. Split
    out so CL-4/CL-5 (which add real multi-table selection) can call it unchanged, and so it
    can be exercised directly by a unit test now - CL-3 only ever has one bundled table
    active, so this path can't be demonstrated live in the UI yet.
    """
    if not spec.multi_day_by_design:
        return None
    if spec.route_mode == "none":
        # E20/J39: a cross-city comparison needs more than one city to compare.
        from transit_charts.render import crosscity

        cities = crosscity.group_by_city(tables)
        if len(cities) < 2:
            return (
                f"{spec.key} needs at least 2 distinct cities across active tables; "
                f"{len(cities)} present"
            )
        return None
    # D15: needs several separate days pooled, not one day repeated.
    if len(tables) < 3:
        return f"{spec.key} needs at least 3 tables from different days; {len(tables)} active"
    return None


def refresh_route_choices(
    get_active_tables: Callable[[], list[pd.DataFrame]],
    current_routes: list[str] | None, current_exclude: list[str] | None,
) -> tuple:
    """Recompute route choices from whichever tables are active right now, keeping any
    current selection that's still valid and dropping any that isn't (a route only present
    in a table the user just deselected). Wired to the data-source controls (upload, active
    table checkboxes), not the chart picker - route mode/visibility stays whatever the
    current chart already needs.
    """
    choices = _route_choices(get_active_tables())
    kept_routes = [r for r in (current_routes or []) if r in choices]
    kept_exclude = [r for r in (current_exclude or []) if r in choices]
    return gr.update(choices=choices, value=kept_routes), gr.update(choices=choices, value=kept_exclude)


def reset_for_chart(
    registry: dict[str, ChartSpec], get_active_tables: Callable[[], list[pd.DataFrame]],
    chart_key: str,
) -> tuple:
    """Chart-selection handler: fix widget visibility AND reset values to that chart's own
    defaults. Resetting only visibility would leave a series chart's min_n=20 in place when
    switching to a grid chart (min_n_default=3) - the exact historical bug the PRD calls out
    for D14 (a bad threshold hid 97% of its cells).
    """
    spec = registry[chart_key]
    tables = get_active_tables()
    route_choices = _route_choices(tables)
    direction_choices = _direction_choices(tables)
    default_routes = route_choices[:1] if spec.route_mode == "single" else []
    return (
        gr.update(choices=route_choices, value=default_routes, visible=spec.route_mode != "none"),
        gr.update(choices=direction_choices, value=_AUTO_DIRECTION, visible=spec.supports_direction),
        gr.update(value=spec.bucket_minutes_default or 60, visible=spec.bucket_minutes_default is not None),
        gr.update(value=spec.min_n_default or 20, visible=spec.min_n_default is not None),
        gr.update(value=spec.min_trip_coverage_default or 0.6, visible=spec.min_trip_coverage_default is not None),
        gr.update(value=False, visible=spec.supports_combine),
        gr.update(value=spec.annotate_default, visible=spec.supports_annotate),
        gr.update(value=spec.threshold_default, visible=spec.supports_threshold),
        gr.update(choices=route_choices, value=[], visible=spec.exclude_route_aware),
        gr.update(value=False, visible=spec.interactive_capable),
    )


def render_chart(
    registry: dict[str, ChartSpec], get_active_tables: Callable[[], list[pd.DataFrame]],
    chart_key, routes, direction, bucket_minutes, min_n, min_trip_coverage,
    combine, annotate, threshold, exclude_route, html,
) -> tuple:
    """Build the args for the selected chart and call it through the registry - the same
    `spec.render(**spec.build_kwargs(inputs))` call `cli.py`'s `_cmd_chart` makes, generalized
    to whichever chart/parameters are current instead of CL-2's one hardcoded case.
    """
    spec = registry[chart_key]
    tables = get_active_tables()

    problem = validate_active_tables(spec, tables)
    if problem:
        return gr.update(visible=False), f"⚠️ {problem}", gr.update(visible=False)

    routes = routes or []
    if spec.route_mode == "single" and len(routes) != 1:
        return (
            gr.update(visible=False),
            f"⚠️ {spec.key} needs exactly one route selected; {len(routes)} selected",
            gr.update(visible=False),
        )

    table = tables[0] if len(tables) == 1 else pd.concat(tables, ignore_index=True)
    out_dir = Path(tempfile.mkdtemp(prefix="chart_lab_"))

    args = SimpleNamespace(
        route=list(routes),
        exclude_route=list(exclude_route or []),
        direction=None if direction in (None, _AUTO_DIRECTION) else direction,
        bucket_minutes=bucket_minutes if spec.bucket_minutes_default is not None else None,
        min_n=int(min_n),
        # Gradio always sends a concrete slider value - there is no "flag absent" to detect,
        # so every GUI-driven call is explicit by construction (PRD §3 gotcha). reset_for_chart
        # already seeded the right starting value per chart.
        min_n_explicit=True,
        min_trip_coverage=float(min_trip_coverage),
        combine=bool(combine),
        annotate=int(annotate),
        threshold=float(threshold),
        # style.chart_params fingerprints this path (reads it to hash it) for provenance in
        # the output JSON, so it must be a real, existing file - not just a label. CL-3 has
        # exactly one possible source (the CL-2 bundled example); CL-4/CL-5 will need to plumb
        # the real per-table source path through once uploads/downloads exist.
        table=[data_sources.EXAMPLE_TABLE_PATH],
        out_prefix=out_dir / spec.key.lower(),
    )
    interactive = bool(html) and spec.interactive_capable
    inputs = ResolvedChartInputs(args=args, table=table, tables=tables, interactive=interactive)

    try:
        result = spec.render(**spec.build_kwargs(inputs))
    except (sources.InputError, ValueError) as exc:
        return gr.update(visible=False), f"⚠️ {exc}", gr.update(visible=False)

    files = [str(result.png), str(result.csv), str(result.json)]
    if result.html:
        files.append(str(result.html))
    return (
        gr.update(value=str(result.png), visible=True),
        "",
        gr.update(value=files, visible=True),
    )


def build_chart_ui(demo: gr.Blocks, get_active_tables: Callable[[], list[pd.DataFrame]]) -> None:
    """Render the chart picker + parameter widgets + output area inside `demo`.

    `get_active_tables` is called fresh on every render (and on data-source refresh), never
    cached here - CL-4/CL-5 will swap the CL-2 example-only lambda for one backed by real
    upload/online state without touching this function.
    """
    registry = build_registry()
    chart_choices = [(spec.label, key) for key, spec in registry.items()]
    default_key = "C9"
    default_spec = registry[default_key]

    gr.Markdown(
        "# chart_lab — transit_charts, interactively\n\n"
        "Pick a chart; only the parameters it actually uses are shown."
    )

    gr.Markdown("## Data")
    with gr.Row():
        upload = gr.File(
            label="Add your own tidy table (from `transit_charts extract`)",
            file_types=[".csv", ".gz", ".parquet"],
        )
        active_tables_cbg = gr.CheckboxGroup(
            choices=data_sources.loaded_table_choices(),
            value=data_sources.get_active_ids(),
            label="Active tables (used together by every chart below)",
        )
    upload_message_md = gr.Markdown(value="")

    with gr.Row():
        chart_dd = gr.Dropdown(chart_choices, value=default_key, label="Chart")
    with gr.Row():
        route_dd = gr.Dropdown(
            choices=_route_choices(get_active_tables()), multiselect=True,
            label="Route(s) (single-route charts need exactly one)", visible=True,
        )
        direction_dd = gr.Dropdown(
            choices=_direction_choices(get_active_tables()), value=_AUTO_DIRECTION,
            label="Direction", visible=default_spec.supports_direction,
        )
    with gr.Row():
        bucket_slider = gr.Slider(
            5, 240, step=5, value=default_spec.bucket_minutes_default or 60,
            label="Bucket width (minutes)",
            visible=default_spec.bucket_minutes_default is not None,
        )
        min_n_slider = gr.Slider(
            1, 100, step=1, value=default_spec.min_n_default or 20, label="Minimum n",
            visible=default_spec.min_n_default is not None,
        )
        min_trip_coverage_slider = gr.Slider(
            0.0, 1.0, step=0.05, value=default_spec.min_trip_coverage_default or 0.6,
            label="Minimum trip coverage",
            visible=default_spec.min_trip_coverage_default is not None,
        )
    with gr.Row():
        combine_cb = gr.Checkbox(
            value=False, label="Combine (add pooled all-routes panel)",
            visible=default_spec.supports_combine,
        )
        annotate_slider = gr.Slider(
            0, 20, step=1, value=default_spec.annotate_default, label="Annotate top N",
            visible=default_spec.supports_annotate,
        )
        threshold_slider = gr.Slider(
            0.0, 1.0, step=0.05, value=default_spec.threshold_default, label="Bunching threshold",
            visible=default_spec.supports_threshold,
        )
    with gr.Row():
        exclude_route_dd = gr.Dropdown(
            choices=_route_choices(get_active_tables()), multiselect=True,
            label="Exclude route(s)", visible=default_spec.exclude_route_aware,
        )
        html_cb = gr.Checkbox(
            value=False, label="Also write interactive HTML",
            visible=default_spec.interactive_capable,
        )

    message_md = gr.Markdown(value="")
    image = gr.Image(label="Chart", visible=False)
    downloads = gr.File(label="Downloads (PNG/CSV/JSON[/HTML])", file_count="multiple", visible=False)

    param_inputs = [
        chart_dd, route_dd, direction_dd, bucket_slider, min_n_slider,
        min_trip_coverage_slider, combine_cb, annotate_slider, threshold_slider,
        exclude_route_dd, html_cb,
    ]
    reset_outputs = [
        route_dd, direction_dd, bucket_slider, min_n_slider, min_trip_coverage_slider,
        combine_cb, annotate_slider, threshold_slider, exclude_route_dd, html_cb,
    ]
    render_outputs = [image, message_md, downloads]

    chart_dd.change(
        lambda k: reset_for_chart(registry, get_active_tables, k),
        inputs=[chart_dd], outputs=reset_outputs,
    ).then(
        lambda *a: render_chart(registry, get_active_tables, *a),
        inputs=param_inputs, outputs=render_outputs,
    )
    for widget in (
        route_dd, direction_dd, bucket_slider, min_n_slider, min_trip_coverage_slider,
        combine_cb, annotate_slider, threshold_slider, exclude_route_dd, html_cb,
    ):
        widget.change(
            lambda *a: render_chart(registry, get_active_tables, *a),
            inputs=param_inputs, outputs=render_outputs,
        )

    def _on_upload(file_path):
        if not file_path:
            return gr.update(), gr.update(), ""
        try:
            table_id = data_sources.register_user_table(Path(file_path))
        except sources.InputError as exc:
            return gr.update(), gr.update(), f"⚠️ {exc}"
        # Additive by default (PRD/CL-4 goal): the newly-uploaded table joins whatever was
        # already active, the bundled example included, rather than replacing it.
        active = sorted(set(data_sources.get_active_ids()) | {table_id})
        data_sources.set_active_ids(active)
        choices = data_sources.loaded_table_choices()
        return gr.update(choices=choices, value=active), gr.update(value=None), ""

    upload.upload(
        _on_upload, inputs=[upload], outputs=[active_tables_cbg, upload, upload_message_md],
    ).then(
        lambda r, e: refresh_route_choices(get_active_tables, r, e),
        inputs=[route_dd, exclude_route_dd], outputs=[route_dd, exclude_route_dd],
    ).then(
        lambda *a: render_chart(registry, get_active_tables, *a),
        inputs=param_inputs, outputs=render_outputs,
    )

    def _on_active_tables_change(ids):
        data_sources.set_active_ids(ids)

    active_tables_cbg.change(
        _on_active_tables_change, inputs=[active_tables_cbg], outputs=None,
    ).then(
        lambda r, e: refresh_route_choices(get_active_tables, r, e),
        inputs=[route_dd, exclude_route_dd], outputs=[route_dd, exclude_route_dd],
    ).then(
        lambda *a: render_chart(registry, get_active_tables, *a),
        inputs=param_inputs, outputs=render_outputs,
    )

    demo.load(
        lambda *a: render_chart(registry, get_active_tables, *a),
        inputs=param_inputs, outputs=render_outputs,
    )
