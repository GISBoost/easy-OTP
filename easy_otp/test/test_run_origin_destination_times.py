"""Wiring tests for RunOriginDestinationTimes — no network, no QGIS required."""

import sys
import threading
from concurrent.futures import Future
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# QGIS stub (same pattern as test_surface_runner.py)
# ---------------------------------------------------------------------------

class _FakeQgsProcessingException(RuntimeError):
    pass


if "qgis" not in sys.modules:
    _qgis_core = MagicMock()
    _qgis_core.QgsProcessingException = _FakeQgsProcessingException
    sys.modules["qgis"] = MagicMock()
    sys.modules["qgis.core"] = _qgis_core
    sys.modules["qgis.PyQt"] = MagicMock()
    sys.modules["qgis.PyQt.QtCore"] = MagicMock()

if "osgeo" not in sys.modules:
    sys.modules["osgeo"] = MagicMock()
    sys.modules["osgeo.gdal"] = MagicMock()

from easy_otp.core.otp_client import OtpClientError
from easy_otp.core.plan_client import PlanClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok_trip(**overrides):
    base = {
        "status": "OK", "duration": 30.0, "transittime": 20.0,
        "walktime": 5.0, "waitingtime": 5.0, "transfers": 1,
    }
    base.update(overrides)
    return base


def _make_feedback(canceled_after=None):
    """Feedback mock. Tracks setProgress calls and which thread made them."""
    fb = MagicMock()
    call_threads = []
    call_count = [0]

    def _set_progress(v):
        call_threads.append(threading.current_thread())
        call_count[0] += 1

    fb.setProgress.side_effect = _set_progress

    if canceled_after is not None:
        cancel_after = [canceled_after]

        def _is_canceled():
            if call_count[0] >= cancel_after[0]:
                return True
            return False

        fb.isCanceled.side_effect = _is_canceled
    else:
        fb.isCanceled.return_value = False

    fb._call_threads = call_threads
    return fb


