"""Unit tests for family_a.cli (FA-1).

No QGIS, no network, no real wall-clock waits (time.monotonic/time.sleep are
mocked so the record loop runs instantly). Run: pytest tests/test_cli.py -v
"""

import json
import zipfile
from datetime import datetime
from pathlib import Path
from unittest import mock

import pytest
from google.transit import gtfs_realtime_pb2

from family_a.cli import _cmd_build, _cmd_match, _cmd_record, build_parser
from family_a.recorder import SnapshotFetchError


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------

def test_record_defaults():
    parser = build_parser()
    args = parser.parse_args(["record", "--url", "http://x", "--out-dir", "out"])
    assert args.duration_min == 60
    assert args.interval_sec == 60
    assert args.func is _cmd_record


def test_build_defaults():
    parser = build_parser()
    args = parser.parse_args(
        ["build", "--matched", "matched.csv", "--static", "gtfs.zip", "--out-prefix", "out"]
    )
    assert args.func is _cmd_build
    assert args.min_observations_per_segment == 2


def test_match_defaults():
    parser = build_parser()
    args = parser.parse_args(
        ["match", "--positions-dir", "pos", "--static", "gtfs.zip", "--out", "out.csv"]
    )
    assert args.func is _cmd_match
    assert args.max_perpendicular_dist_m == 100.0


@pytest.mark.parametrize("flag", ["--duration-min", "--interval-sec"])
def test_non_positive_values_rejected(flag):
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["record", "--url", "http://x", "--out-dir", "out", flag, "0"])


def test_build_min_observations_per_segment_rejects_non_positive():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(
            ["build", "--matched", "m.csv", "--static", "g.zip", "--out-prefix", "out",
             "--min-observations-per-segment", "0"]
        )


# ---------------------------------------------------------------------------
# _cmd_record (time mocked so the loop resolves instantly)
# ---------------------------------------------------------------------------

class _FakeClock:
    """Deterministic stand-in for time.monotonic that advances on each read."""

    def __init__(self, step: float = 1.0):
        self._now = 0.0
        self._step = step

    def monotonic(self) -> float:
        value = self._now
        self._now += self._step
        return value

    def sleep(self, _seconds: float) -> None:
        self._now += 1.0


def _make_args(tmp_path, **overrides):
    class _NS:
        url = "http://example.com/vehicles.pb"
        out_dir = str(tmp_path)
        duration_min = 1
        interval_sec = 60

    ns = _NS()
    for key, value in overrides.items():
        setattr(ns, key, value)
    return ns


def test_cmd_record_success_writes_snapshots_and_manifest(tmp_path):
    clock = _FakeClock(step=10.0)  # total_sec = 60; ~6 iterations before break
    with mock.patch("family_a.cli.time.monotonic", side_effect=clock.monotonic), \
            mock.patch("family_a.cli.time.sleep", side_effect=clock.sleep), \
            mock.patch("family_a.cli.fetch_snapshot", return_value=b"\x01\x02"):
        result = _cmd_record(_make_args(tmp_path))

    assert result == 0
    manifest = json.loads((tmp_path / "recording.json").read_text(encoding="utf-8"))
    assert manifest["snapshot_count"] >= 1
    assert manifest["failed_count"] == 0
    assert manifest["total_bytes"] == manifest["snapshot_count"] * 2
    snapshots = list(tmp_path.glob("snapshot_*.pb"))
    assert len(snapshots) == manifest["snapshot_count"]


def test_cmd_record_counts_failures_and_still_writes_manifest(tmp_path):
    clock = _FakeClock(step=10.0)
    with mock.patch("family_a.cli.time.monotonic", side_effect=clock.monotonic), \
            mock.patch("family_a.cli.time.sleep", side_effect=clock.sleep), \
            mock.patch("family_a.cli.fetch_snapshot", side_effect=SnapshotFetchError("HTTP 403")):
        result = _cmd_record(_make_args(tmp_path))

    assert result == 0
    manifest = json.loads((tmp_path / "recording.json").read_text(encoding="utf-8"))
    assert manifest["snapshot_count"] == 0
    assert manifest["failed_count"] >= 1
    assert manifest["feed_id"] == ""


def test_cmd_record_keyboard_interrupt_still_writes_manifest(tmp_path):
    with mock.patch("family_a.cli.time.monotonic", side_effect=[0.0, 5.0]), \
            mock.patch("family_a.cli.fetch_snapshot", side_effect=KeyboardInterrupt):
        result = _cmd_record(_make_args(tmp_path))

    assert result == 0
    assert (tmp_path / "recording.json").exists()


# ---------------------------------------------------------------------------
# _cmd_match (FA-2)
# ---------------------------------------------------------------------------


