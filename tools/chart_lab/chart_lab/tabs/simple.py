"""Generic one-command tab (form from fields -> run -> log + result) and the three small tabs
built on it: Stop headway, Realized (TripUpdates, family_b) and Diagnose (rt_diagnose).
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from chart_lab import paths  # noqa: F401 - side effect: sys.path wiring
from chart_lab import cli_form, jobs

# result_fn(values, code, log) -> (markdown, image path or None)
ResultFn = Callable[[dict, int, str], "tuple[str, str | None]"]


def run_values(tool: str, fields: list[cli_form.Field], values: dict, sub: str | None):
    """Generator of (log, exit code or None) for one command built from form values."""
    yield from jobs.stream(tool, cli_form.to_argv(fields, values, sub))


def build_tab(tool: str, fields: list[cli_form.Field], sub: str | None, intro: str,
              result_fn: ResultFn | None = None) -> None:
    import gradio as gr

    from chart_lab import browse

    gr.Markdown(intro)
    comps, ordered = cli_form.build_widgets(fields)
    by_dest = dict(zip((f.dest for f in ordered), comps))
    with gr.Row():
        for f in ordered:
            if f.path:
                b = gr.Button(f"Browse {f.flag or f.dest}…", size="sm")
                b.click(lambda k=f.path: browse.pick(k), outputs=[by_dest[f.dest]])
    with gr.Row():
        run_btn = gr.Button("Run", variant="primary")
        stop_btn = gr.Button("Cancel", variant="stop")
    status = gr.Markdown()
    image = gr.Image(label="Result", visible=False, interactive=False)
    log = gr.Textbox(label="Log", lines=14, max_lines=14, autoscroll=True, interactive=False)

    def handler(*vals):
        values = dict(zip((f.dest for f in ordered), vals))
        code, text = None, ""
        try:
            for text, code in run_values(tool, fields, values, sub):
                yield text, f"⏳ {jobs.describe_exit(code)}…", gr.update(visible=False)
        except jobs.JobBusy:
            yield "", "⚠️ Another job is already running.", gr.update(visible=False)
            return
        warns = "".join(f"\n> ⚠️ {w}" for w in jobs.warnings_in(text))
        icon = ("⚠️" if warns else "✅") if code == 0 else ("⛔" if code < 0 else "❌")
        md, img = (result_fn(values, code, text) if result_fn and code == 0 else ("", None))
        yield text, f"{icon} {jobs.describe_exit(code)}{warns}\n\n{md}", gr.update(value=img, visible=bool(img))

    run_btn.click(handler, inputs=comps, outputs=[log, status, image])
    stop_btn.click(lambda: jobs.cancel() and None)


# --- Stop headway --------------------------------------------------------------------------

def stop_headway_fields() -> list[cli_form.Field]:
    from transit_charts import cli

    return cli_form.describe(cli.build_parser(), "stop-headway", {
        "matched": {"path": "file"}, "static": {"path": "file"}, "out_prefix": {"path": "save"},
    })


def _stop_headway_result(values: dict, code: int, log: str):
    prefix = str(values.get("out_prefix", ""))
    png = Path(prefix + "_H31.png")
    return (f"Map CSV: `{prefix}_stops.csv`", str(png) if png.exists() else None)


# --- family_b (TripUpdates -> realized GTFS) ------------------------------------------------

def realized_b_fields() -> list[cli_form.Field]:
    import build_realized

    return cli_form.describe(build_realized.build_parser(), None, {
        "snapshots": {"path": "dir"}, "static": {"path": "file"}, "out_prefix": {"path": "save"},
    })


def _realized_b_result(values: dict, code: int, log: str):
    p = str(values.get("out_prefix", ""))
    # build_realized exits 0 even when its own checks print PROBLEM, so look at the log.
    note = " **A PROBLEM was reported - check the [p50]/[p85] lines in the log.**" if "PROBLEM" in log else ""
    return f"Expected outputs: `{p}_p50.zip`, `{p}_p85.zip`.{note}", None


# --- rt_diagnose ----------------------------------------------------------------------------

def diagnose_fields() -> list[cli_form.Field]:
    return [
        cli_form.Field("pb", None, "text", None, None, "Live GTFS-RT TripUpdates .pb file", True,
                       path="file"),
        cli_form.Field("static_zip", None, "text", None, None, "Static GTFS .zip to compare", True,
                       path="file"),
    ]


def _diagnose_result(values: dict, code: int, log: str):
    verdict = next((ln.strip() for ln in log.splitlines() if ln.startswith("VERDICT")), "")
    return (f"**{verdict}**" if verdict else ""), None


def build_stop_headway_tab() -> None:
    build_tab("transit_charts", stop_headway_fields(), "stop-headway",
              "Pooled headway per stop for the hex map (`*_stops.csv`) and the H31 city-wide chart. "
              "Always uses the whole feed.", _stop_headway_result)


def build_realized_b_tab() -> None:
    try:
        fields = realized_b_fields()
    except ImportError as exc:  # e.g. gtfs-realtime-bindings missing: don't take the whole app down
        import gradio as gr

        gr.Markdown(f"⚠️ This tab is unavailable: {exc}")
        return
    build_tab("family_b", fields, None,
              "Realized P50/P85 GTFS from archived GTFS-RT **TripUpdates** snapshots "
              "(e.g. ŁKA, `polish_trains_updates_<day>.pb[.gz]`).", _realized_b_result)


def build_diagnose_tab() -> None:
    build_tab("rt_diagnose", diagnose_fields(), None,
              "Does this static GTFS share `trip_id`s with a live GTFS-RT feed? Gives a verdict: "
              "EXACT-MATCH / FUZZY / NEITHER.", _diagnose_result)
