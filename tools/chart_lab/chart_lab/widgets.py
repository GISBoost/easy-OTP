"""Dynamic per-chart parameter widgets, driven entirely by transit_charts's registry.

The registry (`transit_charts/registry.py`, CL-0) is the single source of truth for which
flags apply to which chart. Adding a chart there must make it show up here with zero changes
to this file - that is the whole point of CL-0+CL-3 together (see PRD acceptance criteria).
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import pandas as pd

from chart_lab import data_sources, paths  # noqa: F401 - paths import is a sys.path side effect

import gradio as gr

from chart_lab import manifest_client
from transit_charts import sources
from transit_charts.registry import ChartSpec, ResolvedChartInputs, build_registry

# Shown in the direction dropdown for "let the render function pick" (None on the wire).
_AUTO_DIRECTION = "(auto)"

# One-sentence, plain-language explanation per chart, layered on top of registry.py's short
# `label` (which is more of a title than an explanation). Deliberately local to chart_lab, not
# a new ChartSpec field: a chart added to the registry without an entry here still works fine
# (falls back to just the label, see _chart_description below) - the CL-0/CL-3 "new chart needs
# zero changes elsewhere to appear" guarantee stays intact, this is opt-in polish, not a
# required contract.
CHART_DESCRIPTIONS: dict[str, str] = {
    "C9": "For one route and direction: how late (or early) buses actually run at each stop, "
          "shown as a dot for the typical delay with a whisker for the spread.",
    "C10": "For a set of routes: how the spread of delay (10th-90th percentile) changes hour "
           "by hour through the day.",
    "C11": "For a set of routes: the share of trips running early / on-time / late, stacked "
           "hour by hour.",
    "A2": "For one route and direction: every single trip that day traced as its own line "
          "(position vs time) - good for spotting one unusual run among the rest.",
    "B5": "For one route and direction: how irregular the gaps between buses are, by stop "
          "and hour.",
    "B6": "For a set of routes: how much longer riders actually wait versus what the "
          "schedule promises, hour by hour.",
    "B7": "For one route and direction: the full shape of gaps between buses per hour, "
          "stacked as overlapping ridges.",
    "B8": "For one route and direction: how often two buses bunch up too close together, "
          "by stop and hour.",
    "D14": "For one route and direction: average speed on each segment of the route, by "
           "hour of day.",
    "D17": "For one route and direction: how much slack the schedule has versus how long "
           "the trip actually takes.",
    "D15": "For one route across several recorded days: splits delay into a systematic part "
           "(same every day) and a random, day-to-day part.",
    "E20": "Across at least two cities: compares the tell-tale delay pattern buses show "
           "right after leaving a terminus.",
    "H28": "Network-wide: ranks every route by how regular its headways are, worst to best.",
    "H29": "Network-wide: ranks every route by how much extra waiting time it costs riders.",
    "H30": "Network-wide: how often each route bunches, one row per route through the day.",
    "J39": "Across at least two cities: median time between buses at a stop, overlaid so "
           "cities can be compared directly.",
}


def _chart_description(registry: dict[str, ChartSpec], key: str) -> str:
    spec = registry[key]
    return f"**{key} - {spec.label}.** {CHART_DESCRIPTIONS.get(key, '')}"


# One folder for the whole process lifetime, not a fresh tempfile.mkdtemp() per render: the
# CL-2 "don't litter the repo/install folder" reasoning still holds, but scattering every
# render into its own throwaway directory (the original approach) made "where are my charts"
# unanswerable - 133 folders accumulated from one afternoon of slider-dragging in testing.
# Filenames are timestamped (see render_chart) so renders never collide within this one folder.
OUTPUT_DIR = Path(tempfile.gettempdir()) / "chart_lab_output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


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
    # Whichever route sorts first - not a "best" route, just something so a single-route
    # chart never starts on the empty selection that raises its own "needs exactly one
    # route" validation warning (see demo.load's comment for the concrete bug this caused).
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
    get_active_table_paths: Callable[[], list[Path]] | None = None,
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
    # Microsecond timestamp, not a counter: render_chart has no state of its own to count
    # from, and this only needs to not collide with the previous render in the same folder.
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")

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
        # the output JSON, so it must be a real, existing file - not just a label. Bug fixed
        # here: this used to be hardcoded to the CL-2 bundled example regardless of what was
        # actually active, so an uploaded/catalogue-downloaded table's output JSON always
        # claimed the Łódź example as its source. get_active_table_paths (wired to
        # data_sources.get_active_paths by build_chart_ui) reports the real active files;
        # falls back to the example path for callers (tests) that don't supply one.
        table=(get_active_table_paths() if get_active_table_paths
               else [data_sources.EXAMPLE_TABLE_PATH]),
        out_prefix=OUTPUT_DIR / f"{spec.key.lower()}_{stamp}",
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
    chart_choices = [(f"{key} — {spec.label}", key) for key, spec in registry.items()]
    default_key = "C9"
    default_spec = registry[default_key]

    gr.Markdown(
        "# chart_lab — transit_charts, interactively\n\n"
        "Pick a chart below; only the parameters it actually uses will show up."
    )
    with gr.Accordion("How to use this app (click to expand)", open=False):
        gr.Markdown(
            "1. **Data** — the bundled Łódź example is active by default. Add your own file, "
            "or fetch one from the **Online catalogue** below.\n"
            "2. **Chart** — pick one from the dropdown; its one-sentence description appears "
            "underneath.\n"
            "3. **Parameters** — click route(s)/direction below the chart picker; every other "
            "control updates the chart the moment you change it, no \"generate\" button.\n"
            "4. Some charts (D15, E20, J39) need several tables active at once — a warning "
            "explains what's missing until you add enough.\n"
            "5. **Downloads**, at the bottom, has the PNG plus the CSV/JSON behind it, and a "
            "button to open the folder they're saved to.\n\n"
            "Full write-up: [README on GitHub]"
            "(https://github.com/GISBoost/easy-OTP/tree/main/tools/chart_lab#readme)."
        )

    gr.Markdown("## 1. Data")
    with gr.Row():
        upload = gr.File(
            label="Add your own tidy table (from `transit_charts extract`)",
            file_types=[".csv", ".gz", ".parquet"],
        )
        active_tables_cbg = gr.CheckboxGroup(
            choices=data_sources.loaded_table_choices(),
            value=data_sources.get_active_ids(),
            label="Active tables (used together by every chart below)",
            info="Tick as many as you like — a few charts (D15, E20, J39) need several at once.",
        )
    upload_message_md = gr.Markdown(value="")

    # Fetch is an explicit button, never automatic on app load: a user who only ever uses the
    # bundled example or their own files should never pay a startup network call (or see a
    # startup failure/delay) for a feature they're not touching.
    # City -> month -> day, not one flat "city — date" list: gtfs-dashboard can accumulate
    # months of daily recordings per city, and a single dropdown mixing every city's every day
    # was the exact complaint this redesign answers - narrowing step by step keeps each
    # dropdown short enough to actually scan.
    catalogue_days_by_city: dict[str, list[manifest_client.CityDay]] = {}
    catalogue_by_key: dict[tuple[str, str], manifest_client.CityDay] = {}
    with gr.Accordion("Online catalogue (published city-days, via gtfs-dashboard)", open=False):
        gr.Markdown(
            "Already-recorded days for other cities, published by `gtfs-dashboard`. "
            "Fetch the list, then narrow city → month → day."
        )
        fetch_btn = gr.Button("1. Fetch available cities")
        with gr.Row():
            catalogue_city_dd = gr.Dropdown(choices=[], label="City", interactive=True)
            catalogue_month_dd = gr.Dropdown(choices=[], label="Month", interactive=True)
            catalogue_day_dd = gr.Dropdown(choices=[], label="Day", interactive=True)
        load_btn = gr.Button("2. Download and add to active tables")
    catalogue_message_md = gr.Markdown(value="")

    gr.Markdown("## 2. Chart")
    with gr.Row():
        chart_dd = gr.Dropdown(
            chart_choices, value=default_key, label="Chart",
            info="Pick what you want to see; the controls below adjust to match.",
        )
    chart_description_md = gr.Markdown(value=_chart_description(registry, default_key))

    gr.Markdown("## 3. Parameters")
    with gr.Row():
        route_dd = gr.CheckboxGroup(
            choices=_route_choices(get_active_tables()),
            label="Route(s)", visible=True,
            info="Click to select. Most charts need exactly one; a few allow several.",
        )
        direction_dd = gr.Radio(
            choices=_direction_choices(get_active_tables()), value=_AUTO_DIRECTION,
            label="Direction", visible=default_spec.supports_direction,
            info="(auto) lets the chart pick a sensible default.",
        )
    with gr.Row():
        bucket_slider = gr.Slider(
            5, 240, step=5, value=default_spec.bucket_minutes_default or 60,
            label="Bucket width (minutes)", info="Size of each time bucket.",
            visible=default_spec.bucket_minutes_default is not None,
        )
        min_n_slider = gr.Slider(
            1, 100, step=1, value=default_spec.min_n_default or 20, label="Minimum n",
            info="Hide any bucket/cell backed by fewer than this many observations.",
            visible=default_spec.min_n_default is not None,
        )
        min_trip_coverage_slider = gr.Slider(
            0.0, 1.0, step=0.05, value=default_spec.min_trip_coverage_default or 0.6,
            label="Minimum trip coverage",
            info="Minimum share of the route a trip must cover to count.",
            visible=default_spec.min_trip_coverage_default is not None,
        )
    with gr.Row():
        combine_cb = gr.Checkbox(
            value=False, label="Combine (add pooled all-routes panel)",
            info="Adds one extra panel that pools every selected route together.",
            visible=default_spec.supports_combine,
        )
        annotate_slider = gr.Slider(
            0, 20, step=1, value=default_spec.annotate_default, label="Annotate top N",
            info="Label the top N segments/rows directly on the chart.",
            visible=default_spec.supports_annotate,
        )
        threshold_slider = gr.Slider(
            0.0, 1.0, step=0.05, value=default_spec.threshold_default, label="Bunching threshold",
            info="Headway ratio below which two buses count as \"bunched\".",
            visible=default_spec.supports_threshold,
        )
    with gr.Row():
        exclude_route_dd = gr.CheckboxGroup(
            choices=_route_choices(get_active_tables()),
            label="Exclude route(s)", visible=default_spec.exclude_route_aware,
            info="Remove these from the selection above.",
        )
        html_cb = gr.Checkbox(
            value=False, label="Also write interactive HTML",
            info="Saves a zoomable/hoverable version you can open in a browser.",
            visible=default_spec.interactive_capable,
        )

    gr.Markdown("## 4. Result")
    message_md = gr.Markdown(value="")
    image = gr.Image(label="Chart", visible=False)
    downloads = gr.File(label="Downloads (PNG/CSV/JSON[/HTML])", file_count="multiple", visible=False)
    open_folder_btn = gr.Button(f"Open charts folder ({OUTPUT_DIR})")
    open_folder_message_md = gr.Markdown(value="")

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
        lambda k: (*reset_for_chart(registry, get_active_tables, k), _chart_description(registry, k)),
        inputs=[chart_dd], outputs=[*reset_outputs, chart_description_md],
    ).then(
        lambda *a: render_chart(
            registry, get_active_tables, *a, get_active_table_paths=data_sources.get_active_paths,
        ),
        inputs=param_inputs, outputs=render_outputs,
    )
    # .input(), not .change(): .change() fires on ANY value change, including the
    # programmatic gr.update() calls reset_for_chart makes to these same components when the
    # chart selection changes - .input() fires only on direct user interaction. Wiring these
    # to .change() caused a real, confirmed-live bug: switching charts fired reset_for_chart's
    # own render (via chart_dd's .then()) AND a redundant render from every widget it just
    # reset, all racing each other - whichever finished last won the display regardless of
    # whether it used the post-reset values, so the chart could show stale route/parameter
    # text that didn't match the visible widget state.
    for widget in (
        route_dd, direction_dd, bucket_slider, min_n_slider, min_trip_coverage_slider,
        combine_cb, annotate_slider, threshold_slider, exclude_route_dd, html_cb,
    ):
        widget.input(
            lambda *a: render_chart(
            registry, get_active_tables, *a, get_active_table_paths=data_sources.get_active_paths,
        ),
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
        lambda *a: render_chart(
            registry, get_active_tables, *a, get_active_table_paths=data_sources.get_active_paths,
        ),
        inputs=param_inputs, outputs=render_outputs,
    )

    def _on_active_tables_change(ids):
        data_sources.set_active_ids(ids)

    # .input(), same reasoning as the parameter widgets above: upload/catalogue-load already
    # set this component's value programmatically and drive their own render chain, so a plain
    # .change() here would double-fire and race that chain instead of only reacting when a
    # user actually (de)selects a checkbox themselves.
    active_tables_cbg.input(
        _on_active_tables_change, inputs=[active_tables_cbg], outputs=None,
    ).then(
        lambda r, e: refresh_route_choices(get_active_tables, r, e),
        inputs=[route_dd, exclude_route_dd], outputs=[route_dd, exclude_route_dd],
    ).then(
        lambda *a: render_chart(
            registry, get_active_tables, *a, get_active_table_paths=data_sources.get_active_paths,
        ),
        inputs=param_inputs, outputs=render_outputs,
    )

    def _on_fetch_catalogue():
        try:
            manifest = manifest_client.fetch_manifest()
        except manifest_client.ManifestError as exc:
            return gr.update(choices=[], value=None), f"⚠️ {exc}"
        city_days = manifest_client.list_available_city_days(manifest)
        if not city_days:
            return gr.update(choices=[], value=None), "No published tidy tables found."
        catalogue_days_by_city.clear()
        catalogue_by_key.clear()
        for cd in city_days:
            catalogue_days_by_city.setdefault(cd.display_name, []).append(cd)
            catalogue_by_key[(cd.display_name, cd.date)] = cd
        cities = sorted(catalogue_days_by_city)
        return (
            gr.update(choices=cities, value=None),
            f"{len(cities)} cities, {len(city_days)} city-days available.",
        )

    fetch_btn.click(
        _on_fetch_catalogue, outputs=[catalogue_city_dd, catalogue_message_md],
    ).then(
        lambda: (gr.update(choices=[], value=None), gr.update(choices=[], value=None)),
        outputs=[catalogue_month_dd, catalogue_day_dd],
    )

    def _on_catalogue_city_selected(city):
        months = sorted({cd.date[:7] for cd in catalogue_days_by_city.get(city, [])})
        return gr.update(choices=months, value=None), gr.update(choices=[], value=None)

    catalogue_city_dd.input(
        _on_catalogue_city_selected, inputs=[catalogue_city_dd],
        outputs=[catalogue_month_dd, catalogue_day_dd],
    )

    def _on_catalogue_month_selected(city, month):
        days = sorted(
            cd.date for cd in catalogue_days_by_city.get(city, []) if cd.date[:7] == month
        )
        return gr.update(choices=days, value=None)

    catalogue_month_dd.input(
        _on_catalogue_month_selected, inputs=[catalogue_city_dd, catalogue_month_dd],
        outputs=[catalogue_day_dd],
    )

    def _on_load_selected(city, date):
        if not city or not date:
            return gr.update(), "Pick a city, month and day first."
        city_day = catalogue_by_key.get((city, date))
        if city_day is None:
            return gr.update(), "That selection is stale - fetch the catalogue again."
        try:
            path = manifest_client.download_tidy_table(city_day.tidy_table_url)
            table_id = data_sources.register_user_table(path)
        except (manifest_client.ManifestError, sources.InputError) as exc:
            return gr.update(), f"⚠️ {exc}"
        active = sorted(set(data_sources.get_active_ids()) | {table_id})
        data_sources.set_active_ids(active)
        choices = data_sources.loaded_table_choices()
        return gr.update(choices=choices, value=active), f"Added {city_day.label}."

    load_btn.click(
        _on_load_selected, inputs=[catalogue_city_dd, catalogue_day_dd],
        outputs=[active_tables_cbg, catalogue_message_md],
    ).then(
        lambda r, e: refresh_route_choices(get_active_tables, r, e),
        inputs=[route_dd, exclude_route_dd], outputs=[route_dd, exclude_route_dd],
    ).then(
        lambda *a: render_chart(
            registry, get_active_tables, *a, get_active_table_paths=data_sources.get_active_paths,
        ),
        inputs=param_inputs, outputs=render_outputs,
    )

    def _open_output_folder():
        # os.startfile: Windows-only, matches this app's v0.1 scope (PRD §1) - opens Explorer
        # at OUTPUT_DIR on the machine running the app. Only makes sense for a local desktop
        # app talking to its own filesystem, never for a hosted multi-user service.
        try:
            os.startfile(OUTPUT_DIR)
            return ""
        except OSError as exc:
            return f"⚠️ Could not open {OUTPUT_DIR}: {exc}"

    open_folder_btn.click(_open_output_folder, outputs=[open_folder_message_md])

    # Same reset-then-render chain as chart_dd.change(), not a direct render_chart call: the
    # parameter widgets are constructed with no explicit default `value=` (route_dd in
    # particular defaults to []), so calling render_chart directly on load reproduced the
    # exact "0 routes selected" validation warning C9 raises for that empty default - a
    # regression from CL-2's "zero user interaction shows a real rendered chart" guarantee
    # that CL-3 was supposed to preserve. Found by milestone-reviewer via static reading, not
    # caught by the live browser testing that only exercised user-driven chart switches.
    demo.load(
        lambda: reset_for_chart(registry, get_active_tables, default_key),
        outputs=reset_outputs,
    ).then(
        lambda *a: render_chart(
            registry, get_active_tables, *a, get_active_table_paths=data_sources.get_active_paths,
        ),
        inputs=param_inputs, outputs=render_outputs,
    )

    # GPL distribution obligation (PRD §2), not optional polish: the shipped .exe embeds this
    # code, so the license and where to get the source must be visible from inside the app
    # itself, not just in a README a user who only ever ran the .exe will never open.
    gr.Markdown(
        "---\nchart_lab is licensed GPL-3.0-or-later. Source: "
        "[github.com/GISBoost/easy-OTP/tree/main/tools/chart_lab]"
        "(https://github.com/GISBoost/easy-OTP/tree/main/tools/chart_lab)"
    )
