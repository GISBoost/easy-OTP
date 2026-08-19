"""Entry point: launches the chart_lab Gradio app.

    py -m chart_lab.app

No interactive prompts, no hardcoded paths — same convention as transit_charts/cli.py.
"""
from __future__ import annotations

from chart_lab import paths  # noqa: F401 - side effect: wires sys.path before any transit_charts/family_a import

import gradio as gr

with gr.Blocks(title="chart_lab — transit_charts, interactively") as demo:
    gr.Markdown(
        "# chart_lab — transit_charts, interactively\n\n"
        "Chart picker, data source selection, and live parameter widgets are coming "
        "in later milestones. This is the empty shell (CL-1)."
    )

if __name__ == "__main__":
    demo.launch(inbrowser=True)
