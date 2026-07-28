# family_a_reconstruction — Family A GTFS reconstruction from vehicle positions

> **This is not a Processing algorithm.** It does not appear in the QGIS Toolbox and cannot
> be run from within QGIS. It requires its own standalone Python environment (see Setup
> below) and is operated entirely from the command line, for advanced users comfortable with a terminal.

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
- `match` — implemented (FA-2), multi-directory merge (FA-6), trustworthy
  `shape_dist_traveled` as the live-matching distance axis (FA-10, see below).
- `build` — implemented (FA-3), trustworthy `shape_dist_traveled` for stop anchoring (FA-10),
  sequential/monotonic stop-pattern anchoring as the geometric fallback for feeds without a
  trustworthy `shape_dist_traveled` (FA-11).
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
  otherwise turn the inter-poll sleep into a no-op and hammer the remote feed). **Hard capped
  at 1500 (25h)** — a value above that is rejected at startup with a readable error, rather
  than silently clamped. This is a deliberate limit, not an oversight: for multi-day coverage,
  do not extend a single recording past the cap — run `record` once per day (manually or via
  Windows Task Scheduler / cron; this tool does not implement scheduling itself) into a fresh
  `--out-dir` each time, then merge the separate sessions at `match` time (see below).
- `--interval-sec` (default `60`) — polling interval in seconds. Same positive-integer
  requirement as `--duration-min` (but no upper cap).

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

Multi-day example (merges several separate single-day `record` sessions into one table —
see "Recording across multiple days" below):

```bat
py -m family_a.cli match --positions-dir day1_recording day2_recording day3_recording --static warsaw.zip --out matched_multiday.csv
```

- `--positions-dir` (required, **repeatable** — FA-6) — one or more FA-1 archive directories
  (`snapshot_*.pb` files), space-separated. Each directory is processed independently and the
  results concatenated. If any directory contains no `snapshot_*.pb` files, or contains a
  filename that doesn't match `snapshot_YYYYmmdd-HHMMSS.pb`, the whole command exits with a
  clear error naming that specific directory — before any matching is attempted on the other
  directories, and without writing a partial `--out` file.
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
- `--max-reject-share` (default `0.25`) — **FA-15.** Warn when more than this fraction of the
  run's observations were rejected. The usual cause is a static feed from a different
  publication period than the recording, whose `trip_id` namespace doesn't match (measured:
  Poznań 2026-07-17 rejects 79.19%, against 0.97–11.83% on healthy city-days).
- `--min-observations-for-route-alert` (default `50`) — **FA-15.** How many observations a route
  needs before a "100% rejected" verdict is reported for it, so a route glimpsed once or twice
  doesn't generate noise.
- `--diagnostics-csv` (off by default) — **FA-15.** Also write a per-route breakdown
  (`observations`, `accepted`, `unknown_shape`, `too_far_from_route`, `rejected_share`).
- `--fail-on-low-yield` (off by default) — **FA-15.** Exit non-zero when the run is flagged.
  Off by default so an automated daily pipeline keeps running and reporting rather than halting
  on something it can't fix itself; the output table is always written first either way.

**Why the FA-15 flags exist.** Before them, rejections were only ever reported as one whole-run
total, so a route whose every observation was rejected silently kept its scheduled times and was
downstream indistinguishable from a route that ran perfectly on time. Real example: Łódź route
`603` on 2026-07-24 had 7,478 observations, 100% of them rejected (its `shape_id` has no points
in `shapes.txt`), while that day's whole-run reject share was an unremarkable 9.53%.

