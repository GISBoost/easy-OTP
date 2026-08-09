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

The **aggregation half** of this tool — travel times pooled per consecutive stop pair per
time-of-day bucket, summarised as a P50 and a P85 feed, then rebuilt by holding each trip's
scheduled first departure fixed and accumulating from there — follows Braga, Loureiro & Pereira
(2023). Note that "Family A / Family B" classifies the *input* (vehicle positions vs
`TripUpdates`), not the aggregation: Braga et al. feed segment aggregation from raw GPS
positions, so despite being cited in easy-OTP's PRDs as the basis for RT-3's statistics, their
data pipeline is Family A and is implemented more directly here than in RT-3.

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
  trustworthy `shape_dist_traveled` (FA-11), three arithmetic corrections to the construction
  itself (D1/D2/D3, 2026-08-09 — see **Construction semantics** below; **every feed published
  before that date carries all three defects**).
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
`distance_along_shape_m`, `perpendicular_dist_m`, `position_signal` (FA-17 — which FA-12 signal
this directory resolved to: `sequence`, `stop_id`, or `none`; broadcast onto every row of that
directory, exactly like `recording_date` below, because two `--positions-dir` values can
legitimately resolve differently. **Diagnostics only since FA-20** — nothing in `build` reads it —
but it travels in the table rather than a sidecar file so an archived `matched.csv` stays
self-describing about how its positions were windowed), `recording_date` (FA-6) — the calendar date
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
- `--dwell-mode` (default `passage`, legacy `production`) — **D1.** What the reconstructed
  timetable's accumulator carries. See **Construction semantics** below.
- `--percentile-method` (default `inclusive`, legacy `exclusive`) — **D2.** Sample-quantile
  estimator for the P85 feed. See **Construction semantics** below.
- `--time-bucket-source` (default `scheduled`, legacy `observed`) — **D3.** Which clock a
  segment observation is filed under. See **Construction semantics** below.
- `--min-plausible-speed-kmh` (default `2.0`, `0` disables) — **FA-18.** Reject a segment
  observation whose implied average speed falls below this. FA-13's speed check (below) is
  upper-bound only, deliberately so — but what that lets through is a vehicle **standing on a
  terminus during its layover**, whose wait the interpolation then books as travel time. When this
  bound was calibrated, FA-17 removed the worst of it only for the first stop pair and only when
  the recording had no position signal, and the same contamination was still measurable in windowed
  cities — this bound rejected **10.6%** of `sequence` first pairs and **3.4%** of `stop_id` ones.
  **Since FA-20 that is history, not a live division of labour:** every trip's first pair is now
  dropped before it is interpolated, so what this bound still catches is stationary observations
  **mid-trip** — a terminus the schedule places mid-route, a driver break, or a layover spilling
  past stop 2.
  **Calibrated on 823,081 raw segment observations** from 7 city-days spanning all three FA-12
  signal classes (Gdańsk `none`, Łódź + Bucharest `sequence`, Vilnius `stop_id`), measured on real
  shape-polyline distances:

  | threshold | caught (proxy positives) | lost (known-good) | discrimination |
  |---|---:|---:|---:|
  | 1.0 km/h | 19.0% | 0.005% | 4025× |
  | 1.5 km/h | 25.9% | 0.024% | 1086× |
  | **2.0 km/h** | **31.1%** | **0.063%** | **496×** |
  | 2.5 km/h | 35.0% | 0.125% | 280× |
  | 3.0 km/h | 39.8% | 0.215% | 185× |

  "Proxy positives" are first stop pairs of trips with no FA-12 window — the population FA-17's
  own measurement showed to be dominated by layovers, but which still contains legitimately moving
  vehicles. So the catch column is a lower bound on real precision, **not** a claim that 69% of
  stopped vehicles slip through. Negatives are mid-trip pairs in windowed cities. That describes
  how the threshold was picked; **since FA-20 drops every trip's first pair outright, what this
  bound still catches is stationary observations mid-trip.**

  The default is chosen on **physics, not on the curve**: over the median 472 m segment, 2 km/h
  means **14.2 minutes** to cross a single stop pair. The rejected population's signature confirms
  it — those observations average **806 s** against a mean of **107 s** for what is kept, while
  being *shorter* (median 342 m vs 473 m). They are not slow journeys; they are stopped vehicles.
  Going to 3 km/h triples the cost for +8.7pp of catch, and 2–3 km/h is the only band where a
  genuinely extreme jam could live; a false rejection biases delay *downward*, the opposite
  direction to the defect being fixed. Verified end to end on the same archived recordings:

  | city-day | observations rejected | change in `Segments corrected` |
  |---|---:|---:|
  | Gdańsk 07-22 | 0.687% | −0.75% |
  | Bucharest 07-21 | 0.251% | −0.33% |
  | Vilnius 07-21 | 0.099% | not measured |
  | Łódź 07-21 | 0.042% | not measured |

  Two second-order effects to keep in mind. Rejections are **per observation**, so a segment key
  that loses enough of them can drop under `--min-observations-per-segment` and revert to its
  scheduled time — downstream that is indistinguishable from a pair nobody ever observed. And the
  calibration cities have a median segment of 472 m: in a feed with very dense downtown stop
  spacing (40–60 m pairs) 2 km/h is only ~90 s, an ordinary "stop, lights, stop" crawl rather than
  a layover. Nothing in the measured set behaves that way, but that is the first thing to check if
  a newly added city reports an unexpectedly high stationary count.
  **A minimum-distance guard was measured and rejected** — do not add one. Against pooled P50s with
  straight-line distances it looked necessary (false positives had a median length of 69 m), but on
  raw observations with real polyline distances that effect disappears (median 342 m) and the guard
  merely cuts the catch from 31.1% to 23.4% while lowering the cost only from 0.063% to 0.058%.
