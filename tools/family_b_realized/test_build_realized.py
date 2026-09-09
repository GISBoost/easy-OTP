"""Self-check for build_realized.py — run: py test_build_realized.py

Builds a tiny in-memory static GTFS (agency LKA, 1 route, 2 trips, 4 stops) and a
synthetic GTFS-RT FeedMessage with known confirmed times, runs the full pipeline,
and asserts the output feed is a valid GTFS with monotonic, corrected times and
p85 >= p50. No pytest, no fixtures.
"""

from __future__ import annotations

import csv
import io
import pathlib
import sys
import tempfile
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from google.transit import gtfs_realtime_pb2  # noqa: E402

import build_realized as br  # noqa: E402

_STOPS = [("s1", "Stop 1"), ("s2", "Stop 2"), ("s3", "Stop 3"), ("s4", "Stop 4")]
# scheduled: 3 min between stops, 0 dwell, first trip 08:00, second 08:30
_SCHED = {
    "t1": [("s1", "08:00:00"), ("s2", "08:03:00"), ("s3", "08:06:00"), ("s4", "08:09:00")],
    "t2": [("s1", "08:30:00"), ("s2", "08:33:00"), ("s3", "08:36:00"), ("s4", "08:39:00")],
}


def _make_static(path: pathlib.Path) -> None:
    def w(zf, name, header, rows):
        buf = io.StringIO()
        cw = csv.writer(buf, lineterminator="\r\n")
        cw.writerow(header)
        cw.writerows(rows)
        zf.writestr(name, buf.getvalue())

    with zipfile.ZipFile(path, "w") as zf:
        w(zf, "agency.txt", ["agency_id", "agency_name", "agency_url", "agency_timezone"],
          [["LKA", "Test rail", "https://example.org", "Europe/Warsaw"]])
        w(zf, "stops.txt", ["stop_id", "stop_name"], [[sid, name] for sid, name in _STOPS])
        w(zf, "routes.txt", ["route_id", "agency_id", "route_short_name", "route_type"],
          [["R1", "LKA", "R1", "2"]])
        w(zf, "trips.txt", ["route_id", "service_id", "trip_id"],
          [["R1", "SVC", "t1"], ["R1", "SVC", "t2"]])
        rows = []
        for tid, stops in _SCHED.items():
            for i, (sid, t) in enumerate(stops):
                rows.append([tid, t, t, sid, i])
        w(zf, "stop_times.txt",
          ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"], rows)
        w(zf, "calendar_dates.txt", ["service_id", "date", "exception_type"],
          [["SVC", "20260908", "1"]])


def _make_feed(path: pathlib.Path) -> None:
    """Both trips run the s2->s3 segment 120 s slower than scheduled (180 -> 300)."""
    fm = gtfs_realtime_pb2.FeedMessage()
    fm.header.gtfs_realtime_version = "2.0"
    fm.header.timestamp = 1_788_900_000
    realized = {
        "t1": [0, 180, 420, 600],   # cumulative sec from 08:00:00; s2->s3 is 240s here
        "t2": [0, 180, 420, 600],
    }
    base = {"t1": 8 * 3600, "t2": 8 * 3600 + 1800}
    for tid, offs in realized.items():
        ent = fm.entity.add()
        ent.id = tid
        tu = ent.trip_update
        tu.trip.trip_id = tid
        tu.trip.start_date = "20260908"
        for seq, off in enumerate(offs):
            stu = tu.stop_time_update.add()
            stu.stop_sequence = seq
            epoch = 1_788_800_000 + base[tid] + off
            stu.arrival.time = epoch
            stu.departure.time = epoch
    path.write_bytes(fm.SerializeToString())


def _times(zip_path: pathlib.Path, trip_id: str) -> list[tuple[int, str, str]]:
    out = []
    with zipfile.ZipFile(zip_path) as zf, zf.open("stop_times.txt") as fh:
        for row in csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig")):
            if row["trip_id"] == trip_id:
                out.append((int(row["stop_sequence"]), row["arrival_time"], row["departure_time"]))
    return sorted(out)


def _sec(s: str) -> int:
    h, m, ss = s.split(":")
    return int(h) * 3600 + int(m) * 60 + int(ss)


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        _make_static(tdp / "static.zip")
        (tdp / "raw").mkdir()
        _make_feed(tdp / "raw" / "polish_trains_updates_2026-09-08.pb")

        rc = br.main([
            "--snapshots", str(tdp / "raw"),
            "--static", str(tdp / "static.zip"),
            "--service-days", "2026-09-08",
            "--agency-id", "LKA",
            "--out-prefix", str(tdp / "out" / "test_realized"),
        ])
        assert rc == 0

        p50 = tdp / "out" / "test_realized_p50.zip"
        p85 = tdp / "out" / "test_realized_p85.zip"
        assert p50.exists() and p85.exists()

        assert br._sniff_gtfs(p50) == [], br._sniff_gtfs(p50)
        assert br._check_monotonic(p50) == 0
        assert br._check_monotonic(p85) == 0

        t1_50 = _times(p50, "t1")
        # first stop unchanged (anchored to scheduled departure)
        assert t1_50[0][1] == "08:00:00", t1_50
        # s1->s2 observed == scheduled (180s) -> arrival at s2 still 08:03:00
        assert t1_50[1][1] == "08:03:00", t1_50
        # s2->s3 observed 240s (both trips) -> arrival 08:03:00 + 240 = 08:07:00, not 08:06:00
        assert t1_50[2][1] == "08:07:00", t1_50
        # s3->s4 observed == scheduled 180s -> 08:10:00
        assert t1_50[3][1] == "08:10:00", t1_50

        t1_85 = _times(p85, "t1")
        for (_s, a50, _d50), (_s2, a85, _d85) in zip(t1_50, t1_85):
            assert _sec(a85) >= _sec(a50), (a50, a85)

    print("OK - build_realized self-check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