Output columns: `trip_id`, `timestamp` (UTC; pandas' default tz-aware format in CSV, e.g.
`2026-07-05 20:37:26+00:00` — space-separated, not literal ISO-8601 `T`-separated),
`distance_along_shape_m`, `perpendicular_dist_m`, `recording_date` (FA-6) — the calendar date
of the recording *session* that observation came from, derived from the **earliest usable
GTFS-RT `FeedHeader.timestamp` among that `--positions-dir`'s snapshots**, converted to the
static feed's `agency_timezone` (`agency.txt`, falls back to `Europe/Warsaw` if absent — same
resolution `build` already uses for `day_type`). This is deliberate, not the recording
machine's clock or the directory's own name (a directory named e.g. `positions_lodz2` carries
no reliable date information — do not rely on it either): the feed timestamp is the transit
agency's own server time, absolute and timezone-independent, so `recording_date` comes out
correct even when `record` was run from a machine in a different timezone than the agency
being recorded. If every snapshot in a directory lacks a usable `header.timestamp` (GTFS-RT
marks it "strongly recommended", not required — some real feeds omit it), `match` falls back
to the snapshot filename instead and prints a warning naming that directory, since the
resulting date is then only as good as the recording machine's own clock. Written as
a plain `YYYY-MM-DD` string in CSV; stored as a `date32` column in Parquet (read back as
`datetime.date` values via `pd.read_parquet`). Every row from one `--positions-dir` gets the
same `recording_date`, regardless of that individual observation's own timestamp — this
identifies which recording *session* a row came from, distinct from `day_type` (FA-5), which
is derived per-observation.

**Known limitation:** `distance_along_shape_m` is not guaranteed to be strictly increasing
over time for a given trip. When a route passes close to itself (a loop, a nearby parallel
carriageway, a layover), GPS noise can flip the nearest-match point between two locations that
are geometrically close but far apart along the route, producing a small backward jump even
though the position stayed genuinely close to the route the whole time (low
`perpendicular_dist_m`). Observed on ~9% of trips in a manual test against a real Warszawa
archive. This is an inherent limitation of simple nearest-segment matching without
trajectory-continuity awareness, not a bug — see `family_a/matcher.py`'s module docstring.
`build`'s interpolation step does not assume this series is strictly monotonic.

**Trustworthy `shape_dist_traveled` (FA-10):** when the static feed's `shape_dist_traveled`
column is present, fully filled, and unit-consistent with the shape's own geometry (checked
per shape — see `family_a/shape_dist.py`), `match` uses it directly as the distance axis for
live vehicle positions instead of the geometric projection described above, and `build` uses
it directly for stop anchoring instead of `stop_distance_along_shape`'s geometric projection —
this sidesteps the geometric method's tie-break-to-earliest-segment ambiguity entirely, which
otherwise silently collapses two genuinely different occurrences of the same stop on a
loop/out-and-back route to one identical distance. Confirmed on real data so far: Prague's PID
feed is fully trustworthy (its `shape_dist_traveled` is published in **kilometres**, not
metres — `shape_dist.py` detects and converts several common unit conventions, not just
metres). A feed without this column, or with it present but empty/inconsistent (observed on
Łódź and Vilnius: column present in the header, every value blank), falls back to the
geometric method above with no behaviour change. Both subcommands print
`Shapes trustworthy for shape_dist_traveled (FA-10): N/M` in their summary (`build` also
prints the corresponding trip-level count) — check this line to see whether a given feed
benefited.

The command prints a summary of the resolved agency timezone, directories merged, the
recording date range covered, snapshots processed, observations matched, and observations
rejected broken down by reason
(`unknown_shape`, `too_far_from_route`, `no_trip_id`, `corrupt_snapshot`) — all totals summed
across every `--positions-dir` given.

### Recording across multiple days

GTFS-RT's `trip_id` is not date-qualified — the same `trip_id` recurs on every service day the
trip runs. To improve statistical robustness (P50/P85) against a one-off anomaly on any single
recording day (an accident, a diversion, an event), record several separate, single-day
sessions rather than one continuous multi-day recording (`record`'s `--duration-min` is hard
capped at 1500 minutes / 25h for exactly this reason — see Usage — record above):

```bat
py -m family_a.cli record --url https://mkuran.pl/gtfs/lodz/vehicles.pb --out-dir day1_recording --duration-min 240
rem ... next day ...
py -m family_a.cli record --url https://mkuran.pl/gtfs/lodz/vehicles.pb --out-dir day2_recording --duration-min 240
rem ... merge both sessions at match time ...
py -m family_a.cli match --positions-dir day1_recording day2_recording --static lodz.zip --out matched_multiday.csv
```

`match` derives each directory's `recording_date` from its own snapshots' GTFS-RT feed
timestamps (see the output-columns note above) and tags every observation from that directory
accordingly; `build`'s `collect_segment_observations` then groups position series by
`(trip_id, recording_date)` rather than by bare `trip_id`, so two different days' position
series for the same `trip_id` are never concatenated into one artificially continuous run.