- `--keep-first-segment` (off by default) — **FA-20**, generalising FA-17. By default each trip's
  **first** stop pair is dropped, unconditionally. A vehicle idling on its origin terminus already
  carries the next trip's `trip_id`, and `interpolate_stop_time` returns the *first* bracketing
  pair — so the moment it *arrived to wait* is recorded as the moment it crossed stop 1, and the
  whole layover is booked as travel time on that one pair.

  FA-17 dropped the pair only when the recording carried no FA-12 position signal
  (`position_signal == "none"`, in practice Gdańsk alone). A nine-city measurement on raw
  observations (2026-07-29) showed **the signal class does not predict the artifact** — median
  implied speed of the first pair against mid-trip, km/h:

  | city | FA-12 signal | mid-trip | first pair | under 2 km/h |
  |---|---|---:|---:|---:|
  | Rome | `sequence` 100% | 19.06 | **2.46** | 39.9% |
  | Prague | `sequence` 71.8% | 24.54 | 3.11 | 37.9% |
  | Boston | `sequence` 93.9% | 20.40 | 4.32 | 22.2% |
  | Gdańsk | **`none`** | 19.77 | 4.72 | 23.4% |
  | Szczecin | `sequence` 100% | 20.76 | 5.03 | 20.1% |
  | Brisbane | `sequence` 100% | 26.80 | 6.76 | 11.1% |
  | Lisbon | `sequence` 100% | 16.08 | 6.54 | 7.1% |
  | Vilnius | `stop_id` 100% | 20.67 | 10.57 | 3.4% |
  | Sofia | `stop_id` 100% | 18.54 | 12.00 | 0.1% |
  | Łódź | `sequence` 100% | 18.65 | ~18 | ~0% |

  Rome and Szczecin have **full** `sequence` coverage and are among the worst; Sofia has `stop_id`
  and is nearly clean; Gdańsk — the single city FA-17 was calibrated on — is only fourth. Łódź is
  the one city with essentially no artifact at all, and it is `sequence` too. The FA-17 condition
  was a coincidence of its sample.

  The first pair is also the only one that can carry genuine **departure lateness**, since
  `rebuild_stop_times` anchors each trip on its *scheduled* first departure. Measured on the same
  recordings that costs little: median terminus layover **120–502 s** per city against a median
  departure lateness of **−16 to +15 s**, and pooled observations are reduced to a P50 per segment
  key anyway.

  **What it costs and what it does to the numbers.** Dropping the pair removes **0.2–2.5% of
  segment observations** depending on the city (measured across 51 city-days: Szczecin −0.19%,
  Łódź −0.50%, Rome −1.13%, Vilnius −1.95%, Prague −2.52%; Gdańsk exactly 0%, since FA-17 already
  dropped its pairs). The effect on reported delay is much larger than that share suggests,
  because observations are pooled per segment key and then accumulated along the trip: median
  mean delay falls in eleven of twelve cities, and **Rome, Boston and Lisbon cross below zero**
  (Rome 10.3 → −28.6 s, Boston 56.3 → −4.4 s, Lisbon 2.7 → −17.8 s). Those three already had a
  negative steady-state per-segment increment before the change (−2.1, −1.3, −0.8 s per segment),
  so the sign is consistent with their own schedules being padded — but a feed that now reports
  vehicles as *early* is a publishing decision, not just a technical one. Łódź, which has no
  artifact to remove, moves +0.0 s — the control that says the rule is not simply shaving delay
  off everything.

  Two limits on all the numbers above, carried from the calibration rather than re-derived. The
  departure-lateness figures come only from trips with an **observable dwell**, and that coverage
  ranges from 71% (Rome) to 10% (Szczecin), so Szczecin's lateness is weakly representative. And
  the whole calibration is **27–29 July, i.e. school holidays** — service is thinner and terminus
  layovers plausibly longer than in term time. Re-measure on term-time data before treating any
  of it as settled.

  Pass this flag to measure the artifact instead of dropping it — it disables the skip entirely,
  including for the `none` recordings FA-17 would still have dropped. Note FA-20 **reverses**
  FA-17's backward-compatibility rule on purpose: a matched table written before FA-17 (no
  `position_signal` column) is now skipped as well, rather than left alone.

  One consequence worth knowing before you point this at a new feed: a trip with only **two
  stops** now contributes nothing at all, because its only pair is the first one. A feed made
  entirely of two-stop trips (shuttles, some rail feeds) will build a realized GTFS identical to
  its schedule, and the only signal will be FA-15's `--min-corrected-route-share` warning.
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
- `--max-unknown-trip-share` (default `0.20`) — **FA-16.** Warn when more than this fraction of
  the matched table's `trip_id`s are unknown to `--static`. `match` only emits a row once the
  `trip_id` resolved through that same static feed's `trips.txt`, so for a correctly paired run
  this is **0 by construction** — anything above zero means the matched table and `--static` come
  from different publications, and every unmatched trip is silently dropped. The default is loose
  on purpose: a real mismatch is never subtle (Łódź renumbers its whole `trip_id` namespace every
  1–3 days, ~99% unknown; Poznań per publication period, 67–98%).
