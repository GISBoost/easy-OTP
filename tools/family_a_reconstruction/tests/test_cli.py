"""Unit tests for family_a.cli (FA-1).

No QGIS, no network, no real wall-clock waits (time.monotonic/time.sleep are
mocked so the record loop runs instantly). Run: pytest tests/test_cli.py -v
"""

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import argparse

import pytest
from google.transit import gtfs_realtime_pb2

from family_a.cli import _cmd_build, _cmd_match, _cmd_record, _duration_minutes, build_parser
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
    assert args.time_bucket_minutes == 120


def test_match_defaults():
    parser = build_parser()
    args = parser.parse_args(
        ["match", "--positions-dir", "pos", "--static", "gtfs.zip", "--out", "out.csv"]
    )
    assert args.func is _cmd_match
    assert args.max_perpendicular_dist_m == 100.0
    assert args.positions_dir == ["pos"]


def test_match_positions_dir_accepts_multiple_values():
    parser = build_parser()
    args = parser.parse_args(
        ["match", "--positions-dir", "day1", "day2", "--static", "gtfs.zip", "--out", "out.csv"]
    )
    assert args.positions_dir == ["day1", "day2"]


def test_match_positions_dir_accumulates_across_repeated_flag():
    """Plain nargs="+" with argparse's default 'store' action would silently
    OVERWRITE on a second --positions-dir occurrence, dropping the first
    directory with no error - the exact repeated-flag style shown in FA-6's
    own acceptance criteria must accumulate instead.
    """
    parser = build_parser()
    args = parser.parse_args(
        ["match", "--positions-dir", "day1", "--positions-dir", "day2", "--static", "gtfs.zip", "--out", "out.csv"]
    )
    assert args.positions_dir == ["day1", "day2"]


def test_match_positions_dir_accumulates_mixed_repeated_and_multi_value():
    parser = build_parser()
    args = parser.parse_args(
        ["match", "--positions-dir", "day1", "--positions-dir", "day2", "day3",
         "--static", "gtfs.zip", "--out", "out.csv"]
    )
    assert args.positions_dir == ["day1", "day2", "day3"]


@pytest.mark.parametrize("flag", ["--duration-min", "--interval-sec"])
def test_non_positive_values_rejected(flag):
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["record", "--url", "http://x", "--out-dir", "out", flag, "0"])


# ---------------------------------------------------------------------------
# _duration_minutes (FA-6)
# ---------------------------------------------------------------------------


def test_duration_minutes_direct_boundary_1500_ok():
    assert _duration_minutes("1500") == 1500


def test_duration_minutes_direct_1501_rejected():
    with pytest.raises(argparse.ArgumentTypeError):
        _duration_minutes("1501")


def test_duration_minutes_direct_zero_rejected():
    with pytest.raises(argparse.ArgumentTypeError):
        _duration_minutes("0")


def test_record_duration_min_over_cap_rejected_readable_message(capsys):
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["record", "--url", "http://x", "--out-dir", "out", "--duration-min", "1501"])
    captured = capsys.readouterr()
    assert "1500" in captured.err
    assert "match" in captured.err


def test_record_duration_min_1600_full_parser_nonzero_exit():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["record", "--url", "http://x", "--out-dir", "out", "--duration-min", "1600"])


def test_record_duration_min_1500_accepted():
    parser = build_parser()
    args = parser.parse_args(["record", "--url", "http://x", "--out-dir", "out", "--duration-min", "1500"])
    assert args.duration_min == 1500


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


def _write_snapshot(tmp_path, *, filename="snapshot_20260101-000000.pb", trip_id="trip1", timestamp=1_700_000_000, entity_id="e1"):
    feed = gtfs_realtime_pb2.FeedMessage(
        header=gtfs_realtime_pb2.FeedHeader(gtfs_realtime_version="2.0"),
        entity=[
            gtfs_realtime_pb2.FeedEntity(
                id=entity_id,
                vehicle=gtfs_realtime_pb2.VehiclePosition(
                    trip=gtfs_realtime_pb2.TripDescriptor(trip_id=trip_id),
                    position=gtfs_realtime_pb2.Position(latitude=0.005, longitude=0.0),
                    timestamp=timestamp,
                ),
            )
        ],
    )
    path = tmp_path / filename
    path.write_bytes(feed.SerializeToString())
    return path


