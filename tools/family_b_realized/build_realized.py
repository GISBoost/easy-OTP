"""Build a realized (P50 / P85) GTFS for one agency from archived GTFS-RT TripUpdates.

Wraps the RT-3 "Family B" engine (`easy_otp.core.gtfsrt_realizer`) — segment-based
percentile aggregation, Braga et al. (2023) — with a CLI that:

  1. reads one or more archived `polish_trains_updates_<service-day>.pb[.gz]` snapshots
     of the mkuran.pl Polish national rail TripUpdates aggregate,
  2. keeps only the `trip_update` entities for the fully-elapsed service day(s) that
     snapshot represents (excludes the partial "today" the same file also carries — the
     substitute for the JSON-only `confirmed` flag the protobuf form drops),
  3. filters the static GTFS to a single agency (`LKA` by default),
  4. runs the engine and writes `<out-prefix>_p50.zip` / `<out-prefix>_p85.zip`.

This is NOT a QGIS Processing algorithm. Standalone, throwaway venv, never runs in QGIS
(uses `gtfs-realtime-bindings` — allowed for `tools/`, same as `rt_diagnose`).

Why ŁKA needs its own path (not `family_a_reconstruction`, not the dashboard):
see easy-R5/docs/notes/realized-gtfs-lka-tripupdates.md and docs/reference/RT-3_realized-gtfs-notes.md.

Usage:
    py build_realized.py --snapshots ./raw --static polish_trains.zip \\
        --agency-id LKA --out-prefix ./out/lka_realized_2026-09-08

    # explicit day set instead of deriving from filenames:
    py build_realized.py --snapshots ./raw --static polish_trains.zip \\
        --service-days 2026-09-08,2026-09-09 --out-prefix ./out/lka_realized
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import pathlib
import re
import sys
import tempfile
import zipfile

# The engine lives in the plugin package but imports no QGIS — see the note in
# easy_otp/core/gtfsrt_realizer.py ("No QGIS / GDAL imports"). Import it directly
# rather than copying, so there is one source of truth for the reconstruction logic.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from easy_otp.core.gtfsrt_realizer import (  # noqa: E402
    aggregate_segments,
    collect_segment_times,
    decode_snapshot,
    load_static_index,
    rebuild_stop_times,
    repackage_gtfs,
)

_DAY_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_DAY_COMPACT_RE = re.compile(r"^\d{8}$")


def _read_bytes(path: pathlib.Path) -> bytes:
    if path.suffix == ".gz":
        with gzip.open(path, "rb") as fh:
            return fh.read()
    return path.read_bytes()


def _service_day_from_name(path: pathlib.Path) -> str | None:
    m = _DAY_RE.search(path.name)
    return m.group(1) if m else None


def _filter_feed_to_days(
    raw: bytes, days: set[str], out_pb: pathlib.Path, keep_trip_ids: set[str] | None = None
) -> tuple[int, int]:
    """Keep only trip_update entities whose trip.start_date is in `days` (and, if
    `keep_trip_ids` is given, whose trip_id is in that set).

    `days` holds 'YYYY-MM-DD'; the feed's start_date is compact 'YYYYMMDD'.
    Returns (kept, dropped).
    """
    feed = decode_snapshot(raw)
    want = {d.replace("-", "") for d in days}
    kept = [
        e
        for e in feed.entity
        if e.HasField("trip_update")
        and e.trip_update.trip.start_date in want
        and (keep_trip_ids is None or e.trip_update.trip.trip_id in keep_trip_ids)
    ]
    dropped = len(feed.entity) - len(kept)
    del feed.entity[:]
    feed.entity.extend(kept)
    out_pb.write_bytes(feed.SerializeToString())
    return len(kept), dropped


def _filter_static_by_agency(src_zip: str, agency_id: str, out_zip: pathlib.Path) -> int:
    """Write a static GTFS keeping only `agency_id`'s routes/trips/stop_times/calendars.

    stops.txt, agency.txt, shapes.txt and any other members are copied whole — a
    harmless superset for routing. Returns the number of trips kept.
    """
    with zipfile.ZipFile(src_zip) as zin:
        names = set(zin.namelist())

        def rows(member: str) -> tuple[list[str], list[dict]]:
            with zin.open(member) as fh:
                r = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig"))
                return list(r.fieldnames or []), list(r)

        rt_fields, routes = rows("routes.txt")
        # routes.txt agency_id may be blank when the feed has a single agency; keep those too.
        keep_route_ids = {
            row["route_id"]
            for row in routes
            if row.get("agency_id", "") in ("", agency_id)
        }
        if not any(row.get("agency_id") == agency_id for row in routes):
            # No route explicitly names the agency — treat the whole feed as that agency.
            keep_route_ids = {row["route_id"] for row in routes}

        tr_fields, trips = rows("trips.txt")
        keep_trips = [row for row in trips if row["route_id"] in keep_route_ids]
        keep_trip_ids = {row["trip_id"] for row in keep_trips}
        keep_service_ids = {row["service_id"] for row in keep_trips}

        st_fields, stop_times = rows("stop_times.txt")
        keep_stop_times = [row for row in stop_times if row["trip_id"] in keep_trip_ids]
        keep_routes = [row for row in routes if row["route_id"] in keep_route_ids]

        def write_csv(zout: zipfile.ZipFile, member: str, fields: list[str], data: list[dict]) -> None:
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=fields, lineterminator="\r\n", extrasaction="ignore")
            w.writeheader()
            w.writerows(data)
            zout.writestr(member, buf.getvalue().encode("utf-8"))

        with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zout:
            write_csv(zout, "routes.txt", rt_fields, keep_routes)
            write_csv(zout, "trips.txt", tr_fields, keep_trips)
            write_csv(zout, "stop_times.txt", st_fields, keep_stop_times)
            for member in ("calendar.txt", "calendar_dates.txt"):
                if member in names:
                    f, data = rows(member)
                    write_csv(zout, member, f, [r for r in data if r["service_id"] in keep_service_ids])
            for member in names:
                if member in {"routes.txt", "trips.txt", "stop_times.txt", "calendar.txt", "calendar_dates.txt"}:
                    continue
                zout.writestr(member, zin.read(member))

    return len(keep_trip_ids)


def _sniff_gtfs(zip_path: pathlib.Path) -> list[str]:
    required = {"agency.txt", "stops.txt", "routes.txt", "trips.txt", "stop_times.txt"}
    with zipfile.ZipFile(zip_path) as zf:
        bad = zf.testzip()
        if bad is not None:
            return [f"CRC error in {bad}"]
        base = {n.rsplit("/", 1)[-1] for n in zf.namelist()}
    missing = sorted(required - base)
    if not ({"calendar.txt", "calendar_dates.txt"} & base):
        missing.append("calendar.txt|calendar_dates.txt")
    return missing


def _check_monotonic(zip_path: pathlib.Path) -> int:
    """Return the count of trips with a non-decreasing-time violation (should be 0)."""
    def sec(s: str) -> int:
        h, m, ss = s.split(":")
        return int(h) * 3600 + int(m) * 60 + int(ss)

    per_trip: dict[str, list[tuple[int, int, int]]] = {}
    with zipfile.ZipFile(zip_path) as zf, zf.open("stop_times.txt") as fh:
        for row in csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig")):
            a = row.get("arrival_time") or row.get("departure_time")
            d = row.get("departure_time") or row.get("arrival_time")
            if not a:
                continue
            per_trip.setdefault(row["trip_id"], []).append(
                (int(row["stop_sequence"]), sec(a), sec(d))
            )
    violations = 0
    for stops in per_trip.values():
        stops.sort()
        clock = -1
        for _seq, a, d in stops:
            if a < clock or d < a:
                violations += 1
                break
            clock = d
    return violations


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snapshots", required=True, type=pathlib.Path,
                    help="folder of polish_trains_updates_<day>.pb[.gz] snapshots")
    ap.add_argument("--static", required=True,
                    help="matching static GTFS zip (e.g. polish_trains.zip)")
    ap.add_argument("--service-days", default="",
                    help="comma-separated YYYY-MM-DD; default: derive one day per snapshot from its filename")
    ap.add_argument("--agency-id", default="LKA",
                    help="restrict the static feed and output to this agency_id (blank = no filter)")
    ap.add_argument("--out-prefix", required=True, type=pathlib.Path,
                    help="writes <prefix>_p50.zip and <prefix>_p85.zip")
    args = ap.parse_args(argv)

    snaps = sorted(
        p for p in args.snapshots.iterdir()
        if p.suffix in (".pb", ".gz") and "updates" in p.name
    )
    if not snaps:
        ap.error(f"no polish_trains_updates_*.pb[.gz] snapshots under {args.snapshots}")

    explicit_days = {d.strip() for d in args.service_days.split(",") if d.strip()}
    args.out_prefix.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)

        if args.agency_id:
            static_zip = tdp / "static_filtered.zip"
            n_trips = _filter_static_by_agency(args.static, args.agency_id, static_zip)
            static_zip = str(static_zip)
            print(f"[static] {args.agency_id}: {n_trips} trips kept from {args.static}")
        else:
            static_zip = args.static
            n_trips = -1

        idx = load_static_index(static_zip)

        filtered_pbs: list[pathlib.Path] = []
        all_days: set[str] = set()
        for snap in snaps:
            days = explicit_days or {d for d in [_service_day_from_name(snap)] if d}
            if not days:
                print(f"[skip] {snap.name}: no service day given and none in filename")
                continue
            all_days |= days
            out_pb = tdp / f"snapshot_{sorted(days)[0].replace('-', '')}-000000.pb"
            kept, dropped = _filter_feed_to_days(
                _read_bytes(snap), days, out_pb, keep_trip_ids=idx.all_trip_ids
            )
            filtered_pbs.append(out_pb)
            print(f"[snapshot] {snap.name} -> days {sorted(days)}: {kept} trip_updates kept, {dropped} dropped")

        if not filtered_pbs:
            ap.error("no usable snapshots after filtering")

        seg, canceled, skipped = collect_segment_times(
            filtered_pbs, idx, matching_mode="TRIP_ID", segment_source_mode="PER_MESSAGE",
        )
        n_obs = sum(len(v) for v in seg.values())
        print(f"[collect] {len(seg)} segments, {n_obs} observations, "
              f"{len(canceled)} canceled trips, {skipped} pairs skipped")

        p50, p85 = aggregate_segments(seg)

        out_p50 = args.out_prefix.with_name(args.out_prefix.name + "_p50.zip")
        out_p85 = args.out_prefix.with_name(args.out_prefix.name + "_p85.zip")
        for tag, stats, out in (("p50", p50, out_p50), ("p85", p85, out_p85)):
            corr, corrected, gaps = rebuild_stop_times(idx, stats, matching_mode="TRIP_ID")
            repackage_gtfs(static_zip, str(out), corr)
            total = corrected + gaps
            pct = 100.0 * corrected / total if total else 0.0
            miss = _sniff_gtfs(out)
            mono = _check_monotonic(out)
            status = "OK" if not miss and mono == 0 else f"PROBLEM missing={miss} monotonic_violations={mono}"
            print(f"[{tag}] {out.name}: {corrected}/{total} segments corrected ({pct:.1f}%), "
                  f"{gaps} kept scheduled -- {status}")

    print(f"[done] service days: {sorted(all_days)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
