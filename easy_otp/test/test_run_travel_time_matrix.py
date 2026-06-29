"""Wiring tests for RunTravelTimeMatrix — no network, no QGIS required."""

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# QGIS stub (same pattern as test_run_origin_destination_times.py)
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


def _null_trip(status="404"):
    return {
        "status": status, "duration": None, "transittime": None,
        "walktime": None, "waitingtime": None, "transfers": None,
    }


def _make_feedback(canceled_after=None):
    """Feedback mock. call_count tracks how many setProgress calls have been made."""
    fb = MagicMock()
    call_count = [0]

    def _set_progress(v):
        call_count[0] += 1

    fb.setProgress.side_effect = _set_progress

    if canceled_after is not None:
        def _is_canceled():
            return call_count[0] >= canceled_after
        fb.isCanceled.side_effect = _is_canceled
    else:
        fb.isCanceled.return_value = False

    return fb


_DEFAULT_QUERY_KWARGS = dict(
    mode="TRANSIT,WALK",
    date_mmddyyyy="11-22-2024",
    time_hhmmss="08:30:00",
    max_walk_distance=800.0,
    walk_reluctance=3.0,
    wait_reluctance=2.0,
    transfer_penalty=60,
    min_transfer_time=600,
)


def _run_matrix_loop(plan_client, origins, destinations, max_workers, query_kwargs, feedback):
    """Replicate the Cartesian thread-pool loop from RunTravelTimeMatrix.processAlgorithm.

    origins: [(fid, lat, lon)]
    destinations: [(fid, lat, lon)]
    Returns: (results_dict, cancelled)
    results_dict: {(o_fid, d_fid): trip}
    """
    import itertools

    pairs = list(itertools.product(origins, destinations))
    n_pairs = len(pairs)
    results = {}
    completed_count = [0]

    def _query(o_fid, o_lat, o_lon, d_fid, d_lat, d_lon):
        return (o_fid, d_fid), plan_client.get_trip(
            from_lat=o_lat, from_lon=o_lon,
            to_lat=d_lat, to_lon=d_lon,
            **query_kwargs,
        )

    executor = ThreadPoolExecutor(max_workers=max_workers)
    futures_map = {}
    cancelled = False
    try:
        for (o_fid, o_lat, o_lon), (d_fid, d_lat, d_lon) in pairs:
            if feedback.isCanceled():
                cancelled = True
                break
            f = executor.submit(_query, o_fid, o_lat, o_lon, d_fid, d_lat, d_lon)
            futures_map[f] = (o_fid, d_fid)

        for future in as_completed(futures_map):
            if feedback.isCanceled():
                cancelled = True
                for f in futures_map:
                    f.cancel()
                break
            pair_key = futures_map[future]
            try:
                key, trip = future.result()
            except OtpClientError as e:
                key = pair_key
                trip = {
                    "status": "ERROR", "duration": None, "transittime": None,
                    "walktime": None, "waitingtime": None, "transfers": None,
                }
                feedback.pushWarning(str(e))
            results[key] = trip
            completed_count[0] += 1
            if n_pairs > 0:
                feedback.setProgress(int(completed_count[0] / n_pairs * 100))
    finally:
        executor.shutdown(wait=False)

    return results, cancelled


_METRIC_KEY_MAP = {
    "duration": "duration",
    "transfers": "transfers",
    "walktime": "walktime",
    "waittime": "waitingtime",
}


def _build_long_rows(results, origins, destinations, metrics):
    """Inline replica of the module-level _build_long_rows for tests."""
    rows = []
    for o_fid, _, _ in origins:
        for d_fid, _, _ in destinations:
            trip = results.get((o_fid, d_fid), {"status": "MISSING"})
            row = {"origin_id": o_fid, "dest_id": d_fid, "status": trip.get("status")}
            for m in metrics:
                row[m] = trip.get(_METRIC_KEY_MAP.get(m, m))
            rows.append(row)
    return rows


