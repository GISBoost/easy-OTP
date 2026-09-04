"""Guard against pandas/numpy creeping back into cli.py's module-level imports (FA-1/FA-2/FA-3).

The phone-side Termux recorder runs ~30 concurrent 'family_a.cli record' processes at once
(scripts/termux/record_supervised.sh, one per city) - 'record' never needs pandas/numpy, but a
module-top `import pandas as pd` in cli.py (or in matcher.py/segment_stats.py, which cli.py
imports regardless of subcommand) used to cost every one of those processes ~50-58 MB of RSS for
nothing, which was found to be the dominant contributor to the phone's chronic low-memory state
(2026-09-04 investigation: ~1.6 GB of ~2.9 GB used was this import overhead alone). pandas/numpy
are still needed by 'match'/'build' - see the deferred `import pandas as pd` inside
_cmd_match/_cmd_build and inside matcher.match_snapshots/segment_stats.collect_stop_crossings.

Must run in a subprocess: sys.modules is process-global, and other test modules in the same
pytest session already import pandas (e.g. test_cli.py itself, for building matched DataFrames),
which would make an in-process "pandas not in sys.modules" assertion meaningless / order-dependent.

No QGIS, no network. Run: pytest tests/test_cli_lazy_imports.py -v
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_importing_cli_does_not_load_pandas_or_numpy():
    result = _run(
        "import sys; import family_a.cli; "
        "assert 'pandas' not in sys.modules; assert 'numpy' not in sys.modules; "
        "print('OK')"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "OK"


def test_build_parser_does_not_load_pandas_or_numpy():
    """build_parser() runs for every subcommand (argparse needs the full tree, including
    match/build's DEFAULT_* constants in their --help text, before it knows which subcommand
    was picked) - it must stay pandas/numpy-free regardless."""
    result = _run(
        "import sys; from family_a.cli import build_parser; build_parser(); "
        "assert 'pandas' not in sys.modules; assert 'numpy' not in sys.modules; "
        "print('OK')"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "OK"


def test_parsing_and_dispatching_record_args_does_not_load_pandas():
    result = _run(
        "import sys; from family_a.cli import build_parser, _cmd_record; "
        "args = build_parser().parse_args(['record', '--url', 'http://x.invalid/vp.pb', "
        "'--out-dir', 'unused', '--duration-min', '1']); "
        "assert args.func is _cmd_record; "
        "assert 'pandas' not in sys.modules; "
        "print('OK')"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "OK"


def test_match_and_build_still_pull_in_pandas_when_actually_invoked():
    """Confirms the deferred import isn't accidentally missing - 'match'/'build' must still work."""
    result = _run(
        "import sys; from family_a import matcher; "
        "assert 'pandas' not in sys.modules; "
        "matcher.match_snapshots([], {}, {}); "
        "assert 'pandas' in sys.modules; "
        "print('OK')"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "OK"