- `--diagnostics-csv` (off by default) — **FA-15.** Also write a per-route breakdown
  (`corrected_segments`, `gap_segments`, `corrected_share_full_feed`). Read that last column as
  "how much of this route's whole published timetable got corrected", never as "how well was
  this route observed" — it is diluted by the feed's validity window, per the note above.
- `--fail-on-low-yield` (off by default) — **FA-15/FA-16.** Exit non-zero when the build is
  flagged — either as low-yield (`--min-corrected-route-share`) **or** because the matched table
  and `--static` are not the same publication (`--max-unknown-trip-share`). Both realized zips are
  always written first regardless.

**Feeds with purely numeric `trip_id`s** (FA-16): `build` reads the matched table's `trip_id`
column as text explicitly. Without that, pandas infers the column's type per chunk and such a feed
comes back with most values as integers, which then match nothing in the static feed — silently
discarding the bulk of a healthy match. Boston, whose `trip_id`s are 91.6% numeric, was processing
629 of 8,471 trips and correcting 14,404 segments instead of 192,219. What survived was decided by
where the chunk boundaries fell in a `trip_id`-sorted file, so it is an **arbitrary, clustered
subset — 25 of 126 observed routes — not a uniform sample**. **Boston output published before this
fix must not be compared against later runs, and cannot be rescaled into agreement with them.**
Rome (5.0% numeric) happened to escape it, but only by luck of how its rows chunked.

**Known limitation of the `build` gate** (measured, not theoretical): it does *not* catch a
wrong-static-feed failure. Observations rejected during `match` never reach the matched table, so
the "routes observed" denominator shrinks along with them — Poznań 2026-07-17 (a badly broken day)
builds at 87.5% corrected-route coverage, against healthy 07-18's 87.9%. That failure class is
`match`'s `--max-reject-share` gate to catch, and it does.