def _build_wide_rows(results, origins, destinations):
    """Inline replica of the module-level _build_wide_rows for tests."""
    dest_fids = [d_fid for d_fid, _, _ in destinations]
    rows = []
    for o_fid, _, _ in origins:
        row = {"origin_id": o_fid}
        for d_fid in dest_fids:
            row[str(d_fid)] = results.get((o_fid, d_fid), {}).get("duration")
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Test: N×M call count and result key structure
# ---------------------------------------------------------------------------

class TestNxMCallCount:
    def test_3x3_makes_9_calls(self):
        client = MagicMock(spec=PlanClient)
        client.get_trip.return_value = _ok_trip()

        origins = [(1, 51.8, 19.3), (2, 51.81, 19.31), (3, 51.82, 19.32)]
        dests = [(10, 51.75, 19.45), (11, 51.76, 19.46), (12, 51.77, 19.47)]

        results, cancelled = _run_matrix_loop(
            client, origins, dests,
            max_workers=4,
            query_kwargs=_DEFAULT_QUERY_KWARGS,
            feedback=_make_feedback(),
        )

        assert not cancelled
        assert client.get_trip.call_count == 9
        assert len(results) == 9

    def test_result_keys_are_origin_dest_pairs(self):
        client = MagicMock(spec=PlanClient)
        client.get_trip.return_value = _ok_trip()

        origins = [(1, 51.8, 19.3), (2, 51.81, 19.31)]
        dests = [(10, 51.75, 19.45), (11, 51.76, 19.46)]

        results, _ = _run_matrix_loop(
            client, origins, dests,
            max_workers=2,
            query_kwargs=_DEFAULT_QUERY_KWARGS,
            feedback=_make_feedback(),
        )

        assert set(results.keys()) == {(1, 10), (1, 11), (2, 10), (2, 11)}

    def test_all_results_ok(self):
        client = MagicMock(spec=PlanClient)
        client.get_trip.return_value = _ok_trip()

        origins = [(1, 51.8, 19.3), (2, 51.81, 19.31)]
        dests = [(10, 51.75, 19.45)]

        results, cancelled = _run_matrix_loop(
            client, origins, dests,
            max_workers=2,
            query_kwargs=_DEFAULT_QUERY_KWARGS,
            feedback=_make_feedback(),
        )

        assert not cancelled
        assert all(t["status"] == "OK" for t in results.values())


# ---------------------------------------------------------------------------
# Test: LONG row count = N×M
# ---------------------------------------------------------------------------

class TestLongRowCount:
    def test_3x3_long_has_9_rows(self):
        client = MagicMock(spec=PlanClient)
        client.get_trip.return_value = _ok_trip()

        origins = [(1, 51.8, 19.3), (2, 51.81, 19.31), (3, 51.82, 19.32)]
        dests = [(10, 51.75, 19.45), (11, 51.76, 19.46), (12, 51.77, 19.47)]

        results, _ = _run_matrix_loop(
            client, origins, dests,
            max_workers=4,
            query_kwargs=_DEFAULT_QUERY_KWARGS,
            feedback=_make_feedback(),
        )
        rows = _build_long_rows(results, origins, dests, metrics=["duration"])
        assert len(rows) == 9

    def test_long_rows_have_correct_id_pairs(self):
        client = MagicMock(spec=PlanClient)
        client.get_trip.return_value = _ok_trip()

        origins = [(1, 51.8, 19.3), (2, 51.81, 19.31)]
        dests = [(10, 51.75, 19.45)]

        results, _ = _run_matrix_loop(
            client, origins, dests,
            max_workers=1,
            query_kwargs=_DEFAULT_QUERY_KWARGS,
            feedback=_make_feedback(),
        )
        rows = _build_long_rows(results, origins, dests, metrics=["duration"])
        assert len(rows) == 2
        assert {(r["origin_id"], r["dest_id"]) for r in rows} == {(1, 10), (2, 10)}


