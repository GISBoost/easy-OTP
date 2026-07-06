# family_a_reconstruction — Family A GTFS reconstruction from vehicle positions

> **This is not a Processing algorithm.** It does not appear in the QGIS Toolbox and cannot
> be run from within QGIS. It requires its own standalone Python environment (see Setup
> below) and is operated entirely from the command line, for advanced users comfortable with
> a terminal.

Standalone tool — **not part of the easy-OTP QGIS plugin** and never imported by it. It
reconstructs an observed GTFS from GTFS-RT **VehiclePositions** for cities that publish no
`TripUpdates` feed (Warszawa, Wrocław, Łódź — see easy-OTP's `KNOWN_ISSUES.md` #13). This is
the position-based map-matching + interpolation method ("Family A": Wessel, Allen & Farber
2017 *retro-gtfs*; rt2gtfs, Chen & Botta 2026), distinct from easy-OTP's RT-3
(`BuildRealizedGtfs`), which is a segment-based ("Family B") approach for cities that do have
`TripUpdates`.

Because this tool needs `numpy`/`pandas`/`gtfs-realtime-bindings`, it gets its own throwaway
venv — same precedent as `tools/rt_diagnose` — and is never run inside QGIS.

**Accuracy expectations:** output quality depends entirely on the input recording — position
density (polling interval, recording duration) and the static feed's `shapes.txt` completeness
(see the fallback note under Usage — match below). This is an approximation method, not a
replacement for Family B (`TripUpdates`-based RT-3) where that is available: it corrects only
the stop-to-stop segments a recording actually observed vehicles crossing; everything else
keeps the original scheduled time.

## Status

All three subcommands are implemented and documented below:

- `record` — implemented (FA-1).
- `match` — implemented (FA-2).
- `build` — implemented (FA-3).
- Full end-to-end worked example and CLI polish — this document (FA-4).

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
py -m family_a.cli record --url https://mkuran.pl/gtfs/warsaw/vehicles.pb --out-dir .\out --duration-min 60 --interval-sec 60
```

- `--url` (required) — VehiclePositions `.pb` feed URL.
- `--out-dir` (required) — directory to write snapshots into (created if missing).
- `--duration-min` (default `60`) — total recording duration in minutes. Must be a positive
  integer; `0` or negative values are rejected at startup (a non-positive interval would
  otherwise turn the inter-poll sleep into a no-op and hammer the remote feed).
- `--interval-sec` (default `60`) — polling interval in seconds. Same positive-integer
  requirement as `--duration-min`.

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
shapes, producing a `(trip_id, timestamp, distance_along_shape_m)` series — the input
`build` uses to interpolate stop-crossing times.

```bat
py -m family_a.cli match --positions-dir .\out --static warsaw.zip --out matched.csv
```

- `--positions-dir` (required) — an FA-1 archive directory (`snapshot_*.pb` files). If it
  contains no `snapshot_*.pb` files, the command exits with a clear error instead of
  producing an empty output table.
- `--static` (required) — static GTFS `.zip` path. Must be a valid zip containing at least
  `trips.txt`; a missing file, a non-zip file, or a zip lacking `trips.txt` all exit with a
  clear error naming the problem, instead of a raw traceback.
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
`build`'s interpolation step does not assume this series is strictly monotonic.

The command prints a summary of snapshots processed, observations matched, and observations
rejected broken down by reason (`unknown_shape`, `too_far_from_route`, `no_trip_id`,
`corrupt_snapshot`).

**Fallback when `shapes.txt` is missing:** some GTFS feeds omit `shapes.txt` entirely. In that
case `match` falls back to a straight-line shape built from each trip's own stop sequence
(`stops.txt`/`stop_times.txt`) — a documented accuracy degradation, not an error. A warning is
printed when this happens, and the summary's "Fallback shapes used" line reflects it.

## Usage — build

Reconstructs a realized GTFS from a `match` table: interpolates each scheduled stop's
crossing time from the matched position series, aggregates observed stop-to-stop travel times
(median/P50 and 85th percentile/P85), and rewrites `stop_times.txt` accordingly. Stop pairs
with too few observations, or none at all, keep the original scheduled times ("gap").

```bat
py -m family_a.cli build --matched matched.csv --static warsaw.zip --out-prefix realized
```

- `--matched` (required) — the table written by `match` (`.csv` or `.parquet`). Must contain
  `trip_id`, `timestamp`, and `distance_along_shape_m` columns; a missing file or a table
  missing any of those columns (e.g. accidentally pointing this at the wrong CSV) exits with
  a clear error naming what's missing, instead of a raw traceback.
- `--static` (required) — the same static GTFS `.zip` used in `match`. Must contain
  `trips.txt`, `stop_times.txt`, and `stops.txt`.
- `--out-prefix` (required) — writes `<out-prefix>_p50.zip` and `<out-prefix>_p85.zip`.
- `--min-observations-per-segment` (default `2`) — stop-to-stop segments observed fewer than
  this many times are dropped (treated as a gap, keeping the scheduled time) rather than
  trusted.

Output: two GTFS zips, byte-identical to the input static feed except for corrected
`arrival_time`/`departure_time` values in `stop_times.txt` (P50 = typical/median observed
travel time per segment, P85 = pessimistic/85th-percentile). The command prints counts for
trips processed/skipped, segments observed/corrected/dropped, interpolation gaps, missing
stop locations, and rejected implausible segment times — use these to judge how much of the
recording actually corrected the schedule versus fell back to planned times.

Family A has no trip-cancellation signal (unlike RT-3, which can read `ScheduleRelationship`
from `TripUpdate`s) — every trip in the static feed is reconstructed, cancelled or not.

**Known limitation — corrections are not scoped to the recording's day or time of day.** A
segment is keyed only by `(route_id, direction_id, from_stop_id, to_stop_id)` — once a segment
passes the `--min-observations-per-segment` threshold, its P50/P85 value is applied to *every*
trip in the static feed that shares that stop pair, regardless of what date or time of day that
trip runs. A static feed commonly spans many weeks of distinct service patterns (`trips.txt`
often has hundreds of thousands of trips across a `calendar_dates.txt`-driven schedule), so a
single recording session's estimate for one segment can end up rewriting that segment's time on
thousands of scheduled trips it never observed. This is more of a risk the shorter the
recording and the closer `--min-observations-per-segment` sits to its default of 2: with only
two observations, one noisy outlier (e.g. a vehicle dwelling at a terminus for its scheduled
recovery time, which `build` cannot distinguish from genuine travel time) can double- or
triple- the resulting P50 for that segment, and that inflated value then propagates to every
matching trip. Mitigate by recording longer sessions (several hours or a full service day) and
by comparing the printed "Segments corrected" against "Segments dropped (fewer than N
observations)" to judge whether the sample actually supports trusting the result before using
it for analysis.

## Worked example — Warszawa, end to end

Warszawa publishes static GTFS and GTFS-RT VehiclePositions, but no TripUpdates feed
(`KNOWN_ISSUES.md` #13 in the main plugin repo) — exactly the case this tool exists for. This
example uses the community mirror at `mkuran.pl`, which needs **no API key** (the official
`api.um.warszawa.pl` VehiclePositions endpoint requires one — see
`docs/reference/RT_test-feeds-by-city.md` in the main repo).

1. **Download the static GTFS** (once, before or during recording):

   ```bat
   curl -o warsaw.zip https://mkuran.pl/gtfs/warsaw.zip
   ```

2. **Record vehicle positions.** Pick a duration long enough to observe most routes at least
   once end to end (an hour is a reasonable start); a shorter interval gives denser position
   data but polls the feed more often — 30–60 s is reasonable and avoids hammering the mirror:

   ```bat
   py -m family_a.cli record --url https://mkuran.pl/gtfs/warsaw/vehicles.pb --out-dir warsaw_recording --duration-min 60 --interval-sec 60
   ```

   Let it run to completion (or Ctrl+C early — the partial archive is still usable).

3. **Map-match the recording** against the static feed:

   ```bat
   py -m family_a.cli match --positions-dir warsaw_recording --static warsaw.zip --out warsaw_matched.csv
   ```

   Check the printed summary: a large `too_far_from_route`/`unknown_shape` count relative to
   `Observations matched` suggests the recording window didn't overlap well with the static
   feed's validity, or `--max-perpendicular-dist-m` needs loosening.

4. **Build the realized GTFS:**

   ```bat
   py -m family_a.cli build --matched warsaw_matched.csv --static warsaw.zip --out-prefix warsaw_realized
   ```

   Produces `warsaw_realized_p50.zip` and `warsaw_realized_p85.zip`.

### Verifying the result

- **GTFS validity:** open `warsaw_realized_p50.zip` in a GTFS validator (e.g. MobilityData's
  canonical GTFS validator, https://github.com/MobilityData/gtfs-validator) — it should
  validate with no new errors beyond whatever the original `warsaw.zip` already had.
- **Actual correction happened:** pick a trip_id that appeared in the `match` output's
  `Observations matched`, and compare its `stop_times.txt` rows in `warsaw.zip` versus
  `warsaw_realized_p50.zip` — the corrected feed's arrival/departure times for that trip's
  observed segments should differ from the original scheduled times, while stops the
  recording never observed keep the original times.
- **Monotonic times:** within any single trip in the corrected feed, arrival/departure times
  must still be non-decreasing stop to stop (this is enforced by `rebuild_stop_times`'s
  monotonic clamp — a validator flagging non-monotonic times would indicate a bug).
- **Loads in the plugin:** point easy-OTP's `RunTemporalAccessibility` (Processing Toolbox →
  easy-OTP → Analysis) at `warsaw_realized_p50.zip` as its GTFS input — it should build an OTP
  graph and run exactly as it would against any other static GTFS feed, since the output is a
  standard static GTFS.

## Tests

```bat
cd tools/family_a_reconstruction
pytest tests/ -q
```

Runs entirely inside this tool's own venv — pure stdlib + pytest, no network, no QGIS. Not
wired into `easy_otp/test`.