Output: two GTFS zips, byte-identical to the input static feed except for corrected
`arrival_time`/`departure_time` values in `stop_times.txt` (P50 = typical/median observed
travel time per segment, P85 = pessimistic/85th-percentile). The command prints the resolved
agency timezone (`Agency timezone resolved: ...`), counts for trips processed/skipped, segments
observed/corrected/dropped, interpolation gaps, missing stop locations, segments rejected for an
implausible time or speed (FA-13 safety net), observations rejected as stationary (FA-18), stop
times whose blank schedule was interpolated (FA-19), and first stop pairs skipped as terminus
layover (FA-20 — printed on every run, including the zero under `--keep-first-segment`). Use these
to judge how much of the recording actually corrected the schedule versus fell back to planned
times.

### Construction semantics — three corrections, 2026-08-09

Three defects were found by auditing this tool against the four reference works and then
measured end to end on four archived city-days — Prague 07-18, Gdańsk 07-22, Łódź 07-21,
Vilnius 07-21, covering all three FA-12 position-signal classes. None of the three is a
methodological choice; each is an arithmetic error whose direction and size are reproducible.
All three are now fixed **by default**, and each legacy behaviour is still reachable through
one flag, for exactly two purposes: rebuilding an already-published feed, and measuring the
size of the defect on new data.

**Every release published before 2026-08-09 was built with all three legacy semantics.** To
reproduce one bit for bit, pass all three flags together:

```bat
py -m family_a.cli build --matched matched.csv --static gtfs.zip --out-prefix legacy ^
   --dwell-mode production --percentile-method exclusive --time-bucket-source observed
```

`build` prints its construction on every run (`Construction: dwell=… percentile=… time-bucket=…`)
and adds a warning line whenever any of the three is not the corrected default, so a release log
records which arm produced it. That round trip is verified, not assumed: rebuilding all four
archived city-days with the three legacy flags reproduces the pre-fix P50 and P85 zips
**byte for byte**, all eight files.

Note that P50 and P85 output under `passage` writes `arrival_time == departure_time` at every
stop but each trip's origin. Total journey time is unchanged — the dwell lives inside the segment
time, which is where the observation put it — so routing results are unaffected; only the split
between "standing" and "moving" is no longer asserted, because this method never measured it.

**D1 — scheduled dwell was counted twice (`--dwell-mode`).** `interpolate_stop_time` returns the
*first* pair of observations bracketing a stop's distance, so a vehicle standing at a stop is
credited with crossing it at the moment it *arrived*. The observed segment time is therefore an
**arrival-to-arrival** interval and already contains the real dwell at the previous stop — and
`rebuild_stop_times` then added the *scheduled* dwell on top of it, compounding along the trip.
This is a units error, not a modelling choice: none of Wessel et al., rt2gtfs or Braga et al. has
it, because all of them write a single time into both fields. `passage` runs the whole chain in
passage times and writes `arrival == departure`; a perfectly punctual vehicle now reproduces the
schedule exactly, which under `production` it could not. Measured on Prague 07-18, P50 feed,
mean arrival delay, changing **only** `--dwell-mode` (rows with a delay of exactly 0 dropped, as
in the published charts):

| `route_type` | scheduled dwell | `production` | `passage` | change |
|---|---|---:|---:|---:|
| 0 tram | ~0 | +23.4 s | **+23.4 s** | **0** ← natural control |
| 11 trolleybus | 0 | +84.2 s | **+84.2 s** | **0** ← natural control |
| 3 bus | ~0 | +27.4 s | +24.6 s | −2.8 s |
| **1 metro** | 410 s/trip | **+239.0 s** | **+16.1 s** | **−222.9 s** |
| **2 rail** | large | **+247.8 s** | **+12.2 s** | **−235.6 s** |
| **whole feed** | | **+49.7 s** | **+24.0 s** | **−51.7%** |

The two zero-dwell route types do not move by a single second, and the size of the metro's shift
matches the scheduled dwell the static feed itself books (410 s per trip, ~205 s per row) — a
prediction made from the static feed alone, before the experiment. Whole-city controls behave the
same way: **Łódź, whose feed has no row at all with `arrival_time != departure_time`, rebuilds
byte-for-byte identically under both modes**, and Gdańsk (845 such rows out of 2.2 M, 0.038%)
changes 0.047% of its rows and no delay statistic at the printed precision.