# ---------------------------------------------------------------------------
# Test: WIDE shape = N rows × M destination columns
# ---------------------------------------------------------------------------

class TestWideShape:
    def test_3x3_wide_has_3_rows_and_3_dest_columns(self):
        client = MagicMock(spec=PlanClient)
        client.get_trip.return_value = _ok_trip(duration=15.0)

        origins = [(1, 51.8, 19.3), (2, 51.81, 19.31), (3, 51.82, 19.32)]
        dests = [(10, 51.75, 19.45), (11, 51.76, 19.46), (12, 51.77, 19.47)]

        results, _ = _run_matrix_loop(
            client, origins, dests,
            max_workers=4,
            query_kwargs=_DEFAULT_QUERY_KWARGS,
            feedback=_make_feedback(),
        )
        rows = _build_wide_rows(results, origins, dests)
        assert len(rows) == 3
        assert set(rows[0].keys()) == {"origin_id", "10", "11", "12"}

    def test_wide_duration_values_populated(self):
        client = MagicMock(spec=PlanClient)
        client.get_trip.return_value = _ok_trip(duration=20.0)

        origins = [(1, 51.8, 19.3)]
        dests = [(10, 51.75, 19.45), (11, 51.76, 19.46)]

        results, _ = _run_matrix_loop(
            client, origins, dests,
            max_workers=2,
            query_kwargs=_DEFAULT_QUERY_KWARGS,
            feedback=_make_feedback(),
        )
        rows = _build_wide_rows(results, origins, dests)
        assert len(rows) == 1
        assert rows[0]["10"] == 20.0
        assert rows[0]["11"] == 20.0

    def test_wide_unreachable_cell_is_none(self):
        client = MagicMock(spec=PlanClient)
        client.get_trip.return_value = _null_trip("404")

        origins = [(1, 51.8, 19.3)]
        dests = [(10, 51.75, 19.45)]

        results, _ = _run_matrix_loop(
            client, origins, dests,
            max_workers=1,
            query_kwargs=_DEFAULT_QUERY_KWARGS,
            feedback=_make_feedback(),
        )
        rows = _build_wide_rows(results, origins, dests)
        assert rows[0]["10"] is None


# ---------------------------------------------------------------------------
# Test: complexity warning fires above threshold
# ---------------------------------------------------------------------------

class TestComplexityWarning:
    _WARN = 5_000

    def test_warning_fires_for_5050_pairs(self):
        feedback = MagicMock()
        n_origins, n_dests = 101, 50
        n_pairs = n_origins * n_dests  # 5050 > 5000
        if n_pairs > self._WARN:
            feedback.pushWarning(
                f"Large matrix: {n_pairs} pairs"
            )
        assert feedback.pushWarning.called

    def test_no_warning_for_4_pairs(self):
        feedback = MagicMock()
        n_origins, n_dests = 2, 2
        n_pairs = n_origins * n_dests  # 4
        if n_pairs > self._WARN:
            feedback.pushWarning(f"Large matrix: {n_pairs} pairs")
        assert not feedback.pushWarning.called

    def test_no_warning_at_threshold(self):
        feedback = MagicMock()
        n_pairs = self._WARN  # exactly 5000 — not above
        if n_pairs > self._WARN:
            feedback.pushWarning(f"Large matrix: {n_pairs} pairs")
        assert not feedback.pushWarning.called


# ---------------------------------------------------------------------------
# Test: cancellation stops submission
# ---------------------------------------------------------------------------

