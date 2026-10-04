"""Run one tools/ CLI command in a child process, streaming its log; cancellable.

A child process (not a thread, not `sys.executable -m ...`) because:
  - Cancel is `terminate()`;
  - the child's pandas memory goes back to the OS when it exits, instead of staying in the
    Gradio server;
  - in a PyInstaller build `sys.executable` is chart_lab.exe, not an interpreter, while
    multiprocessing (spawn + freeze_support() in app.py) works frozen and unfrozen.

No Gradio import here, so it is testable on its own.
"""
from __future__ import annotations

import contextlib
import logging
import multiprocessing as mp
import os
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from typing import Iterator

# ponytail: one job at a time, process-wide. Queue/parallel jobs if that is ever needed.
_LOCK = threading.Lock()
_CURRENT: mp.Process | None = None
_CANCEL = threading.Event()  # set by cancel(); survives the gaps between a job's start/steps

TOOLS = ("family_a", "transit_charts", "family_b", "rt_diagnose")


class JobBusy(RuntimeError):
    """Another job is already running."""


def _dispatch(tool: str, argv: list[str]) -> int:
    from chart_lab import paths  # noqa: F401 - side effect: sys.path wiring in the child

    if tool in ("family_a", "transit_charts"):
        if tool == "family_a":
            from family_a import cli
        else:
            from transit_charts import cli
        args = cli.build_parser().parse_args(argv)
        return int(args.func(args) or 0)
    if tool == "family_b":
        import build_realized

        return int(build_realized.main(argv) or 0)
    if tool == "rt_diagnose":
        import compare_rt_vs_static

        sys.argv = ["rt_diagnose", *argv]
        compare_rt_vs_static.main()
        return 0
    raise ValueError(f"unknown tool: {tool!r}")


def run_cli(tool: str, argv: list[str], log_path: str) -> None:
    """Child-process entry point (module level so `spawn` can pickle it)."""
    with open(log_path, "w", encoding="utf-8", buffering=1) as log, \
            contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        # family_a modules warn through `logging` (no handler configured -> bare text without a
        # level); give them a level prefix so warnings_in() can surface them.
        logging.basicConfig(format="%(levelname)s: %(message)s", stream=sys.stderr)
        try:
            code = _dispatch(tool, argv)
        except SystemExit as exc:  # argparse errors / sys.exit(<msg>) from the tools
            if isinstance(exc.code, str):
                print(exc.code)
                code = 1
            else:
                code = int(exc.code or 0)
        except Exception:
            traceback.print_exc()
            code = 1
    os._exit(code)  # skip interpreter teardown; the log is already flushed and closed


def cancel() -> bool:
    """Cancel the running job (and any later step of a multi-step run). True if one was alive."""
    _CANCEL.set()
    proc = _CURRENT
    if proc is not None and proc.is_alive():
        proc.terminate()
        return True
    return False


def cancel_requested() -> bool:
    return _CANCEL.is_set()


def stream(tool: str, argv: list[str], poll_s: float = 0.3,
           reset_cancel: bool = True) -> Iterator[tuple[str, int | None]]:
    """Run `tool argv` and yield (full log so far, exit code or None while running).

    The last item has the exit code (negative if cancelled). Raises JobBusy if another job
    is running. `reset_cancel=False` for the 2nd+ step of a run, so a Cancel click between
    steps is not forgotten.
    """
    global _CURRENT
    if not _LOCK.acquire(blocking=False):
        raise JobBusy("another job is already running")
    log_path = None
    try:
        proc = None
        if reset_cancel:
            _CANCEL.clear()
        fd, log_path = tempfile.mkstemp(prefix=f"chart_lab_{tool}_", suffix=".log")
        os.close(fd)
        proc = mp.get_context("spawn").Process(target=run_cli, args=(tool, list(argv), log_path))
        proc.start()
        _CURRENT = proc
        while proc.is_alive():
            if _CANCEL.is_set():
                proc.terminate()
            yield _read(log_path), None
            time.sleep(poll_s)
        proc.join()
        yield _read(log_path), proc.exitcode
    finally:
        if proc is not None and proc.is_alive():  # generator abandoned (e.g. the UI request was cancelled)
            proc.terminate()
            proc.join()
        _CURRENT = None
        if log_path:
            try:
                os.unlink(log_path)
            except OSError:
                pass
        _LOCK.release()


def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def warnings_in(log: str) -> list[str]:
    """Quality warnings to show above the log.

    Tool-level gates (`WARNING (FA-15)`, `WARNING (FA-16)`, `PROBLEM` checks) are listed one by
    one. Per-observation `logging` warnings (`WARNING: matcher.py: ...`, hundreds per run) are
    collapsed into a single count line so they cannot drown the gates.
    """
    gates, noisy = [], []
    for ln in (x.strip() for x in log.splitlines()):
        if ln.startswith("WARNING ("):
            gates.append(ln)
        elif ln.startswith("WARNING:"):
            noisy.append(ln)
        elif ln.startswith("PROBLEM") or " PROBLEM " in ln:
            gates.append(ln)
    if noisy:
        gates.append(f"{len(noisy)} library warning(s) in the log, first: {noisy[0][:160]}")
    return gates


def describe_exit(code: int | None) -> str:
    if code is None:
        return "running"
    if code == 0:
        return "done"
    return "cancelled" if code < 0 else f"failed (exit code {code})"
