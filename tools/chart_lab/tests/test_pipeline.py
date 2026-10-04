"""Unit tests for chart_lab.tabs.pipeline (GL-3): planning/chaining, no Gradio, no real data.

    cd tools/chart_lab
    py -m pytest tests/test_pipeline.py -q
"""
from __future__ import annotations

import pytest

from chart_lab import paths  # noqa: F401 - side effect: sys.path wiring
from chart_lab import jobs
from chart_lab.tabs import pipeline
from family_a import cli as fa_cli
from transit_charts import cli as tc_cli


def _plan(tmp_path, steps=("match", "build", "extract"), adv=None):
    return pipeline.plan(list(steps), "Łódź 1", "d1\nd2", "g.zip", str(tmp_path), adv or {})


def test_chain_outputs_feed_next_step_and_all_argv_parse(tmp_path):
    p = {s: (t, a) for s, t, a in _plan(tmp_path)}
    m = fa_cli.build_parser().parse_args(p["match"][1])
    b = fa_cli.build_parser().parse_args(p["build"][1])
    e = tc_cli.build_parser().parse_args(p["extract"][1])
    assert m.positions_dir == ["d1", "d2"] and m.static == "g.zip"
    assert b.matched == m.out and str(e.matched) == m.out        # match output -> build & extract input
    assert b.static == str(e.static) == "g.zip"
    assert str(e.out).endswith("_tidy.csv.gz") and e.city == "Łódź 1"


def test_advanced_values_pass_through_and_defaults_are_omitted(tmp_path):
    adv = {"match": {"max_perpendicular_dist_m": 150.0, "fail_on_low_yield": True},
           "extract": {"route": "10*\n55"}}
    p = {s: a for s, _, a in _plan(tmp_path, adv=adv)}
    assert fa_cli.build_parser().parse_args(p["match"]).max_perpendicular_dist_m == 150.0
    assert "--max-reject-share" not in p["match"]
    assert tc_cli.build_parser().parse_args(p["extract"]).route == ["10*", "55"]


def test_missing_shared_inputs_named(tmp_path):
    with pytest.raises(ValueError, match="static GTFS"):
        pipeline.plan(["build"], "x", "", "", str(tmp_path), {})
    # recording folder only needed when match runs
    pipeline.plan(["build"], "x", "", "g.zip", str(tmp_path), {})


def test_managed_fields_hidden_from_form():
    for step in pipeline.STEP_ORDER:
        dests = {f.dest for f in pipeline.visible_fields(step)}
        assert not dests & pipeline.STEPS[step][2]
    assert next(f for f in pipeline.visible_fields("extract") if f.dest == "route").advanced is False


def test_warnings_extracted_and_status_shows_them():
    log = "x\nWARNING (FA-15): 40% rejected\nWARNING (FA-16): unknown trips\nfine"
    w = jobs.warnings_in(log)
    assert len(w) == 2
    s = pipeline._status("match", 0, [f"**match:** {x}" for x in w])
    assert "FA-15" in s and "Quality warnings" in s


def test_run_reports_missing_input_without_launching(tmp_path):
    out = list(pipeline.run(["match"], "", "", "", str(tmp_path), {}))
    assert out[-1][1].startswith("⚠️")