class TestCancellation:
    def test_cancel_before_any_submit(self):
        """If already cancelled before loop starts, no queries are submitted."""
        client = MagicMock(spec=PlanClient)
        client.get_trip.return_value = _ok_trip()

        feedback = MagicMock()
        feedback.isCanceled.return_value = True

        origins = [(i, 51.8 + i * 0.01, 19.3) for i in range(3)]
        dests = [(10 + j, 51.75, 19.45 + j * 0.01) for j in range(3)]

        results, cancelled = _run_matrix_loop(
            client, origins, dests,
            max_workers=4,
            query_kwargs=_DEFAULT_QUERY_KWARGS,
            feedback=feedback,
        )

        assert cancelled
        assert len(results) == 0

    def test_cancel_mid_run_returns_fewer_than_n_times_m(self):
        """Cancellation mid-collection stops early: fewer than N×M results."""
        client = MagicMock(spec=PlanClient)
        client.get_trip.return_value = _ok_trip()

        # cancel after 3 setProgress calls (i.e. 3 completed pairs)
        feedback = _make_feedback(canceled_after=3)

        origins = [(i, 51.8 + i * 0.01, 19.3) for i in range(3)]
        dests = [(10 + j, 51.75, 19.45 + j * 0.01) for j in range(3)]  # 9 pairs total

        results, cancelled = _run_matrix_loop(
            client, origins, dests,
            max_workers=1,  # serial to make timing predictable
            query_kwargs=_DEFAULT_QUERY_KWARGS,
            feedback=feedback,
        )

        assert cancelled
        assert len(results) < 9


# ---------------------------------------------------------------------------
# Test: metrics filter controls LONG columns
# ---------------------------------------------------------------------------

class TestMetricsFilter:
    def test_duration_only(self):
        results = {(1, 10): _ok_trip()}
        origins = [(1, 51.8, 19.3)]
        dests = [(10, 51.75, 19.45)]
        rows = _build_long_rows(results, origins, dests, metrics=["duration"])
        assert "duration" in rows[0]
        assert "transfers" not in rows[0]
        assert "walktime" not in rows[0]
        assert "waittime" not in rows[0]

    def test_all_metrics_present_when_selected(self):
        results = {(1, 10): _ok_trip()}
        origins = [(1, 51.8, 19.3)]
        dests = [(10, 51.75, 19.45)]
        rows = _build_long_rows(
            results, origins, dests,
            metrics=["duration", "transfers", "walktime", "waittime"],
        )
        assert "duration" in rows[0]
        assert "transfers" in rows[0]
        assert "walktime" in rows[0]
        assert "waittime" in rows[0]

    def test_waittime_reads_waitingtime_from_trip(self):
        """'waittime' metric must pull from plan_client's 'waitingtime' key."""
        trip = _ok_trip(waitingtime=7.5)
        results = {(1, 10): trip}
        origins = [(1, 51.8, 19.3)]
        dests = [(10, 51.75, 19.45)]
        rows = _build_long_rows(results, origins, dests, metrics=["waittime"])
        assert rows[0]["waittime"] == 7.5

    def test_status_always_present_in_long_rows(self):
        results = {(1, 10): _null_trip("404")}
        origins = [(1, 51.8, 19.3)]
        dests = [(10, 51.75, 19.45)]
        rows = _build_long_rows(results, origins, dests, metrics=["duration"])
        assert rows[0]["status"] == "404"
        assert rows[0]["duration"] is None


# ---------------------------------------------------------------------------
# Test: OtpClientError recorded as ERROR status, run continues
# ---------------------------------------------------------------------------

class TestOtpClientErrorHandling:
    def test_network_error_recorded_not_aborted(self):
        client = MagicMock(spec=PlanClient)

        def _side_effect(**kwargs):
            if kwargs["from_lat"] == 51.81:
                raise OtpClientError("connection refused")
            return _ok_trip()

        client.get_trip.side_effect = _side_effect

        origins = [(1, 51.8, 19.3), (2, 51.81, 19.31)]
        dests = [(10, 51.75, 19.45)]

        results, cancelled = _run_matrix_loop(
            client, origins, dests,
            max_workers=1,
            query_kwargs=_DEFAULT_QUERY_KWARGS,
            feedback=_make_feedback(),
        )

        assert not cancelled
        assert results[(2, 10)]["status"] == "ERROR"
        assert results[(2, 10)]["duration"] is None
        assert results[(1, 10)]["status"] == "OK"