def _make_gtfs_zip(tmp_path):
    path = tmp_path / "gtfs.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("trips.txt", "trip_id,route_id,shape_id\ntrip1,routeA,shape1\n")
        zf.writestr(
            "shapes.txt",
            "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\n"
            "shape1,0.0,0.0,0\n"
            "shape1,0.01,0.0,1\n",
        )
    return path


def _write_snapshot(tmp_path):
    feed = gtfs_realtime_pb2.FeedMessage(
        header=gtfs_realtime_pb2.FeedHeader(gtfs_realtime_version="2.0"),
        entity=[
            gtfs_realtime_pb2.FeedEntity(
                id="e1",
                vehicle=gtfs_realtime_pb2.VehiclePosition(
                    trip=gtfs_realtime_pb2.TripDescriptor(trip_id="trip1"),
                    position=gtfs_realtime_pb2.Position(latitude=0.005, longitude=0.0),
                    timestamp=1_700_000_000,
                ),
            )
        ],
    )
    path = tmp_path / "snapshot_20260101-000000.pb"
    path.write_bytes(feed.SerializeToString())
    return path


def _make_match_args(tmp_path, **overrides):
    class _NS:
        positions_dir = str(tmp_path)
        static = None
        out = None
        max_perpendicular_dist_m = 100.0

    ns = _NS()
    for key, value in overrides.items():
        setattr(ns, key, value)
    return ns


def test_cmd_match_end_to_end_writes_csv(tmp_path, capsys):
    gtfs = _make_gtfs_zip(tmp_path)
    _write_snapshot(tmp_path)
    out_path = tmp_path / "matched.csv"

    args = _make_match_args(tmp_path, static=str(gtfs), out=str(out_path))
    result = _cmd_match(args)

    assert result == 0
    assert out_path.exists()
    content = out_path.read_text(encoding="utf-8")
    assert "trip1" in content

    captured = capsys.readouterr()
    assert "Snapshots processed" in captured.out
    assert "Observations matched" in captured.out


def test_cmd_match_no_snapshots_found_returns_1(tmp_path, capsys):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    args = _make_match_args(empty_dir, static="unused.zip", out="unused.csv")

    result = _cmd_match(args)

    assert result == 1
    captured = capsys.readouterr()
    assert "No snapshot_*.pb files found" in captured.err


def test_cmd_match_static_not_found_returns_1(tmp_path, capsys):
    _write_snapshot(tmp_path)
    args = _make_match_args(tmp_path, static=str(tmp_path / "missing.zip"), out=str(tmp_path / "out.csv"))

    result = _cmd_match(args)

    assert result == 1
    captured = capsys.readouterr()
    assert "Static GTFS not found" in captured.err


def test_cmd_match_static_not_a_zip_returns_1(tmp_path, capsys):
    _write_snapshot(tmp_path)
    not_a_zip = tmp_path / "not_a_zip.zip"
    not_a_zip.write_text("this is not a zip file", encoding="utf-8")
    args = _make_match_args(tmp_path, static=str(not_a_zip), out=str(tmp_path / "out.csv"))

    result = _cmd_match(args)

    assert result == 1
    captured = capsys.readouterr()
    assert "not a valid zip file" in captured.err


def test_cmd_match_static_missing_required_files_returns_1(tmp_path, capsys):
    _write_snapshot(tmp_path)
    incomplete_gtfs = tmp_path / "incomplete.zip"
    with zipfile.ZipFile(incomplete_gtfs, "w") as zf:
        zf.writestr(
            "shapes.txt",
            "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\nshape1,0.0,0.0,0\n",
        )
    args = _make_match_args(tmp_path, static=str(incomplete_gtfs), out=str(tmp_path / "out.csv"))

    result = _cmd_match(args)

    assert result == 1
    captured = capsys.readouterr()
    assert "missing required file(s)" in captured.err
    assert "trips.txt" in captured.err


def test_cmd_match_no_shapes_and_no_fallback_files_returns_1(tmp_path, capsys):
    """shapes.txt absent triggers the stops-fallback (matcher.py), which needs
    stops.txt/stop_times.txt - if those are ALSO absent, this must fail here
    with a clear message, not a raw zipfile KeyError from inside the fallback.
    """
    _write_snapshot(tmp_path)
    no_fallback_gtfs = tmp_path / "no_fallback.zip"
    with zipfile.ZipFile(no_fallback_gtfs, "w") as zf:
        zf.writestr("trips.txt", "trip_id,route_id,shape_id\ntrip1,routeA,\n")
    args = _make_match_args(tmp_path, static=str(no_fallback_gtfs), out=str(tmp_path / "out.csv"))

    result = _cmd_match(args)

    assert result == 1
    captured = capsys.readouterr()
    assert "missing required file(s)" in captured.err
    assert "stops.txt" in captured.err
    assert "stop_times.txt" in captured.err


