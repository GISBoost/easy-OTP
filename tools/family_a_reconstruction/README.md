# family_a_reconstruction — Family A GTFS reconstruction from vehicle positions

Standalone tool — **not part of the easy-OTP QGIS plugin** and never imported by it. It
reconstructs an observed GTFS from GTFS-RT **VehiclePositions** for cities that publish no
`TripUpdates` feed (Warszawa, Wrocław, Łódź — see easy-OTP's `KNOWN_ISSUES.md` #13). This is
the position-based map-matching + interpolation method ("Family A": Wessel, Allen & Farber
2017 *retro-gtfs*; rt2gtfs, Chen & Botta 2026), distinct from easy-OTP's RT-3
(`BuildRealizedGtfs`), which is a segment-based ("Family B") approach for cities that do have
`TripUpdates`.

Because this tool needs `numpy`/`pandas`/`gtfs-realtime-bindings`, it gets its own throwaway
venv — same precedent as `tools/rt_diagnose` — and is never run inside QGIS.

## Status

- `record` — implemented (FA-1).
- `match` — implemented (FA-2).
- `build` — not implemented yet (FA-3).
- Full end-to-end worked example — FA-4.

## Setup (Windows)

```bat
cd tools/family_a_reconstruction
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Usage — record

Polls a VehiclePositions feed at a fixed interval and archives the raw, unmodified `.pb` bytes
of each poll, plus a `recording.json` manifest.

```bat
python -m family_a.cli record --url https://mkuran.pl/gtfs/warsaw/vehicles.pb --out-dir .\out --duration-min 60 --interval-sec 60
```

- `--url` (required) — VehiclePositions `.pb` feed URL.
- `--out-dir` (required) — directory to write snapshots into (created if missing).
- `--duration-min` (default `60`) — total recording duration in minutes.
- `--interval-sec` (default `60`) — polling interval in seconds.

Output: one `snapshot_YYYYmmdd-HHMMSS.pb` file per successful poll, plus `recording.json`
describing the session (URL, interval, start/stop times, snapshot/failure counts, total
bytes). A 200 response with an empty body is still written as a 0-byte `.pb` file — this
matches RT-2's recorder behaviour and is worth knowing since VehiclePositions feeds are less
commonly exercised than TripUpdates.

Press **Ctrl+C** to stop early — the loop exits cleanly and `recording.json` is still written
for the partial archive.

There is no per-session subfolder: running `record` twice into the same `--out-dir` silently
overwrites the previous `recording.json` (snapshot files themselves are timestamped and never
collide unless run twice in the same second). Use a fresh `--out-dir` per recording session.

## Usage — match

Map-matches the `VehiclePosition` observations in an FA-1 archive onto the static GTFS's
shapes, producing a `(trip_id, timestamp, distance_along_shape_m)` series — the input FA-3
will later use to interpolate stop-crossing times.

```bat
py -m family_a.cli match --positions-dir .\out --static warsaw.zip --out matched.csv
```

- `--positions-dir` (required) — an FA-1 archive directory (`snapshot_*.pb` files).
- `--static` (required) — static GTFS `.zip` path.
- `--out` (required) — output table path. Written as CSV by default; if the path ends in
  `.parquet`, written as Parquet instead (requires `pyarrow` or `fastparquet` — not in
  `requirements.txt` by default, to avoid an extra dependency for the common case; install one
  yourself if you want Parquet output).
- `--max-perpendicular-dist-m` (default `100`) — observations projected farther than this from
  the matched shape are rejected (likely off-route, GPS error, or a `trip_id` that resolves to
  the wrong shape).

Output columns: `trip_id`, `timestamp` (UTC; pandas' default tz-aware format in CSV, e.g.
`2026-07-05 20:37:26+00:00` — space-separated, not literal ISO-8601 `T`-separated),
`distance_along_shape_m`, `perpendicular_dist_m`.

**Known limitation:** `distance_along_shape_m` is not guaranteed to be strictly increasing
over time for a given trip. When a route passes close to itself (a loop, a nearby parallel
carriageway, a layover), GPS noise can flip the nearest-match point between two locations that
are geometrically close but far apart along the route, producing a small backward jump even
though the position stayed genuinely close to the route the whole time (low
`perpendicular_dist_m`). Observed on ~9% of trips in a manual test against a real Warszawa
archive. This is an inherent limitation of simple nearest-segment matching without
trajectory-continuity awareness, not a bug — see `family_a/matcher.py`'s module docstring.
FA-3's interpolation step must not assume this series is strictly monotonic.

The command prints a summary of snapshots processed, observations matched, and observations
rejected broken down by reason (`unknown_shape`, `too_far_from_route`, `no_trip_id`,
`corrupt_snapshot`).

**Fallback when `shapes.txt` is missing:** some GTFS feeds omit `shapes.txt` entirely. In that
case `match` falls back to a straight-line shape built from each trip's own stop sequence
(`stops.txt`/`stop_times.txt`) — a documented accuracy degradation, not an error. A warning is
printed when this happens, and the summary's "Fallback shapes used" line reflects it.

## build

Not implemented yet (FA-3). Running it prints a clear message and exits with a non-zero
status:

```bat
py -m family_a.cli build
```

## Tests

```bat
cd tools/family_a_reconstruction
pytest tests/ -q
```

Runs entirely inside this tool's own venv — pure stdlib + pytest, no network, no QGIS. Not
wired into `easy_otp/test`.
