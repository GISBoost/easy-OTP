"""Unit tests for chart_lab.tabs.simple (GL-4): the three small tabs' argv, no Gradio launch.

    cd tools/chart_lab
    py -m pytest tests/test_simple_tabs.py -q
"""
from __future__ import annotations

from chart_lab import paths  # noqa: F401 - side effect: sys.path wiring
from chart_lab import cli_form
from chart_lab.tabs import simple
import build_realized
from transit_charts import cli as tc_cli


def test_stop_headway_argv_parses_back():
    f = simple.stop_headway_fields()
    argv = cli_form.to_argv(f, {"matched": "m.csv", "static": "g.zip", "city": "lodz",
                                "out_prefix": "o", "min_n_stop": 5.0}, "stop-headway")
    ns = tc_cli.build_parser().parse_args(argv)
    assert ns.min_n_stop == 5 and str(ns.out_prefix) == "o"


def test_realized_b_argv_parses_back():
    f = simple.realized_b_fields()
    argv = cli_form.to_argv(f, {"snapshots": "raw", "static": "pt.zip", "out_prefix": "o",
                                "service_days": "2026-09-08"}, None)
    ns = build_realized.build_parser().parse_args(argv)
    assert str(ns.snapshots) == "raw" and ns.service_days == "2026-09-08" and ns.agency_id == "LKA"


def test_diagnose_positionals_in_order():
    f = simple.diagnose_fields()
    assert cli_form.to_argv(f, {"pb": "t.pb", "static_zip": "g.zip"}, None) == ["t.pb", "g.zip"]


def test_diagnose_bad_input_reports_failure(tmp_path):
    items = list(simple.run_values("rt_diagnose", simple.diagnose_fields(),
                                   {"pb": str(tmp_path / "x.pb"), "static_zip": str(tmp_path / "x.zip")}, None))
    assert items[-1][1] == 1 and "x.pb" in items[-1][0]


def test_diagnose_verdict_extracted():
    md, _ = simple._diagnose_result({}, 0, "a\nVERDICT: NEITHER  (RT-1 infeasible)\nb")
    assert "NEITHER" in md


def test_tabs_build():
    import gradio as gr
    with gr.Blocks():
        simple.build_stop_headway_tab()
        simple.build_realized_b_tab()
        simple.build_diagnose_tab()
