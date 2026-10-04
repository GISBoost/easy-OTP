"""Pipeline tab: family_a match -> family_a build -> transit_charts extract.

Shared inputs (recording dirs, static GTFS, city, work folder) feed all three steps; each
step's output path is derived from the work folder and chained into the next step, so nothing
is retyped. Every other flag comes from the CLI parsers via cli_form. After `extract` the tidy
table is registered as an active table for the Charts tab.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from chart_lab import paths  # noqa: F401 - side effect: sys.path wiring
from chart_lab import cli_form, data_sources, jobs

STEP_ORDER = ("match", "build", "extract")

# step -> (tool, subcommand, dests the tab fills in itself and therefore hides from the form)
STEPS: dict[str, tuple[str, str, frozenset[str]]] = {
    "match": ("family_a", "match", frozenset({"positions_dir", "static", "out"})),
    "build": ("family_a", "build", frozenset({"matched", "static", "out_prefix"})),
    "extract": ("transit_charts", "extract", frozenset({"matched", "static", "city", "out"})),
}
_OVERLAY = {"extract": {"route": {"advanced": False}}}


def _parser(tool: str):
    if tool == "family_a":
        from family_a import cli
    else:
        from transit_charts import cli
    return cli.build_parser()


def step_fields(step: str) -> list[cli_form.Field]:
    tool, sub, _ = STEPS[step]
    return cli_form.describe(_parser(tool), sub, _OVERLAY.get(step))


def visible_fields(step: str) -> list[cli_form.Field]:
    return [f for f in step_fields(step) if f.dest not in STEPS[step][2]]


def work_dir(city: str, work: str) -> Path:
    if work.strip():
        return Path(work.strip())
    slug = re.sub(r"[^\w-]+", "_", city.strip()) or "city"
    return Path.home() / "Documents" / "chart_lab" / slug


def outputs(city: str, work: str) -> dict[str, Path]:
    d = work_dir(city, work)
    slug = re.sub(r"[^\w-]+", "_", city.strip())
    return {"matched": d / "matched.csv", "prefix": d / "realized",
            "tidy": d / f"{slug}_tidy.csv.gz"}


def plan(steps: list[str], city: str, positions: str, static: str, work: str,
         adv: dict[str, dict[str, Any]]) -> list[tuple[str, str, list[str]]]:
    """[(step, tool, argv)] for `steps`, or ValueError naming the missing shared input."""
    need = {"city": city, "static GTFS": static}
    if "match" in steps:
        need["recording folder(s)"] = positions
    missing = [k for k, v in need.items() if not (v or "").strip()]
    if missing:
        raise ValueError("Missing: " + ", ".join(missing))
    out = outputs(city, work)
    managed = {
        "match": {"positions_dir": positions, "static": static, "out": str(out["matched"])},
        "build": {"matched": str(out["matched"]), "static": static, "out_prefix": str(out["prefix"])},
        "extract": {"matched": str(out["matched"]), "static": static, "city": city.strip(),
                    "out": str(out["tidy"])},
    }
    result = []
    for step in steps:
        tool, sub, _ = STEPS[step]
        values = {**adv.get(step, {}), **managed[step]}
        result.append((step, tool, cli_form.to_argv(step_fields(step), values, sub)))
    return result


def register_tidy(city: str, work: str) -> str:
    """Add the extract output to the active tables; returns a status line."""
    tidy = outputs(city, work)["tidy"]
    table_id = data_sources.register_user_table(tidy)
    data_sources.set_active_ids(sorted(set(data_sources.get_active_ids()) | {table_id}))
    return f"Tidy table added to the active tables (open the Charts tab): `{tidy}`"


def run(steps, city, positions, static, work, adv):
    """Generator of (log, status_markdown) for the UI. Stops at the first failing step."""
    try:
        todo = plan(list(steps), city, positions, static, work, adv)
        d = work_dir(city, work)
        d.mkdir(parents=True, exist_ok=True)
    except (ValueError, OSError) as exc:
        yield "", f"⚠️ {exc}"
        return
    if "match" not in steps and not outputs(city, work)["matched"].exists():
        yield "", f"⚠️ `{outputs(city, work)['matched']}` not found - run **match** first."
        return
    log_all, warns = "", []
    for i, (step, tool, argv) in enumerate(todo):
        if i and jobs.cancel_requested():
            yield log_all, _status(step, -1, warns)
            return
        header = f"=== {step} ===\n"
        try:
            for log, code in jobs.stream(tool, argv, reset_cancel=(i == 0)):
                yield log_all + header + log, f"⏳ {step}…"
        except jobs.JobBusy:
            yield log_all, "⚠️ Another job is already running."
            return
        log_all += header + log + "\n"
        warns += [f"**{step}:** {w}" for w in jobs.warnings_in(log)]
        if code != 0:
            yield log_all, _status(step, code, warns)
            return
    extra = ""
    if "extract" in steps:
        try:
            extra = register_tidy(city, work)
        except Exception as exc:  # sources.InputError or an unreadable file
            extra = f"⚠️ Could not load the tidy table: {exc}"
    yield log_all, _status(steps[-1], 0, warns, extra)


def _status(step: str, code: int, warns: list[str], extra: str = "") -> str:
    icon = ("⚠️" if warns else "✅") if code == 0 else ("⛔" if code < 0 else "❌")
    lines = [f"{icon} **{step}** {jobs.describe_exit(code)}"
             + (" with quality warnings" if warns and code == 0 else "")]
    if warns:
        lines.append("\n⚠️ **Quality warnings - read before trusting the output:**\n")
        lines += [f"> {w}" for w in warns]
    if extra:
        lines.append("\n" + extra)
    return "\n".join(lines)


def build_tab() -> None:
    """Render the tab contents inside the current gr.Tab."""
    import gradio as gr

    from chart_lab import browse

    gr.Markdown(
        "Reconstruct a realized GTFS from a recording: **match** the positions onto the static "
        "GTFS, **build** the P50/P85 feeds, **extract** the tidy table the charts read. Outputs "
        "go to the work folder and each step feeds the next."
    )
    with gr.Row():
        city = gr.Textbox(label="City *", info="Label carried into the tidy table, e.g. lodz")
        work = gr.Textbox(label="Work folder",
                          info="Blank = ~/Documents/chart_lab/<city>/. Outputs are written here.")
    positions = gr.Textbox(label="Recording folder(s) *", lines=3,
                           info="One folder with snapshot_*.pb per line (several = multi-day merge)")
    with gr.Row():
        add_dir = gr.Button("Add recording folder…", size="sm")
        static = gr.Textbox(label="Static GTFS .zip *", scale=3)
        pick_static = gr.Button("Browse…", size="sm")
    add_dir.click(lambda cur: (cur.rstrip() + "\n" if cur.strip() else "") + browse.pick("dir"),
                  inputs=[positions], outputs=[positions])
    pick_static.click(lambda: browse.pick("file"), outputs=[static])

    shared = [city, positions, static, work]
    step_fields_ui: dict[str, list[cli_form.Field]] = {}
    step_comps: dict[str, list] = {}
    for step in STEP_ORDER:
        with gr.Accordion(f"{STEP_ORDER.index(step) + 1}. {step} - options", open=(step == "extract")):
            comps, ordered = cli_form.build_widgets(visible_fields(step))
        step_comps[step], step_fields_ui[step] = comps, ordered
    flat = [c for s in STEP_ORDER for c in step_comps[s]]

    def _adv(values: tuple) -> dict[str, dict[str, Any]]:
        out, i = {}, 0
        for s in STEP_ORDER:
            n = len(step_comps[s])
            out[s] = {f.dest: v for f, v in zip(step_fields_ui[s], values[i:i + n])}
            i += n
        return out

    with gr.Row():
        btns = {s: gr.Button(f"Run {s}") for s in STEP_ORDER}
        run_all = gr.Button("Run all (match → build → extract)", variant="primary")
        stop = gr.Button("Cancel", variant="stop")
    status = gr.Markdown()
    log = gr.Textbox(label="Log", lines=18, max_lines=18, autoscroll=True, interactive=False)

    def _runner(steps):
        def handler(city_v, positions_v, static_v, work_v, *vals):
            yield from run(steps, city_v, positions_v, static_v, work_v, _adv(vals))
        return handler

    for s, b in btns.items():
        b.click(_runner([s]), inputs=shared + flat, outputs=[log, status])
    run_all.click(_runner(list(STEP_ORDER)), inputs=shared + flat, outputs=[log, status])
    stop.click(lambda: jobs.cancel() and None)