def _make_gtfs_zip_no_shapes(tmp_path):
    """A GTFS zip with no shapes.txt AND no shape_id in trips.txt — the
    realistic combination that motivates the stops-fallback (see FA-2's
    matcher.load_fallback_shapes_from_stops docstring).
    """
    path = tmp_path / "gtfs_no_shapes.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("trips.txt", "trip_id,route_id,shape_id\ntrip1,routeA,\n")
        zf.writestr(
            "stops.txt",
            "stop_id,stop_lat,stop_lon\ns1,0.0,0.0\ns2,0.01,0.0\n",
        )
        zf.writestr(
            "stop_times.txt",
            "trip_id,stop_id,stop_sequence\ntrip1,s1,0\ntrip1,s2,1\n",
        )
    return path


def test_cmd_match_end_to_end_fallback_when_shapes_missing(tmp_path, capsys):
    gtfs = _make_gtfs_zip_no_shapes(tmp_path)
    _write_snapshot(tmp_path)
    out_path = tmp_path / "matched.csv"

    args = _make_match_args(tmp_path, static=str(gtfs), out=str(out_path))
    result = _cmd_match(args)

    assert result == 0
    content = out_path.read_text(encoding="utf-8")
    assert "trip1" in content  # would be absent (all "unknown_shape") if the fallback silently no-ops

    captured = capsys.readouterr()
    assert "falling back to straight-line" in captured.err
    assert "Fallback shapes used (shapes.txt missing): yes" in captured.out


def test_cmd_match_parquet_without_pyarrow_returns_1(tmp_path, capsys, monkeypatch):
    gtfs = _make_gtfs_zip(tmp_path)
    _write_snapshot(tmp_path)
    out_path = tmp_path / "matched.parquet"

    def _raise_import_error(self, *args, **kwargs):
        raise ImportError("pyarrow not installed")

    monkeypatch.setattr("pandas.DataFrame.to_parquet", _raise_import_error)

    args = _make_match_args(tmp_path, static=str(gtfs), out=str(out_path))
    result = _cmd_match(args)

    assert result == 1
    captured = capsys.readouterr()
    assert "pyarrow" in captured.err
    assert not out_path.exists()


# ---------------------------------------------------------------------------
# _cmd_build (FA-3)
# ---------------------------------------------------------------------------


def _make_build_static_zip(tmp_path):
    path = tmp_path / "gtfs_build.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "trips.txt",
            "trip_id,route_id,direction_id,shape_id\nt1,R1,0,shape1\n",
        )
        zf.writestr(
            "stops.txt",
            "stop_id,stop_lat,stop_lon\nA,0.0,0.0\nB,0.01,0.0\n",
        )
        zf.writestr(
            "stop_times.txt",
            "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
            "t1,08:00:00,08:00:00,A,1\n"
            "t1,08:10:00,08:10:00,B,2\n",
        )
        zf.writestr(
            "shapes.txt",
            "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\n"
            "shape1,0.0,0.0,0\n"
            "shape1,0.01,0.0,1\n",
        )
    return path


def _make_build_args(tmp_path, **overrides):
    class _NS:
        matched = None
        static = None
        out_prefix = None
        min_observations_per_segment = 1

    ns = _NS()
    for key, value in overrides.items():
        setattr(ns, key, value)
    return ns


def test_cmd_build_end_to_end_writes_both_zips(tmp_path, capsys):
    from family_a.matcher import project_point_to_polyline

    gtfs = _make_build_static_zip(tmp_path)
    d_b = project_point_to_polyline(0.01, 0.0, [(0.0, 0.0), (0.01, 0.0)])[0]

    matched_path = tmp_path / "matched.csv"
    matched_path.write_text(
        "trip_id,timestamp,distance_along_shape_m,perpendicular_dist_m\n"
        f"t1,2026-01-01T00:00:00Z,0.0,0.0\n"
        f"t1,2026-01-01T00:00:50Z,{d_b},0.0\n",
        encoding="utf-8",
    )

    out_prefix = str(tmp_path / "out")
    args = _make_build_args(tmp_path, matched=str(matched_path), static=str(gtfs), out_prefix=out_prefix)
    result = _cmd_build(args)

    assert result == 0
    p50_path = Path(f"{out_prefix}_p50.zip")
    p85_path = Path(f"{out_prefix}_p85.zip")
    assert p50_path.exists()
    assert p85_path.exists()

    with zipfile.ZipFile(p50_path) as zf:
        stop_times_content = zf.read("stop_times.txt").decode("utf-8")
    assert "t1" in stop_times_content

    captured = capsys.readouterr()
    assert "Trips processed" in captured.out
    assert "Segment observations collected" in captured.out
    assert "interpolation gaps" in captured.out
    assert "rejected (implausible segment time)" in captured.out
    assert "Segments corrected" in captured.out
    assert "P50 output written to" in captured.out
    assert "P85 output written to" in captured.out