**The static GTFS must stay valid across every recording day being merged.** `match` and
`build` both take a single `--static` argument shared by all `--positions-dir`s — there is no
per-directory static feed. Some agencies periodically republish their static GTFS with a new
`trip_id` generation (observed directly on Łódź's open-data feed: `trip_id`s recorded on one
day used prefix `11443`–`11445`, and three days later — after the agency republished — the
live RT feed's `trip_id`s had shifted to `11450`–`11455`, present in the *new* static feed's
`feed_info.txt` as `feed_version`, but entirely absent from the old one). When that happens
between two recording sessions, no single `--static` zip has valid `trip_id`s for both days —
attempting the merge anyway does not error, it just silently rejects every observation from
whichever day doesn't match as `unknown_shape` (check the printed `unknown_shape` reject count
before trusting a merge's results). Re-download the static feed close to each recording
session, and days whose validity windows don't overlap a single static generation need to be
`build` separately rather than merged.

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
- `--time-bucket-minutes` (default `120`) — time-of-day bucket width in minutes for segment
  correction scoping (see below); 12 buckets/day at the default 2-hour width.
- `--min-corrected-route-share` (default `0.40`) — **FA-15.** Warn when fewer than this fraction
  of the routes actually *observed* in the matched table end up with any corrected segment — the
  signature of a build that is mostly just the static schedule and will read as near-perfect
  punctuality downstream (measured: Turin 2026-07-20 published 217 corrected rows out of
  1,416,230, on a day when 99.1% of its routes were running — so not a calendar artifact). The
  default is **calibrated on real data**: 18 healthy builds measured 61.1–100% on this metric
  against 0% and 20% for two broken ones, leaving a clean 20–61% gap; `0.40` is the maximin point
  of that gap (≈20 points of margin on each side). Note the denominator is observed routes, not
  the whole static feed: a raw
  `corrected/(corrected+gap)` over the whole feed is capped by how much of the feed's validity
  window one recording day can cover — Łódź, for example, could only ever correct 35.4% of its
  rows on a single weekday — so it is not comparable between cities or days and is not used here.
- `--diagnostics-csv` (off by default) — **FA-15.** Also write a per-route breakdown
  (`corrected_segments`, `gap_segments`, `corrected_share_full_feed`). Read that last column as
  "how much of this route's whole published timetable got corrected", never as "how well was
  this route observed" — it is diluted by the feed's validity window, per the note above.
- `--fail-on-low-yield` (off by default) — **FA-15.** Exit non-zero when the build is flagged.
  Both realized zips are always written first regardless.

**Known limitation of the `build` gate** (measured, not theoretical): it does *not* catch a
wrong-static-feed failure. Observations rejected during `match` never reach the matched table, so
the "routes observed" denominator shrinks along with them — Poznań 2026-07-17 (a badly broken day)
builds at 87.5% corrected-route coverage, against healthy 07-18's 87.9%. That failure class is
`match`'s `--max-reject-share` gate to catch, and it does.

Output: two GTFS zips, byte-identical to the input static feed except for corrected
`arrival_time`/`departure_time` values in `stop_times.txt` (P50 = typical/median observed
travel time per segment, P85 = pessimistic/85th-percentile). The command prints the resolved
agency timezone (`Agency timezone resolved: ...`), counts for trips processed/skipped, segments
observed/corrected/dropped, interpolation gaps, missing stop locations, and segments rejected for
an implausible time or speed (FA-13 safety net) — use these to judge how much of the recording
actually corrected the schedule versus fell back to planned times.

Family A has no trip-cancellation signal (unlike RT-3, which can read `ScheduleRelationship`
from `TripUpdate`s) — every trip in the static feed is reconstructed, cancelled or not.