def _make_match_args(tmp_path, **overrides):
    class _NS:
        positions_dir = [str(tmp_path)]
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
    header = content.splitlines()[0]
    assert "recording_date" in header
    assert "2026-01-01" in content

    captured = capsys.readouterr()
    assert "Snapshots processed" in captured.out
    assert "Observations matched" in captured.out
    assert "Directories merged: 1" in captured.out
    assert "Recording date range: 2026-01-01 to 2026-01-01" in captured.out


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
# _cmd_match multi-directory merge (FA-6)
# ---------------------------------------------------------------------------


def test_cmd_match_merges_two_directories_with_recording_date(tmp_path, capsys):
    gtfs = _make_gtfs_zip(tmp_path)
    day1_dir = tmp_path / "day1"
    day2_dir = tmp_path / "day2"
    day1_dir.mkdir()
    day2_dir.mkdir()
    _write_snapshot(day1_dir, filename="snapshot_20260101-000000.pb", entity_id="e1")
    _write_snapshot(day2_dir, filename="snapshot_20260102-000000.pb", entity_id="e2")
    out_path = tmp_path / "matched.csv"

    args = _make_match_args(
        tmp_path, positions_dir=[str(day1_dir), str(day2_dir)], static=str(gtfs), out=str(out_path)
    )
    result = _cmd_match(args)

    assert result == 0
    content = out_path.read_text(encoding="utf-8")
    assert "2026-01-01" in content
    assert "2026-01-02" in content
    # 2 total matched observations (one snapshot per day, each with one vehicle).
    assert len(content.splitlines()) == 3  # header + 2 rows

    captured = capsys.readouterr()
    assert "Directories merged: 2" in captured.out
    assert "Recording date range: 2026-01-01 to 2026-01-02" in captured.out
    assert "Observations matched: 2" in captured.out


def test_cmd_match_reject_counts_summed_across_directories(tmp_path, capsys):
    gtfs = _make_gtfs_zip(tmp_path)
    day1_dir = tmp_path / "day1"
    day2_dir = tmp_path / "day2"
    day1_dir.mkdir()
    day2_dir.mkdir()
    # day1: a valid observation for trip1.
    _write_snapshot(day1_dir, filename="snapshot_20260101-000000.pb", trip_id="trip1", entity_id="e1")
    # day2: a vehicle with no trip_id at all -> counted as no_trip_id reject.
    feed = gtfs_realtime_pb2.FeedMessage(
        header=gtfs_realtime_pb2.FeedHeader(gtfs_realtime_version="2.0"),
        entity=[
            gtfs_realtime_pb2.FeedEntity(
                id="e2",
                vehicle=gtfs_realtime_pb2.VehiclePosition(
                    position=gtfs_realtime_pb2.Position(latitude=0.005, longitude=0.0),
                    timestamp=1_700_000_000,
                ),
            )
        ],
    )
    (day2_dir / "snapshot_20260102-000000.pb").write_bytes(feed.SerializeToString())
    out_path = tmp_path / "matched.csv"

    args = _make_match_args(
        tmp_path, positions_dir=[str(day1_dir), str(day2_dir)], static=str(gtfs), out=str(out_path)
    )
    result = _cmd_match(args)

    assert result == 0
    captured = capsys.readouterr()
    assert "Observations rejected: 1" in captured.out
    assert "  - no_trip_id: 1" in captured.out


def test_cmd_match_empty_directory_in_multi_dir_call_returns_1_named(tmp_path, capsys):
    gtfs = _make_gtfs_zip(tmp_path)
    day1_dir = tmp_path / "day1"
    day2_dir = tmp_path / "day2_empty"
    day1_dir.mkdir()
    day2_dir.mkdir()
    _write_snapshot(day1_dir, filename="snapshot_20260101-000000.pb")
    out_path = tmp_path / "matched.csv"

    args = _make_match_args(
        tmp_path, positions_dir=[str(day1_dir), str(day2_dir)], static=str(gtfs), out=str(out_path)
    )
    result = _cmd_match(args)

    assert result == 1
    captured = capsys.readouterr()
    assert str(day2_dir) in captured.err
    assert "No snapshot_*.pb files found" in captured.err
    assert not out_path.exists()


def test_cmd_match_recording_date_from_earliest_snapshot_not_directory_name(tmp_path, capsys):
    gtfs = _make_gtfs_zip(tmp_path)
    misleading_dir = tmp_path / "positions_lodz2"
    misleading_dir.mkdir()
    _write_snapshot(misleading_dir, filename="snapshot_20260615-120000.pb")
    out_path = tmp_path / "matched.csv"

    args = _make_match_args(
        tmp_path, positions_dir=[str(misleading_dir)], static=str(gtfs), out=str(out_path)
    )
    result = _cmd_match(args)

    assert result == 0
    content = out_path.read_text(encoding="utf-8")
    assert "2026-06-15" in content


