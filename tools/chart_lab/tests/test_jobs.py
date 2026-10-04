"""Unit tests for chart_lab.jobs (GL-1): child-process runner, log streaming, cancel.

    cd tools/chart_lab
    py -m pytest tests/test_jobs.py -q
"""
from __future__ import annotations

import http.server
import threading
import time

import pytest

from chart_lab import jobs


def _run(tool, argv):
    items = list(jobs.stream(tool, argv, poll_s=0.05))
    return items[-1]


def test_success_exit_code_and_log():
    log, code = _run("transit_charts", ["--help"])
    assert code == 0
    assert "extract" in log


def test_argparse_error_goes_to_log_with_nonzero_code():
    log, code = _run("family_a", ["match"])  # required args missing
    assert code == 2
    assert "--positions-dir" in log


def test_command_failure_exit_code():
    log, code = _run("family_a", ["match", "--positions-dir", "nope", "--static", "nope.zip", "--out", "x.csv"])
    assert code == 1


def test_rt_diagnose_usage_exit():
    log, code = _run("rt_diagnose", [])
    assert code == 1
    assert log.strip()


def test_unknown_tool_is_error_not_hang():
    log, code = _run("nope", [])
    assert code == 1
    assert "unknown tool" in log


class _Quiet(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"x")

    def log_message(self, *a):
        pass


def test_cancel_and_busy(tmp_path):
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Quiet)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    argv = ["record", "--url", f"http://127.0.0.1:{srv.server_port}/", "--out-dir", str(tmp_path),
            "--duration-min", "5", "--interval-sec", "60"]
    gen = jobs.stream("family_a", argv, poll_s=0.05)
    next(gen)  # started
    with pytest.raises(jobs.JobBusy):
        next(jobs.stream("transit_charts", ["--help"]))
    time.sleep(1)
    assert jobs.cancel() is True
    last = list(gen)[-1]
    assert last[1] is not None and last[1] != 0
    assert jobs.cancel() is False
    srv.shutdown()


def test_gates_listed_and_library_warnings_collapsed():
    log = ("ok\nWARNING (FA-15): 40% rejected\n[p50] a.zip: 1/2 -- PROBLEM missing=[]\n"
           + "WARNING: matcher.py: windowed search found nothing\n" * 300)
    w = jobs.warnings_in(log)
    assert len(w) == 3 and w[0].startswith("WARNING (FA-15)") and "PROBLEM" in w[1]
    assert w[2].startswith("300 library warning(s)")


def test_cancel_before_start_is_not_lost():
    jobs.cancel()  # nothing running; flag stays set
    assert jobs.cancel_requested()
    last = list(jobs.stream("transit_charts", ["--help"], reset_cancel=False, poll_s=0.01))[-1]
    assert jobs.cancel_requested()
    assert list(jobs.stream("transit_charts", ["--help"], poll_s=0.01))[-1][1] == 0  # default resets
    assert not jobs.cancel_requested()