def test_cmd_build_reports_gap_when_no_observations(tmp_path, capsys):
    gtfs = _make_build_static_zip(tmp_path)

    matched_path = tmp_path / "matched.csv"
    matched_path.write_text(
        "trip_id,timestamp,distance_along_shape_m,perpendicular_dist_m\n",
        encoding="utf-8",
    )

    out_prefix = str(tmp_path / "out")
    args = _make_build_args(tmp_path, matched=str(matched_path), static=str(gtfs), out_prefix=out_prefix)
    result = _cmd_build(args)

    assert result == 0
    captured = capsys.readouterr()
    assert "Segments corrected: 0" in captured.out
    assert "Segments as gap across the full static schedule (kept scheduled time): 1" in captured.out


def test_cmd_build_matched_file_not_found_returns_1(tmp_path, capsys):
    gtfs = _make_build_static_zip(tmp_path)
    out_prefix = str(tmp_path / "out")
    args = _make_build_args(
        tmp_path, matched=str(tmp_path / "missing.csv"), static=str(gtfs), out_prefix=out_prefix
    )

    result = _cmd_build(args)

    assert result == 1
    captured = capsys.readouterr()
    assert "--matched file not found" in captured.err


def test_cmd_build_matched_missing_columns_returns_1(tmp_path, capsys):
    gtfs = _make_build_static_zip(tmp_path)

    matched_path = tmp_path / "matched.csv"
    matched_path.write_text("foo,bar\n1,2\n", encoding="utf-8")

    out_prefix = str(tmp_path / "out")
    args = _make_build_args(tmp_path, matched=str(matched_path), static=str(gtfs), out_prefix=out_prefix)

    result = _cmd_build(args)

    assert result == 1
    captured = capsys.readouterr()
    assert "missing required column(s)" in captured.err
    assert "trip_id" in captured.err
    assert "timestamp" in captured.err
    assert "distance_along_shape_m" in captured.err


def test_cmd_build_static_missing_required_files_returns_1(tmp_path, capsys):
    incomplete_gtfs = tmp_path / "incomplete.zip"
    with zipfile.ZipFile(incomplete_gtfs, "w") as zf:
        zf.writestr("trips.txt", "trip_id,route_id,direction_id,shape_id\nt1,R1,0,shape1\n")
        zf.writestr("stops.txt", "stop_id,stop_lat,stop_lon\nA,0.0,0.0\nB,0.01,0.0\n")

    matched_path = tmp_path / "matched.csv"
    matched_path.write_text(
        "trip_id,timestamp,distance_along_shape_m,perpendicular_dist_m\n"
        "t1,2026-01-01T00:00:00Z,0.0,0.0\n",
        encoding="utf-8",
    )

    out_prefix = str(tmp_path / "out")
    args = _make_build_args(
        tmp_path, matched=str(matched_path), static=str(incomplete_gtfs), out_prefix=out_prefix
    )

    result = _cmd_build(args)

    assert result == 1
    captured = capsys.readouterr()
    assert "missing required file(s)" in captured.err
    assert "stop_times.txt" in captured.err


def _make_valid_matched_csv(tmp_path):
    matched_path = tmp_path / "matched.csv"
    matched_path.write_text(
        "trip_id,timestamp,distance_along_shape_m,perpendicular_dist_m\n"
        "t1,2026-01-01T00:00:00Z,0.0,0.0\n",
        encoding="utf-8",
    )
    return matched_path


def test_cmd_build_static_not_found_returns_1(tmp_path, capsys):
    matched_path = _make_valid_matched_csv(tmp_path)
    out_prefix = str(tmp_path / "out")
    args = _make_build_args(
        tmp_path, matched=str(matched_path), static=str(tmp_path / "missing.zip"), out_prefix=out_prefix
    )

    result = _cmd_build(args)

    assert result == 1
    captured = capsys.readouterr()
    assert "Static GTFS not found" in captured.err


def test_cmd_build_static_not_a_zip_returns_1(tmp_path, capsys):
    matched_path = _make_valid_matched_csv(tmp_path)
    not_a_zip = tmp_path / "not_a_zip.zip"
    not_a_zip.write_text("this is not a zip file", encoding="utf-8")
    out_prefix = str(tmp_path / "out")
    args = _make_build_args(
        tmp_path, matched=str(matched_path), static=str(not_a_zip), out_prefix=out_prefix
    )

    result = _cmd_build(args)

    assert result == 1
    captured = capsys.readouterr()
    assert "not a valid zip file" in captured.err


# ---------------------------------------------------------------------------
# --help epilogs (FA-4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", ["record", "match", "build"])
def test_help_epilogs_contain_example_command(command, capsys):
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([command, "--help"])

    captured = capsys.readouterr()
    assert "py -m family_a.cli" in captured.out