def test_cmd_match_unparseable_snapshot_filename_in_dir_returns_1(tmp_path, capsys):
    gtfs = _make_gtfs_zip(tmp_path)
    bad_dir = tmp_path / "day1"
    bad_dir.mkdir()
    _write_snapshot(bad_dir, filename="snapshot_bogus.pb")
    out_path = tmp_path / "matched.csv"

    args = _make_match_args(tmp_path, positions_dir=[str(bad_dir)], static=str(gtfs), out=str(out_path))
    result = _cmd_match(args)

    assert result == 1
    captured = capsys.readouterr()
    assert str(bad_dir) in captured.err
    assert "snapshot_bogus.pb" in captured.err
    assert not out_path.exists()


def test_cmd_match_duplicate_positions_dir_returns_1(tmp_path, capsys):
    """Giving the same directory twice must fail loudly, not silently
    process it once while still reporting the original (higher) directory
    count in the summary.
    """
    gtfs = _make_gtfs_zip(tmp_path)
    day_dir = tmp_path / "day1"
    day_dir.mkdir()
    _write_snapshot(day_dir, filename="snapshot_20260101-000000.pb")
    out_path = tmp_path / "matched.csv"

    args = _make_match_args(
        tmp_path, positions_dir=[str(day_dir), str(day_dir)], static=str(gtfs), out=str(out_path)
    )
    result = _cmd_match(args)

    assert result == 1
    captured = capsys.readouterr()
    assert str(day_dir) in captured.err
    assert "same directory twice" in captured.err
    assert not out_path.exists()


def test_cmd_match_duplicate_positions_dir_detected_via_different_relative_paths(tmp_path, capsys):
    """Same directory referenced by two different (but equivalent) path
    strings must still be caught - dedup compares resolved paths, not raw
    strings.
    """
    gtfs = _make_gtfs_zip(tmp_path)
    day_dir = tmp_path / "day1"
    day_dir.mkdir()
    _write_snapshot(day_dir, filename="snapshot_20260101-000000.pb")
    out_path = tmp_path / "matched.csv"

    equivalent_path = str(day_dir / ".." / "day1")
    args = _make_match_args(
        tmp_path, positions_dir=[str(day_dir), equivalent_path], static=str(gtfs), out=str(out_path)
    )
    result = _cmd_match(args)

    assert result == 1
    captured = capsys.readouterr()
    assert "same directory twice" in captured.err


# ---------------------------------------------------------------------------
# _cmd_build (FA-3)
# ---------------------------------------------------------------------------


def _make_build_static_zip(tmp_path):
    # service_id + calendar.txt (svc1 active weekdays, 2026-01-01 is a
    # Thursday) so FA-5's day-type gating resolves this trip to a non-empty
    # day_type set instead of degenerating into "always a gap".
    path = tmp_path / "gtfs_build.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "trips.txt",
            "trip_id,route_id,direction_id,shape_id,service_id\nt1,R1,0,shape1,svc1\n",
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
        zf.writestr(
            "calendar.txt",
            "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,"
            "start_date,end_date\n"
            "svc1,1,1,1,1,1,0,0,20260101,20261231\n",
        )
    return path


def _make_build_args(tmp_path, **overrides):
    class _NS:
        matched = None
        static = None
        out_prefix = None
        min_observations_per_segment = 1
        time_bucket_minutes = 120

    ns = _NS()
    for key, value in overrides.items():
        setattr(ns, key, value)
    return ns


