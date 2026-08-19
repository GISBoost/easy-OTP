"""Entry point: launches the chart_lab Gradio app.

    py -m chart_lab.app

No interactive prompts, no hardcoded paths — same convention as transit_charts/cli.py.
"""
from __future__ import annotations

from chart_lab import paths  # noqa: F401 - side effect: wires sys.path before any transit_charts/family_a import

import gradio as gr

from chart_lab import data_sources, widgets

# CL-3: still the CL-2 bundled example only. CL-4/CL-5 replace this lambda with something
# backed by real upload/online UI state; widgets.build_chart_ui only depends on the callable
# interface, so that swap is mechanical.
_get_active_tables = lambda: [data_sources.load_example_table()]  # noqa: E731

with gr.Blocks(title="chart_lab — transit_charts, interactively") as demo:
    widgets.build_chart_ui(demo, get_active_tables=_get_active_tables)

if __name__ == "__main__":
    demo.launch(inbrowser=True)
