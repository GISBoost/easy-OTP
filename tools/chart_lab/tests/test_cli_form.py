"""Unit tests for chart_lab.cli_form (GL-2): argparse -> form fields -> argv.

    cd tools/chart_lab
    py -m pytest tests/test_cli_form.py -q
"""
from __future__ import annotations

import pytest

from chart_lab import paths  # noqa: F401 - side effect: sys.path wiring
from chart_lab import cli_form
from family_a import cli as fa_cli
from transit_charts import cli as tc_cli

FA = fa_cli.build_parser()
TC = tc_cli.build_parser()

ALL = [(FA, s) for s in ("record", "match", "build")] + [(TC, s) for s in ("extract", "chart", "stop-headway")]


@pytest.mark.parametrize("parser,sub", ALL)
def test_every_subcommand_describes_and_round_trips_defaults(parser, sub):
    fields = cli_form.describe(parser, sub)
    assert fields
    # Feeding every default back in must reproduce "nothing set" for optional args.
    vals = {f.dest: f.default for f in fields}
    argv = cli_form.to_argv(fields, vals, sub)
    assert argv[0] == sub
    assert not [a for a in argv if a.startswith("--") and a not in
                {f.flag for f in fields if f.required}]


def test_help_is_expanded_no_raw_percent_escapes():
    for parser, sub in ALL:
        for f in cli_form.describe(parser, sub):
            assert "%%" not in f.help and "%(default)" not in f.help, (sub, f.dest)


def test_match_argv_built_from_form_values_parses_back():
    fields = cli_form.describe(FA, "match")
    argv = cli_form.to_argv(fields, {
        "positions_dir": "d1\nd2\n", "static": "g.zip", "out": "m.csv",
        "max_perpendicular_dist_m": 150.0, "exclude_route_id": "R1\nR2",
        "fail_on_low_yield": True,
    }, "match")
    ns = FA.parse_args(argv)
    assert ns.positions_dir == ["d1", "d2"]
    assert ns.max_perpendicular_dist_m == 150.0
    assert ns.exclude_route_id == ["R1", "R2"]
    assert ns.fail_on_low_yield is True


def test_empty_and_default_values_are_omitted():
    fields = cli_form.describe(FA, "build")
    argv = cli_form.to_argv(fields, {
        "matched": "m.csv", "static": "g.zip", "out_prefix": "o",
        "min_observations_per_segment": 2,   # == default -> omitted
        "diagnostics_csv": "",                # empty -> omitted
        "keep_first_segment": False,
    }, "build")
    assert "--min-observations-per-segment" not in argv
    assert "--diagnostics-csv" not in argv
    assert "--keep-first-segment" not in argv
    assert FA.parse_args(argv).out_prefix == "o"


def test_int_widget_float_is_sent_as_int():
    fields = cli_form.describe(FA, "build")
    argv = cli_form.to_argv(fields, {"matched": "m", "static": "s", "out_prefix": "o",
                                     "time_bucket_minutes": 60.0}, "build")
    assert argv[argv.index("--time-bucket-minutes") + 1] == "60"
    assert FA.parse_args(argv).time_bucket_minutes == 60


def test_positional_chart_name_goes_after_flags():
    fields = cli_form.describe(TC, "chart")
    name = next(f for f in fields if f.flag is None)
    assert name.required and name.kind == "choice"
    argv = cli_form.to_argv(fields, {"name": "C9", "out_prefix": "o"}, "chart")
    assert argv[-1] == "C9"


def test_required_and_overlay_decide_basic_vs_advanced():
    fields = {f.dest: f for f in cli_form.describe(FA, "match", {"static": {"path": "file"}})}
    assert not fields["static"].advanced and fields["static"].path == "file"
    assert fields["max_perpendicular_dist_m"].advanced


def test_build_widgets_runs_for_every_subcommand():
    import gradio as gr
    for parser, sub in ALL:
        with gr.Blocks():
            comps, ordered = cli_form.build_widgets(cli_form.describe(parser, sub))
        assert len(comps) == len(ordered)


def test_cleared_text_field_with_nondefault_sends_explicit_blank_and_quotes_are_stripped():
    import build_realized
    f = cli_form.describe(build_realized.build_parser())
    argv = cli_form.to_argv(f, {"snapshots": '"C:/raw dir"  ', "static": "s.zip", "out_prefix": "o",
                                "agency_id": ""}, None)
    ns = build_realized.build_parser().parse_args(argv)
    assert ns.agency_id == "" and ns.snapshots.name == "raw dir"