This also closes the "unexplained Prague baseline offset" that earlier documentation listed as an
open question: its metro reporting a large constant delay on a closed right-of-way was this
artifact, not a property of Prague.

**D2 — the P85 estimator left the data range (`--percentile-method`).** `aggregate_segments`
called `statistics.quantiles(values, n=100)[84]` with no `method=`, i.e. CPython's default
`exclusive`, which estimates a *population* quantile and extrapolates **beyond the largest
observation** for any key with fewer than ~12 observations. `[100, 200]` returned `255.0`;
`numpy.percentile`, `pandas.quantile` and R's default type 7 all return `185.0`. That makes it a
**reproducibility** defect: anyone recomputing these feeds with standard tools got a different
number. The existing `p85 >= p50` clamp never caught it — it only guards the bottom.

| city-day | keys with 2–5 obs. | keys where `exclusive` exceeds the observed max | `sum(P85)` excl vs incl | P85 feed mean delay |
|---|---:|---:|---:|---|
| Łódź 07-21 | 55.6% | 55.6% | +12.90% | 591.6 → 382.2 s (**−35.4%**) |
| Vilnius 07-21 | 78.3% | 78.3% | +11.15% | 391.2 → 255.7 s (**−34.6%**) |
| Gdańsk 07-22 | 67.5% | 67.5% | +13.15% | 508.9 → 326.0 s (**−35.9%**) |
| Prague 07-18 | 61.3% | 61.3% | +11.79% | 347.9 → 192.1 s (−44.8%) |

The two middle columns are the same number in every city, which is the mechanism stated as a
measurement: for a key with 2–5 observations, `exclusive` overshoots the largest one *every time*,
never occasionally. The last column is the whole corrected construction rather than D2 alone —
for the three cities with negligible scheduled dwell it is effectively pure D2 and lands at
−35 ± 1%; Prague falls further because D1 acts on the same feed.

Hyndman & Fan (1996) catalogue nine sample-quantile definitions and R still exposes all of them,
so *picking* one is legitimate; picking the only one that leaves the observed range, by omission,
is not.

**D3 — the two sides bucketed by different clocks (`--time-bucket-source`).**
`collect_segment_observations` derived `time_bucket` from the **observed** crossing time, while
`rebuild_stop_times` looks the key up by the **scheduled** departure from the previous stop. A
vehicle late enough to cross a bucket boundary was therefore filed in one bucket and searched for
in another, and its observation could never be applied to the trip it came from. The loss is
systematic and one-directional: it discards precisely the largest delays. The affected share is
roughly `delay / bucket_width` — measured at **0.68% (Prague) to 1.57% (Gdańsk)** of observations
landing in a different bucket under the default 120-minute width, but the share scales linearly as
the bucket narrows, reaching **~33% at the 15-minute resolution of Braga et al.**, so this is what
blocked moving to that resolution at all. `scheduled` puts both sides on the same quantity.
`day_type` is unaffected and still comes from the observation's own local date.

**F12 — the speed ceiling is per `route_type` (2026-08-09).** FA-13's upper bound of 100 km/h is
a road-vehicle number and was rejecting legitimate rail wholesale: on Prague 07-18, **2,628 of the
3,075 observations rejected for excess speed (85%) are `route_type=2`**, at a median of 131 km/h
and a p90 of 179 — ordinary regional/InterCity running. Rail (GTFS `route_type` 2, plus the
extended railway family 100–117) now gets 200 km/h, which still catches what the bound exists for:
the same population's maximum is 3,587 km/h, a match teleporting across the shape. Verified end to
end — Prague's FA-13 rejections fall from 5,951 to 3,375 (−43%), recovering 2,576 observations and
2,177 corrected segments, while **Gdańsk, Łódź and Vilnius, which publish no rail, do not change by
a single observation**. This matters beyond tidiness: rail is what carries suburban reach, so
under-correcting it biases exactly the part of an accessibility surface that extends furthest.

### VehiclePosition capability matrix (F5, measured 2026-08-09)

The FA-12 windowing decision rests on a measured per-city matrix of `current_stop_sequence` /
`stop_id` coverage. The *other* VehiclePosition fields were dismissed without one — in particular
`current_status`, on the grounds that "real feeds leave it unset almost always". Measured across
**26 recorded cities** (5 snapshots sampled per recording directory, 108 directories,
`match` now reports the same counters per run as `VehiclePosition fields (F5)`):