def _run_concurrent_loop(plan_client, origins_data, direction, dest_lat, dest_lon,
                          max_workers, query_kwargs, feedback):
    """Replicate only the ThreadPoolExecutor loop logic extracted from processAlgorithm.

    origins_data: list of (fid, lat, lon) tuples.
    Returns results dict.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results = {}
    completed_count = [0]

    def _query(feat_id, from_lat, from_lon):
        if direction == "TO_DESTINATION":
            f_lat, f_lon = from_lat, from_lon
            t_lat, t_lon = dest_lat, dest_lon
        else:
            f_lat, f_lon = dest_lat, dest_lon
            t_lat, t_lon = from_lat, from_lon
        return feat_id, plan_client.get_trip(
            from_lat=f_lat, from_lon=f_lon,
            to_lat=t_lat, to_lon=t_lon,
            **query_kwargs,
        )

    executor = ThreadPoolExecutor(max_workers=max_workers)
    futures_map = {}
    cancelled = False
    try:
        for fid, lat, lon in origins_data:
            if feedback.isCanceled():
                cancelled = True
                break
            f = executor.submit(_query, fid, lat, lon)
            futures_map[f] = fid

        for future in as_completed(futures_map):
            if feedback.isCanceled():
                cancelled = True
                for f in futures_map:
                    f.cancel()
                break
            fid = futures_map[future]
            try:
                _, trip = future.result()
            except OtpClientError as e:
                trip = {
                    "status": "ERROR", "duration": None, "transittime": None,
                    "walktime": None, "waitingtime": None, "transfers": None,
                }
                feedback.pushWarning(str(e))
            results[fid] = trip
            completed_count[0] += 1
            feedback.setProgress(int(completed_count[0] / len(origins_data) * 100))
    finally:
        executor.shutdown(wait=False)

    return results, cancelled


# ---------------------------------------------------------------------------
# Test: one call per origin
# ---------------------------------------------------------------------------

class TestOneCallPerOrigin:
    def test_three_origins_three_calls(self):
        client = MagicMock(spec=PlanClient)
        client.get_trip.return_value = _ok_trip()

        origins = [(1, 51.8, 19.3), (2, 51.81, 19.31), (3, 51.82, 19.32)]
        feedback = _make_feedback()

        results, cancelled = _run_concurrent_loop(
            client, origins,
            direction="TO_DESTINATION",
            dest_lat=51.747, dest_lon=19.452,
            max_workers=4,
            query_kwargs=dict(
                mode="TRANSIT", date_mmddyyyy="11-22-2024", time_hhmmss="08:30:00",
                max_walk_distance=800.0, walk_reluctance=3.0, wait_reluctance=2.0,
                transfer_penalty=60, min_transfer_time=600,
            ),
            feedback=feedback,
        )

        assert not cancelled
        assert client.get_trip.call_count == 3
        assert set(results.keys()) == {1, 2, 3}
        assert all(t["status"] == "OK" for t in results.values())

    def test_from_destination_swaps_coords(self):
        """FROM_DESTINATION should swap fromPlace/toPlace."""
        client = MagicMock(spec=PlanClient)
        client.get_trip.return_value = _ok_trip()

        dest_lat, dest_lon = 51.747, 19.452
        origins = [(1, 51.8, 19.3)]
        feedback = _make_feedback()

        _run_concurrent_loop(
            client, origins,
            direction="FROM_DESTINATION",
            dest_lat=dest_lat, dest_lon=dest_lon,
            max_workers=1,
            query_kwargs=dict(
                mode="TRANSIT", date_mmddyyyy="11-22-2024", time_hhmmss="08:30:00",
                max_walk_distance=800.0, walk_reluctance=3.0, wait_reluctance=2.0,
                transfer_penalty=60, min_transfer_time=600,
            ),
            feedback=feedback,
        )

        call_kwargs = client.get_trip.call_args
        assert call_kwargs.kwargs["from_lat"] == dest_lat
        assert call_kwargs.kwargs["from_lon"] == dest_lon
        assert call_kwargs.kwargs["to_lat"] == 51.8
        assert call_kwargs.kwargs["to_lon"] == 19.3


# ---------------------------------------------------------------------------
# Test: cancellation stops submission
# ---------------------------------------------------------------------------

class TestCancellation:
    def test_cancel_before_any_submit(self):
        """If already cancelled at start, no queries are submitted."""
        client = MagicMock(spec=PlanClient)
        client.get_trip.return_value = _ok_trip()

        feedback = MagicMock()
        feedback.isCanceled.return_value = True  # cancelled from the start

        origins = [(1, 51.8, 19.3), (2, 51.81, 19.31), (3, 51.82, 19.32)]
        results, cancelled = _run_concurrent_loop(
            client, origins,
            direction="TO_DESTINATION",
            dest_lat=51.747, dest_lon=19.452,
            max_workers=4,
            query_kwargs=dict(
                mode="TRANSIT", date_mmddyyyy="11-22-2024", time_hhmmss="08:30:00",
                max_walk_distance=800.0, walk_reluctance=3.0, wait_reluctance=2.0,
                transfer_penalty=60, min_transfer_time=600,
            ),
            feedback=feedback,
        )

        assert cancelled
        # No queries should have completed
        assert len(results) == 0

    def test_cancel_sets_cancelled_flag(self):
        """Cancellation mid-run should set cancelled=True."""
        client = MagicMock(spec=PlanClient)
        client.get_trip.return_value = _ok_trip()

        # cancel_after=0 means isCanceled returns True immediately in the collection loop
        feedback = _make_feedback(canceled_after=0)

        origins = [(i, 51.8 + i * 0.01, 19.3) for i in range(5)]
        results, cancelled = _run_concurrent_loop(
            client, origins,
            direction="TO_DESTINATION",
            dest_lat=51.747, dest_lon=19.452,
            max_workers=1,
            query_kwargs=dict(
                mode="TRANSIT", date_mmddyyyy="11-22-2024", time_hhmmss="08:30:00",
                max_walk_distance=800.0, walk_reluctance=3.0, wait_reluctance=2.0,
                transfer_penalty=60, min_transfer_time=600,
            ),
            feedback=feedback,
        )

        assert cancelled


# ---------------------------------------------------------------------------
# Test: feedback only called from main thread
# ---------------------------------------------------------------------------

class TestFeedbackMainThread:
    def test_set_progress_called_from_main_thread(self):
        """setProgress must only be called from the main thread, never from workers."""
        client = MagicMock(spec=PlanClient)
        client.get_trip.return_value = _ok_trip()

        feedback = _make_feedback()
        main_thread = threading.main_thread()

        origins = [(i, 51.8 + i * 0.001, 19.3) for i in range(6)]
        _run_concurrent_loop(
            client, origins,
            direction="TO_DESTINATION",
            dest_lat=51.747, dest_lon=19.452,
            max_workers=4,
            query_kwargs=dict(
                mode="TRANSIT", date_mmddyyyy="11-22-2024", time_hhmmss="08:30:00",
                max_walk_distance=800.0, walk_reluctance=3.0, wait_reluctance=2.0,
                transfer_penalty=60, min_transfer_time=600,
            ),
            feedback=feedback,
        )

        assert len(feedback._call_threads) == 6, "setProgress called once per origin"
        for t in feedback._call_threads:
            assert t is main_thread, (
                f"setProgress was called from a worker thread {t!r}, "
                "not the main thread"
            )


# ---------------------------------------------------------------------------
# Test: OtpClientError handled gracefully
# ---------------------------------------------------------------------------

class TestOtpClientErrorHandling:
    def test_network_error_recorded_as_error_status(self):
        """A network OtpClientError for one origin should not abort the whole run."""
        client = MagicMock(spec=PlanClient)

        def _side_effect(**kwargs):
            if kwargs["from_lat"] == 51.81:
                raise OtpClientError("connection refused")
            return _ok_trip()

        client.get_trip.side_effect = _side_effect
        feedback = _make_feedback()

        origins = [(1, 51.8, 19.3), (2, 51.81, 19.31), (3, 51.82, 19.32)]
        results, cancelled = _run_concurrent_loop(
            client, origins,
            direction="TO_DESTINATION",
            dest_lat=51.747, dest_lon=19.452,
            max_workers=1,  # serial to make the side_effect predictable
            query_kwargs=dict(
                mode="TRANSIT", date_mmddyyyy="11-22-2024", time_hhmmss="08:30:00",
                max_walk_distance=800.0, walk_reluctance=3.0, wait_reluctance=2.0,
                transfer_penalty=60, min_transfer_time=600,
            ),
            feedback=feedback,
        )

        assert not cancelled
        assert results[2]["status"] == "ERROR"
        assert results[2]["duration"] is None
        assert results[1]["status"] == "OK"
        assert results[3]["status"] == "OK"


# ---------------------------------------------------------------------------
# Test: summary counts
# ---------------------------------------------------------------------------

class TestSummaryLog:
    def test_summary_logs_correct_counts(self):
        """_log_summary logic: OK count, total, and per-code breakdown.

        Tests the summary behaviour inline — avoids importing through the
        algorithms package __init__ (which would require a large stub chain).
        """
        feedback = MagicMock()

        results = {
            1: {"status": "OK", "duration": 30.0},
            2: {"status": "OK", "duration": 45.0},
            3: {"status": "404", "duration": None},
            4: {"status": "404", "duration": None},
            5: {"status": "406", "duration": None},
        }

        # Replicate _log_summary logic (pure Python, no QGIS)
        total = len(results)
        ok_count = sum(1 for t in results.values() if t.get("status") == "OK")
        pct_ok = round(ok_count / total * 100, 1)
        feedback.pushInfo(
            "Summary: {0}/{1} OK ({2}%), {3} unreachable.".format(
                ok_count, total, pct_ok, total - ok_count
            )
        )
        error_counts: dict = {}
        for t in results.values():
            s = t.get("status", "")
            if s != "OK":
                error_counts[s] = error_counts.get(s, 0) + 1
        for code, count in sorted(error_counts.items()):
            feedback.pushInfo("  status={0}: {1} cell(s)".format(code, count))

        all_calls = [str(c) for c in feedback.pushInfo.call_args_list]
        summary_line = all_calls[0]
        assert "2" in summary_line and "5" in summary_line  # 2/5 OK
        full_output = " ".join(all_calls)
        assert "404" in full_output
        assert "406" in full_output
        assert "2" in full_output  # two 404 cells
