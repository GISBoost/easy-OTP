"""Entry point: launches the chart_lab Gradio app.

    py -m chart_lab.app

No interactive prompts, no hardcoded paths — same convention as transit_charts/cli.py.

Jobs run in spawned child processes (jobs.py, browse.py). A spawned child re-imports this
module, so everything heavy (Gradio, pandas, matplotlib) lives inside `build_demo()`, never at
module level - otherwise every job would pay for building the whole UI first. In a frozen
build the child re-runs this script as __main__, hence freeze_support() before anything else.
"""
from __future__ import annotations

import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()


def build_demo():
    from chart_lab import paths  # noqa: F401 - side effect: wires sys.path before any transit_charts/family_a import

    import gradio as gr

    from chart_lab import data_sources, widgets
    from chart_lab.tabs import pipeline, simple

    # The bundled example is loaded and active by default on every launch - uploading a file
    # (CL-4) is additive, never a silent replacement, unless the user deselects it themselves
    # in the active-tables checkbox group.
    data_sources.set_active_ids([data_sources.register_example_table()])

    with gr.Blocks(title="chart_lab — transit_charts, interactively") as demo:
        with gr.Tabs():
            with gr.Tab("Charts") as charts_tab:
                widgets.build_chart_ui(
                    demo, get_active_tables=data_sources.get_active_tables, tab=charts_tab,
                )
            with gr.Tab("Pipeline"):
                pipeline.build_tab()
            with gr.Tab("Stop headway"):
                simple.build_stop_headway_tab()
            with gr.Tab("Realized (TripUpdates)"):
                simple.build_realized_b_tab()
            with gr.Tab("Diagnose RT"):
                simple.build_diagnose_tab()
    return demo


if __name__ == "__main__":
    build_demo().launch(inbrowser=True)
