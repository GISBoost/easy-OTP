# family_b_realized — realized GTFS for ŁKA (and any operator) from TripUpdates

> **Not a Processing algorithm.** Standalone CLI, own throwaway venv, never runs in QGIS
> (uses `gtfs-realtime-bindings`, allowed for `tools/` the same way `rt_diagnose` is).

Builds a **realized P50 / P85 GTFS** from archived GTFS-RT **TripUpdates** snapshots,
wrapping the RT-3 "Family B" engine (`easy_otp/core/gtfsrt_realizer.py`, Braga et al.
2023 segment-based percentile aggregation). It **imports** that module — it does not copy
it — so the reconstruction logic has one source of truth.

## Why this exists (ŁKA specifically)

Every other city's realized GTFS is reconstructed from GTFS-RT **VehiclePositions**
(phone recording -> `family_a_reconstruction` -> `easy-GTFS-RT` releases -> dashboard).
Łódzka Kolej Aglomeracyjna has **no VehiclePositions feed**. Its only realtime source is
`https://mkuran.pl/gtfs/polish_trains/updates.pb` — a **national TripUpdates aggregate**
built from PKP PLK's *Otwarte Dane Kolejowe* API, covering every Polish rail operator
(agency `LKA` for ŁKA). Full background:

- `easy-R5/docs/notes/realized-gtfs-lka-tripupdates.md` — why this path, the measured
  feasibility, the retention limit.
- `easy-R5/docs/notes/lka-gtfs-audit.md` — the wrong-network audit that started it.
- `docs/reference/RT-3_realized-gtfs-notes.md` — the engine's methodology + limits.

Two properties of this feed that differ from a generic TripUpdates source:

1. **`confirmed` passings ≈ empirical.** For a *fully elapsed* service day, PLK marks
   ~99 % of stop events as `confirmed` (an actual track-circuit passing, not a
   prediction) — closer to empirical than the "TripUpdates are predictions" caveat in
   the RT-3 notes. The protobuf form drops the JSON `confirmed` flag, so this tool
   compensates by only ingesting elapsed days (see `--service-days`).
2. **~2-day retention.** The feed carries yesterday (complete) + today (partial) and
   nothing older. **There is no historical archive and none can be reconstructed.**
   Collection has to run forward from now — see the daily collectors:
   - phone: `scripts/termux/fetch_polish_trains_rt.sh` (TX-10)
   - backup: `easy-GTFS-RT/.github/workflows/polish-trains-tripupdates-fetch.yml`
   Both archive `polish_trains_updates_<service-day>.pb.gz` to a monthly release
   `polish-trains-tripupdates-raw-<YYYY-MM>` in `GISBoost/easy-GTFS-RT`.

## Setup

```bat
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```bat
:: single day (once collection has started)
py build_realized.py --snapshots .\raw --static polish_trains.zip ^
    --agency-id LKA --out-prefix .\out\lka_realized_2026-09-08 --service-days 2026-09-08

:: multi-day P50/P85 (the intended use once ~15-20 days are archived) --
:: one service day is derived from each snapshot's filename
py build_realized.py --snapshots .\raw --static polish_trains.zip ^
    --agency-id LKA --out-prefix .\out\lka_realized_2026-09
```

Writes `<out-prefix>_p50.zip` and `<out-prefix>_p85.zip` — each a copy of the
(agency-filtered) static feed with `stop_times.txt` rewritten. Output filename shape
matches the existing realized feeds (`<city>_realized_<date>_p{50,85}.zip`).

`--static` is the `polish_trains.zip` that was current **on the service day** (its
`trip_id`s must match the snapshot's). `--agency-id ""` disables the agency filter and
realizes the whole national feed.

## What the numbers mean

- **P50 for a single day** ≈ that day's few trips per segment (segment pools are thin,
  ~1-3 observations). The method's value is multi-day: pooling by
  `(route_id, direction_id, from_stop_id, to_stop_id)` across days produces a *typical*
  segment time even where each `trip_id` is seen once.
- Reconstruction **anchors on the scheduled first departure** and accumulates observed
  P50 (or P85) segment travel times; scheduled dwell is preserved. A segment with no
  observation keeps its scheduled duration ("kept scheduled" in the run log).
- `p85 >= p50` per segment is enforced by the engine.

## Verified

`test_build_realized.py` (`py test_build_realized.py`) — synthetic static + FeedMessage,
asserts corrected times, monotonicity, `p85 >= p50`.

Real data (2026-09-08, snapshot @ 2026-09-09 15:25): 329/329 static ŁKA trips matched,
645 segments / 5695 observations, p50 feed 94.9 % of segment-instances corrected, 0
monotonicity violations. Trip-duration vs schedule: p50 feed median -57 s (schedule
padding), p85 feed median +274 s (reliability buffer).
