"""Entry point: launches the chart_lab Gradio app.

    py -m chart_lab.app

No interactive prompts, no hardcoded paths — same convention as transit_charts/cli.py.
"""
from __future__ import annotations

from chart_lab import paths  # noqa: F401 - side effect: wires sys.path before any transit_charts/family_a import

import gradio as gr

from chart_lab import data_sources, widgets

# The bundled example is loaded and active by default on every launch - uploading a file
# (CL-4) is additive, never a silent replacement, unless the user deselects it themselves in
# the active-tables checkbox group.
_example_id = data_sources.register_example_table()
data_sources.set_active_ids([_example_id])

with gr.Blocks(title="chart_lab — transit_charts, interactively") as demo:
    widgets.build_chart_ui(demo, get_active_tables=data_sources.get_active_tables)

if __name__ == "__main__":
    demo.launch(inbrowser=True)
