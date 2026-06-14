#!/usr/bin/env python3
"""compare_rt_vs_static.py — diagnose why OTP applies 0 GTFS-RT trip updates.

Standalone ANALYSIS tooling. NOT part of the easy-OTP plugin and never imported
by it: it may use a third-party dep (gtfs-realtime-bindings) that the plugin's
no-pip / protobuf-gating rules forbid inside QGIS. Run it in a throwaway venv.

Usage:
    py tools/rt_diagnose/compare_rt_vs_static.py TRIP_UPDATES_PB STATIC_GTFS_ZIP

Prints field-presence stats, trip_id/route_id overlap, and a VERDICT:
    EXACT-MATCH POSSIBLE | FUZZY POSSIBLE | NEITHER
Exit code is always 0 (read the VERDICT line).
"""
from __future__ import annotations

import csv
import io
import sys
import zipfile
from collections import Counter

try:
    from google.transit import gtfs_realtime_pb2
except ImportError:
    sys.exit(
        "Missing dependency. In a throwaway venv run:\n"
        "  py -m venv .venv\n"
        "  .venv\\Scripts\\activate        (Windows)  /  source .venv/bin/activate (*nix)\n"
        "  pip install gtfs-realtime-bindings\n"
    )


def load_pb(path):
    msg = gtfs_realtime_pb2.FeedMessage()
    with open(path, "rb") as fh:
        msg.ParseFromString(fh.read())
    return msg


def analyse_pb(msg):
    """Return (trip_ids, route_ids, field-presence Counter, n_trip_updates)."""
    trip_ids, route_ids, present, n = set(), set(), Counter(), 0
    for ent in msg.entity:
        if not ent.HasField("trip_update"):
            continue
        n += 1
        td = ent.trip_update.trip
        if td.HasField("trip_id") and td.trip_id:
            trip_ids.add(td.trip_id)
            present["trip_id"] += 1
        if td.HasField("route_id") and td.route_id:
            route_ids.add(td.route_id)
            present["route_id"] += 1
        if td.HasField("direction_id"):
            present["direction_id"] += 1
        if td.HasField("start_time") and td.start_time:
            present["start_time"] += 1
        if td.HasField("start_date") and td.start_date:
            present["start_date"] += 1
    return trip_ids, route_ids, present, n


def read_zip_column(zf, filename, column):
    """Return the set of values in `column` of a CSV member, or an empty set."""
    try:
        raw = zf.read(filename)
    except KeyError:
        return set()
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig", errors="replace")))
    if not reader.fieldnames or column not in reader.fieldnames:
        return set()
    return {row[column].strip() for row in reader if row.get(column)}


def sample(values, k=5):
    return sorted(values)[:k]


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    pb_path, zip_path = sys.argv[1], sys.argv[2]

    rt_trips, rt_routes, present, n = analyse_pb(load_pb(pb_path))
    with zipfile.ZipFile(zip_path) as zf:
        static_trips = read_zip_column(zf, "trips.txt", "trip_id")
        static_routes = read_zip_column(zf, "routes.txt", "route_id")

    print("=" * 72)
    print(f"GTFS-RT file : {pb_path}")
    print(f"Static GTFS  : {zip_path}")
    print("=" * 72)
    print(f"\nTripUpdates in feed: {n}")
    print(f"TripDescriptor field presence (out of {n} TripUpdates):")
    for field in ("trip_id", "route_id", "direction_id", "start_time", "start_date"):
        print(f"  {field:<13}: {present.get(field, 0)}")

    trip_overlap = rt_trips & static_trips
    route_overlap = rt_routes & static_routes

    print(f"\nRT trip_ids : {len(rt_trips)} unique   static trip_ids: {len(static_trips)}")
    print(f"  overlap        : {len(trip_overlap)}")
    print(f"  RT sample      : {sample(rt_trips)}")
    print(f"  static sample  : {sample(static_trips)}")
    print(f"\nRT route_ids: {len(rt_routes)} unique   static route_ids: {len(static_routes)}")
    print(f"  overlap        : {len(route_overlap)}")
    print(f"  RT sample      : {sample(rt_routes)}")
    print(f"  static sample  : {sample(static_routes)}")

    trip_ratio = len(trip_overlap) / len(rt_trips) if rt_trips else 0.0
    route_ratio = len(route_overlap) / len(rt_routes) if rt_routes else 0.0
    has_fuzzy_keys = present.get("route_id", 0) > 0 and present.get("start_time", 0) > 0

    print("\n" + "=" * 72)
    if trip_ratio >= 0.5:
        print("VERDICT: EXACT-MATCH POSSIBLE")
        print("  trip_ids overlap strongly -> this static edition matches the live feed.")
        print("  Load THIS static GTFS in RunRealtimeAccessibility. Fuzzy not needed.")
    elif route_ratio >= 0.5 and has_fuzzy_keys:
        print("VERDICT: FUZZY POSSIBLE")
        print("  trip_ids differ, but route_ids overlap and the .pb carries route_id +")
        print("  start_time -> OTP 1.5 fuzzyTripMatching can resolve trips. Ensure the flag")
        print("  is written on the stop-time-updater block (see A2).")
    else:
        print("VERDICT: NEITHER  (RT-1 infeasible for this pairing)")
        if not has_fuzzy_keys:
            print("  - the .pb lacks route_id/start_time -> fuzzy matching cannot work.")
        if route_ratio < 0.5:
            print("  - route_ids do not overlap -> static and RT are unrelated editions.")
        print("  Pivot to RT-2 RecordGtfsRt + RT-3 BuildRealizedGtfs (PRD).")
    print("=" * 72)


if __name__ == "__main__":
    main()