def test_cmd_build_end_to_end_writes_both_zips(tmp_path, capsys):
    from family_a.matcher import project_point_to_polyline

    gtfs = _make_build_static_zip(tmp_path)
    d_b = project_point_to_polyline(0.01, 0.0, [(0.0, 0.0), (0.01, 0.0)])[0]

    matched_path = tmp_path / "matched.csv"
    # 07:00:00 UTC = 08:00:00 local Europe/Warsaw (UTC+1, no DST in January) -
    # same bucket as stop_times.txt's scheduled 08:00:00/08:10:00, so this
    # keeps exercising an actual correction post-FA-5, not just a gap.
    matched_path.write_text(
        "trip_id,timestamp,distance_along_shape_m,perpendicular_dist_m\n"
        f"t1,2026-01-01T07:00:00Z,0.0,0.0\n"
        f"t1,2026-01-01T07:00:50Z,{d_b},0.0\n",
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
    assert "Agency timezone resolved" in captured.out
    assert "Trips processed" in captured.out
    assert "Segment observations collected" in captured.out
    assert "interpolation gaps" in captured.out
    assert "rejected (implausible segment time)" in captured.out
    assert "Segments corrected: 1" in captured.out
    assert "P50 output written to" in captured.out
    assert "P85 output written to" in captured.out


def test_cmd_build_reads_matched_csv_produced_by_cmd_match_with_recording_date(tmp_path, capsys):
    """FA-6 end-to-end regression: recording_date survives _cmd_match's CSV
    write as a plain string, and _cmd_build's collect_segment_observations
    call groups by (trip_id, recording_date) using that string value without
    raising - test_segment_stats.py's unit tests feed collect_segment_observations
    pre-built datetime.date objects directly, which does not exercise this
    CSV round-trip. Same 07:00 UTC / 08:00 local Europe/Warsaw alignment as
    test_cmd_build_end_to_end_writes_both_zips, so this exercises an actual
    correction, not just a gap.

    Uses its own static zip (not the shared _make_build_static_zip) with a
    shape extending past stop B, so the second live-recorded position has
    real distance margin beyond B - a shape ending exactly at B makes the
    live position's own map-matched distance and stop_distance_along_shape's
    independently-computed distance for B differ by float noise, which can
    break interpolate_stop_time's bracket by a hair (observed while writing
    this test) - an artifact of this test's own construction, not a defect
    in the reviewed code.
    """
    gtfs_path = tmp_path / "gtfs_build_fa6.zip"
    with zipfile.ZipFile(gtfs_path, "w") as zf:
        zf.writestr(
            "trips.txt",
            "trip_id,route_id,direction_id,shape_id,service_id\nt1,R1,0,shape1,svc1\n",
        )
        zf.writestr("stops.txt", "stop_id,stop_lat,stop_lon\nA,0.0,0.0\nB,0.01,0.0\n")
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
            "shape1,0.01,0.0,1\n"
            "shape1,0.02,0.0,2\n",
        )
        zf.writestr(
            "calendar.txt",
            "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,"
            "start_date,end_date\n"
            "svc1,1,1,1,1,1,0,0,20260101,20261231\n",
        )

    def _feed(trip_id, lat, lon, ts):
        return gtfs_realtime_pb2.FeedMessage(
            header=gtfs_realtime_pb2.FeedHeader(gtfs_realtime_version="2.0"),
            entity=[
                gtfs_realtime_pb2.FeedEntity(
                    id="e1",
                    vehicle=gtfs_realtime_pb2.VehiclePosition(
                        trip=gtfs_realtime_pb2.TripDescriptor(trip_id=trip_id),
                        position=gtfs_realtime_pb2.Position(latitude=lat, longitude=lon),
                        timestamp=ts,
                    ),
                )
            ],
        )

    positions_dir = tmp_path / "recording"
    positions_dir.mkdir()
    ts1 = int(datetime(2026, 1, 1, 7, 0, 0, tzinfo=timezone.utc).timestamp())
    ts2 = int(datetime(2026, 1, 1, 7, 0, 50, tzinfo=timezone.utc).timestamp())
    (positions_dir / "snapshot_20260101-070000.pb").write_bytes(
        _feed("t1", 0.0, 0.0, ts1).SerializeToString()
    )
    (positions_dir / "snapshot_20260101-070050.pb").write_bytes(
        _feed("t1", 0.015, 0.0, ts2).SerializeToString()  # past B, well within margin
    )

    matched_path = tmp_path / "matched_via_match.csv"
    match_args = _make_match_args(
        tmp_path, positions_dir=[str(positions_dir)], static=str(gtfs_path), out=str(matched_path)
    )
    assert _cmd_match(match_args) == 0
    header = matched_path.read_text(encoding="utf-8").splitlines()[0]
    assert "recording_date" in header

    out_prefix = str(tmp_path / "out_via_match")
    build_args = _make_build_args(
        tmp_path, matched=str(matched_path), static=str(gtfs_path), out_prefix=out_prefix
    )
    result = _cmd_build(build_args)

    assert result == 0
    captured = capsys.readouterr()
    assert "Segments corrected: 1" in captured.out


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