**Corrections are scoped by day-type and time-of-day.** A segment is keyed by
`(route_id, direction_id, from_stop_id, to_stop_id, day_type, time_bucket)`, where `day_type`
(`WEEKDAY`/`SATURDAY`/`SUNDAY`, derived from the observation's local calendar date) and
`time_bucket` (`--time-bucket-minutes`-wide blocks of the observation's local time of day) are
resolved from the static feed's `agency_timezone` (falls back to `Europe/Warsaw` with a warning
if `agency.txt` is missing or lacks the column). A correction is only applied to a static trip
whose own service actually runs on a day type the recording covered, at a scheduled time in the
same bucket the recording observed — a trip departing at 3:48 AM is never corrected by an
afternoon-only recording, even if it shares a route and stop pair with trips that were observed.
This means short recordings will show a lower, but more trustworthy, "Segments corrected" count
than a naive route/stop-only key would. Two residual limitations, both deliberately deferred:
- `day_type` is derived from day-of-week only, with no public-holiday awareness (Polish
  holidays run Sunday-style service, which this MVP does not detect) — not yet scheduled.
- `day_type` on the observation side is derived from the **calendar date** of the observation's
  local timestamp, not from GTFS's "service day" concept. For an overnight trip (scheduled past
  midnight, e.g. `25:30:00`, which `calendar.txt`/`calendar_dates.txt` correctly attribute to
  the *previous* service day) a real vehicle observation shortly after local midnight gets
  `day_type` from the *next* calendar date instead — a mismatch against that trip's actual
  service day. `matched_lodz.csv`'s recording window (15:44–19:46 local) never crosses
  midnight, so this has not been observed in practice yet. This remains a known gap even after
  FA-6: FA-6's `(trip_id, recording_date)` grouping (see "Recording across multiple days" above)
  prevents two different days' position series from being merged into one artificial run, but
  it does not fix this separate day_type-vs-service-day nuance for trips that individually
  cross midnight.

Mitigate a short or sparse recording by recording longer sessions (several hours or a full
service day, ideally repeated across day types) and by comparing the printed "Segments
corrected" against "Segments dropped (fewer than N observations)" to judge whether the sample
actually supports trusting the result before using it for analysis.

**How far the correction footprint actually reaches in a static-vs-RT accessibility
comparison (verified 2026-07-10 on real Łódź data).** Day-type/time-bucket scoping means "only
a static trip whose own service and schedule fall in the observed window gets corrected", but
that is not the same as "isochrone differences are confined to exactly the recording's clock
window". Comparing `population_covered` between a static and an FA-5-corrected isochrone sweep
(recording window 15:44–19:46 local, buckets touched: `[14:00,16:00)`, `[16:00,18:00)`,
`[18:00,20:00)`, `cutoff=40 min`) showed two effects worth knowing about before reading such a
comparison:
- **Leading edge:** departures before `14:00` show an exact `0.00` diff up to `13:19`, then
  become nonzero starting at exactly `13:20` — i.e. `bucket_start − cutoff`. A traveller
  departing at 13:20 can, within a 40-minute budget, still ride into a segment scheduled in the
  `14:00` bucket, so their isochrone legitimately reflects the correction. This is the isochrone
  method's own reach into the corrected window, not a scoping leak.
- **Trailing tail:** after `20:00` (end of the last touched bucket), diffs shrink but do not
  hit zero immediately — a shrinking, sporadic tail persists for some trips. This comes from
  `rebuild_stop_times`'s cumulative `running_time`: once *any* segment of a trip is corrected
  (correctly gated to that segment's own scheduled day-type/time-bucket), every later stop of
  that *same* `trip_id` inherits the accumulated shift, even stops scheduled well outside the
  observed window — because the reconstruction is modelling what actually happened to that one
  physical vehicle for the rest of its run, the same propagation the plugin's own RT-3 realizer
  uses. Confirmed directly on `stop_times.txt`: trip `11443_1005` gets a segment correction at
  19:53 (in-bucket) and then a constant `+3:34` offset on every subsequent stop through 20:48
  (out-of-bucket) — a fixed carry-forward, not new corrections being applied there. This tail is
  bounded to trips that were both active in the observed window and continued running past it —
  it never reaches an unrelated `trip_id` (that's exactly what day-type/time-bucket scoping
  prevents; see the 3:48 AM example above).

Net effect on the same comparison: `mean_diff_pct ≈ −0.5%` across the full 12:00–22:00 sweep —
small and concentrated, consistent with scoping working as intended.

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