| field | cities at ~100% | cities at 0% | note |
|---|---|---|---|
| `vehicle.id` | **25 of 25 with data** | 0 | universal |
| `current_status` | 10 (+ Kraków 94.7%, Poznań 28.7%) | 14 | the dwell signal |
| `position.bearing` | 13 | 8 | Prague 83.6%, Szczecin 77.2%, Rome 19.0% |
| `position.speed` | 6 | 15 | Kraków 72.1%, Prague 25.7%, Rome 20.0%, Boston 8.4% |
| `position.odometer` | 0 (Rome 91.1% is the only feed at all) | 25 | effectively unavailable |

Three findings worth acting on:

- **`vehicle_id` is published by every single feed.** Nothing in this tool read it until now; it is
  now carried in the matched table. That is what a vehicle-day anchor chain (Braga et al. anchor
  the first departure of a *vehicle's day*, not of every trip), and telling apart Bucharest metro
  departures that share a `trip_id`, both need.
- **The `current_status` assumption was wrong.** Eleven of 25 feeds publish it, ten of them on
  essentially every entity. That is a direct observation of a vehicle standing at a stop across
  nearly half the monitored network — the raw material for modelling dwell from data rather than
  taking it from the schedule.
- **Gdańsk, the FA-12 worst case, is not signal-less.** It publishes no `current_stop_sequence` and
  no `stop_id`, which is why windowed matching does nothing there — but it publishes `vehicle_id`
  and `speed` on 100% of entities. The information that would constrain its matching is in the same
  message, unread.

Two feeds are unusable for a different reason, and the matrix makes it visible rather than
reporting them as zeros: **Helsinki publishes no `trip_id` at all** (100% of its entities carry
`route_id`/`direction_id`/`start_time` instead) and **Turin drops it on 47%** — the same failure
class FA-15/FA-16 were built to catch, here confirmed at the source.

### The P85 feed is an upper bound, not the 85th percentile of a journey

`sum(P85)` over the segments of a trip is **not** the 85th percentile of that trip's travel time.
Summing per-segment percentiles assumes perfect rank correlation between consecutive segments —
that a trip slow on one segment is slow on all of them. Real delays are not that correlated, so
the true percentile grows like `√k` where the sum grows like `k`.

Measured on Łódź 07-21 (12,089 trips with ≥8 observed segments, median 26 segments/trip, 4,000
Monte Carlo replications on the observed distributions):

| quantity | median |
|---|---:|
| `sum(P50)` — typical running time | 2,490 s |
| **`sum(P85)` — what the P85 feed encodes** | **3,365 s** |
| true P85 under perfect correlation (upper bound) | 3,155 s |
| true P85 under independence (lower bound) | 2,688 s |

`sum(P85)` exceeds even the perfectly-correlated upper bound, and exceeds the independent case by
**+25.6%**. Measured as excess over the typical journey it is worse: the feed encodes 886 s where
the truth is nearer 190 s, a **4.7× overstatement**. It shows up directly as a median delay at the
end of a trip of 974 s (Łódź), 789 s (Gdańsk) and 479 s (Prague).

**So read the P85 feed as a conservative upper bound on travel time, not as a percentile.** Braga
et al. (2023) declare the same assumption as a limitation without quantifying it; Chen & Botta
rejected it on UK data and compute percentiles *after* routing, on OD times, which is the correct
way to get an actual percentile and is what a per-day or per-trip feed would enable here.

### What this feed measures, and what it does not

The corrections above remove three arithmetic errors. They do not change what the method *is*,
and that is worth stating plainly, because the same measurement campaign also quantified it.

This tool pools observations per `(route, direction, stop pair, day_type, time_bucket)`, reduces
each pool to a P50/P85, and rebuilds every trip in the static feed anchored on its own scheduled
first departure. What comes out is therefore a **conditional typical timetable** — an estimator of
the *systematic*, repeatable component of delay, in the sense of Aemmer et al. (2022) — and
deliberately not a record of what happened to any individual vehicle. Three mechanisms remove the
stochastic component: anchoring each trip on its scheduled departure, pooling to a median, and
day-type/bucket scoping combined with `--min-observations-per-segment` (a key with too little
coverage reverts to the schedule, i.e. to zero delay).

The size of that gap has been measured, using this repository's own `collect_stop_crossings` —
which produces one interpolated crossing per scheduled stop, with no pooling, no percentile and
no anchoring — on the **identical set of (trip, stop) pairs**:

| city-day | direct observation (mean / median) | published P50 feed | ratio |
|---|---|---|---|
| Łódź 07-21 | 122.2 s / 62.9 s | 42.7 / 13.0 s | **0.35× / 0.21×** |
| Gdańsk 07-22 | 131.8 s / 46.6 s | 59.3 / 26.0 s | **0.45× / 0.56×** |
| Prague 07-18 (after D1) | 55.4 s / 45.4 s | 17.6 / 0.0 s | **0.32× / 0.00×** |
| Vilnius 07-21 | 67.9 s / 24.8 s | 8.5 / 0.0 s | **0.13× / 0.00×** |

**The feed reports 13–45% of the delay contained in its own observations**, and in three of the
four cities its median delay is 0 s where direct observation of the same crossings gives +25 to
+63 s. This is not a bug introduced or removed by the fixes above — it is the architecture, the
same one Braga et al. use, and it is why a "static vs realized" accessibility comparison built on
this feed lands near ±1% while methods that preserve trip identity (Wessel & Farber 2019; Webb et
al. 2025) report 4–15%. That number is a signature of the method family, not a punctuality
measurement of the city.

**One consequence to expect when comparing before and after.** On Prague the D1 artifact and this
architectural damping partially cancelled: the pre-fix feed sat at 0.64× of direct observation,
apparently the closest of the four cities, because double-counted dwell inflated exactly where
pooling deflated. Fixing D1 moves Prague to 0.32×, in line with everywhere else. Agreement with
direct observation therefore gets *worse* on that one city, and that is the correct outcome, not
a regression.

**Blank scheduled times are interpolated, not read as midnight (FA-19).** GTFS only requires
`arrival_time`/`departure_time` at timepoints; leaving them blank in between is legal and the
consumer is expected to fill them in. Until 2026-07-30 this tool coerced a blank to `00:00:00`
(an empty string is falsy, so the fallback chain swallowed it), which made `rebuild_stop_times`
book the *next* timepoint's absolute clock time as a travel duration — compounding to rows of
141 hours. Blanks are now spread evenly between the surrounding timepoints.

Interpolation is by stop count, not weighted by `shape_dist_traveled`: the only feed in this
monitoring set that publishes blanks (Bucharest) publishes not a single distance value in
`stop_times.txt`, so a distance-weighted branch would never execute. Revisit if a feed turns up
with both. A trip with no times at all cannot be anchored to anything, keeps `00:00:00`, and is
logged as a warning; leading and trailing blank runs clamp to the nearest known time.

Worth knowing when reading the counter: in Bucharest every blank sits on a metro line, and those
lines are excluded from matching upstream, so this defect corrupted the published **file** without
ever moving the published delay **statistics**. A feed that puts blanks on matched routes would
see both.

**Bucharest archives released before 2026-07-30 are affected and are not being recomputed.** Their
`stop_times.txt` contains rows up to 141 hours on the metro lines. Rebuild them from the archived
static feed and matched table if you need them; every other city's archives are unaffected, since
no other monitored feed publishes blank times.

Family A has no trip-cancellation signal (unlike RT-3, which can read `ScheduleRelationship`
from `TripUpdate`s) — every trip in the static feed is reconstructed, cancelled or not.

**Corrections are scoped by day-type and time-of-day.** A segment is keyed by
`(route_id, direction_id, from_stop_id, to_stop_id, day_type, time_bucket)`. `day_type`
(`WEEKDAY`/`SATURDAY`/`SUNDAY`) is derived from the observation's local calendar date, resolved
through the static feed's `agency_timezone` (falls back to `Europe/Warsaw` with a warning if
`agency.txt` is missing or lacks the column). `time_bucket` (`--time-bucket-minutes`-wide blocks)
is derived from the **scheduled** departure of the observed trip from the "from" stop — the same
quantity the rebuild step searches by. Until 2026-08-09 it came from the observation's own local
clock instead, which silently discarded any observation late enough to cross a bucket boundary;
see **D3** under Construction semantics above, and `--time-bucket-source` to reproduce it.
A correction is only applied to a static trip
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
