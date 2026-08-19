"""Entry point: launches the chart_lab Gradio app.

    py -m chart_lab.app

No interactive prompts, no hardcoded paths — same convention as transit_charts/cli.py.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

from chart_lab import paths  # noqa: F401 - side effect: wires sys.path before any transit_charts/family_a import

import gradio as gr

from chart_lab import data_sources
from transit_charts.registry import ResolvedChartInputs, build_registry

# Renders go to a per-run temp directory, never into the repo or (later, once frozen by
# PyInstaller in CL-6) the installed app's own folder: CL-3 onward re-renders on every
# parameter change, and that must never litter either location with run artifacts.
_OUTPUT_DIR = Path(tempfile.mkdtemp(prefix="chart_lab_"))


def render_example_chart() -> Path:
    """Render C9 (dot-and-whisker delay) for route 11 of the bundled example table.

    Hardcoded on purpose - this milestone only proves the pipeline (table -> registry ->
    render -> PNG) end to end. Chart selection and parameter widgets are CL-3.
    """
    table = data_sources.load_example_table()
    registry = build_registry()
    spec = registry["C9"]

    args = SimpleNamespace(
        route=["11"],
        direction=None,
        min_n=20,
        min_trip_coverage=0.6,
        table=[data_sources.EXAMPLE_TABLE_PATH],
        out_prefix=_OUTPUT_DIR / "c9_route11",
    )
    inputs = ResolvedChartInputs(args=args, table=table, tables=[table], interactive=False)
    result = spec.render(**spec.build_kwargs(inputs))
    return result.png


with gr.Blocks(title="chart_lab — transit_charts, interactively") as demo:
    gr.Markdown(
        "# chart_lab — transit_charts, interactively\n\n"
        "Chart picker, data source selection, and live parameter widgets are coming "
        "in later milestones. Shown below: C9 (dot-and-whisker delay) for route 11 of "
        "the bundled Łódź 2026-07-23 example data."
    )
    gr.Image(value=render_example_chart, label="C9 — route 11")

if __name__ == "__main__":
    demo.launch(inbrowser=True)
