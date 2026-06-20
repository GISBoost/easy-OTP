# Known issues — easy-OTP

This list is **not exhaustive** and is a living document. It records the limitations
confirmed so far; details and progress are tracked in
[GitHub Issues](../../issues). Each entry gives a severity, a short description, and a
status or workaround. **Section numbers match the GitHub issue number**, so the sequence
may have gaps (a missing number was taken by a pull request — e.g. #5).

---

## 1. Count raster vs original R reference (`wro_under_30.tif`) — not reconciled

**Severity:** high (confidence in correctness). · **Tracker:** [#1](../../issues/1)

After the NoData bug fix (milestone M4) the counting algorithm is covered by unit tests
and considered correct, but a **controlled 1:1 comparison against the original R workflow
is still not closed**. The remaining difference (~3% of pixels; max abs diff = the number
of surfaces) is attributed to one of: a slightly different origin point (snapping to a
different OTP graph node), different GTFS/PBF input data, or the R reference itself being
**inflated on NoData pixels** (the old script mapped NoData → 0, and 0 ≤ 30 counts as
reachable).

**Status:** a controlled re-test is still to be done — identical origin, the same GTFS/PBF,
recomputed via `CountFromExistingSurfaces` on the same set of surfaces — and only then a
decision on which value is the ground truth. We do not assume up front that the current
result is wrong.

## 2. Sampling interval limited to 1 / 15 / 60 min ✅ Fixed

**Severity:** low (quality-of-life). · **Tracker:** [#2](../../issues/2)

~~An intermediate interval (e.g. 5 or 10 min) cannot be set.~~
**Fixed:** `INTERVAL` is now a free integer spin-box (minValue=1).
Any positive integer interval in minutes is accepted.

## 3. `surfaces` folder doesn't encode the origin point

**Severity:** low. · **Tracker:** [#3](../../issues/3)

Two runs with different origin points but the same router/data/interval/window can overwrite
each other's surfaces. The risk is low (the surface-count guard catches a mismatch).
**Workaround:** use a separate WORK_DIR per origin.

## 4. `CountFromExistingSurfaces` trusts the folder contents

**Severity:** medium. · **Tracker:** [#4](../../issues/4)

A folder mixing surfaces from different runs (different intervals/dates) produces wrong
results — e.g. 961 files recomputed with a ×60 multiplier leads to drastic inflation of
`service_min`. **Workaround:** point the algorithm at a clean single-run subfolder.

## 6. Inverted time window gives misleading error message

**Severity:** low (cosmetic). · **Tracker:** [#6](../../issues/6)

When `TIME_START` is set later than `TIME_END` (e.g. 09:00–07:00), the window-length
guard fires first: `window_min` is negative, so `interval_min > window_min` is always
true and the user sees _"Sampling interval (N min) is longer than the analysis window"_
instead of the correct _"end is before start"_ message. The run still fails fast — only
the error text is misleading. **Fix:** add a `window_min <= 0` guard before the interval
check in `run_temporal_accessibility.py`, `compare_temporal_accessibility.py`, and
`run_realtime_accessibility.py`.

## 7. OTP build fails with `DuplicateEntityException` on GTFS feeds with duplicate IDs

**Severity:** medium. · **Tracker:** [#7](../../issues/7)

OTP 1.5.0 aborts the build (exit code 1) when a GTFS feed has duplicate entity IDs
(e.g. a repeated `route_id`), and the plugin's generic "OTP --build failed" message
doesn't pinpoint the cause. **Workaround:** check each feed's `routes.txt` for duplicate
`route_id` values and fix the offending feed. See [#7](../../issues/7) for details.

## 8. `maxWalkDistance` has no effect in analyst mode

**Severity:** low (won't-fix). · **Tracker:** [#8](../../issues/8)

An OTP 1.5.0 limitation (the shortest-path tree is bounded by time, not distance).
Documented in the UI; not fixed. See [#8](../../issues/8) for details.

## 9. Apple Silicon / ARM not supported by the automated downloader

**Severity:** low. · **Tracker:** [#9](../../issues/9)

The Java/OTP download algorithm supports x64 (Windows / Linux / macOS Intel).
**Workaround:** install the native build manually. See [#9](../../issues/9) for details.

## 10. RT-1 infeasible for feeds with independent trip\_id namespaces (Poznań, Kraków)

**Severity:** high (makes `RunRealtimeAccessibility` output meaningless for affected cities). · **Tracker:** [#10](../../issues/10)

`RunRealtimeAccessibility` reports `rt_effective=0` / `RT-NOT-APPLIED_` for Poznań and Kraków because the live GTFS-RT TripUpdates feed assigns trip_ids from a pipeline completely independent of the published static GTFS — zero ids overlap, so OTP logs `No pattern found for tripId` for every update. `fuzzyTripMatching` cannot help because the `.pb` lacks `start_time` / `direction_id`.

Diagnostic (2026-06-20, time-matched fresh downloads):

| City | Overlap | Fuzzy feasible? | Verdict |
|------|---------|-----------------|---------|
| Poznań (ZTM) | 0 / 333 (static: 35 777 trips) | No — `start_time` / `direction_id` absent | NEITHER |
| Kraków (ZTP tram) | 0 / 527 | No — different route_id namespace, no `start_time` | NEITHER |
| **Gdańsk** (Open Gdańsk) | **213 / 213** | N/A | ✅ EXACT-MATCH |

**Workaround:** none for RT-1. Forward path for these cities is RT-2 `RecordGtfsRt` + RT-3 `BuildRealizedGtfs` (v0.5) — record a day's `.pb` snapshots and synthesize a realized static GTFS whose ids match by construction. The output layer is correctly marked `RT-NOT-APPLIED_` so it cannot be mistaken for a realtime result.

## 11. Date-embedded trip\_ids (Gdańsk) require same-day static GTFS re-download

**Severity:** medium (RT silently applies nothing if static is stale). · **Tracker:** [#11](../../issues/11)

Gdańsk trip_ids encode the service date (e.g. `10`**`20260620`**`1957_32_171-01`). The static GTFS regenerates daily with new ids, so a static downloaded yesterday will produce `No pattern found for tripId` for all live TripUpdates — same symptom as the Poznań data-mismatch, even though the same data pair works perfectly when both are fresh (213/213 overlap confirmed 2026-06-20).

**Workaround:** re-download the static GTFS on the same day as each `RunRealtimeAccessibility` session and rebuild the OTP graph. Use `DownloadTransitData` to refresh before each run.

## 12. OTP 1.5 rejects minority of TripUpdates with non-increasing TripTimes

**Severity:** low (minority of trips affected, no data loss). · **Tracker:** [#12](../../issues/12) · **Status:** won't fix — OTP 1.5 limitation.

When OTP 1.5 propagates arrival/departure delays from a TripUpdate, it can produce stop times that are logically impossible (bus arrives before it departed from the previous stop). OTP rejects those specific TripUpdates with `ERROR TripTimes are non-increasing after applying GTFS-RT delay propagation` / `WARN Failed to apply TripUpdate`. Confirmed on Gdańsk (3–5 trips per 60 s poll out of 213). The remaining majority apply silently. OTP upstream issues: #1250, #2780, #2560. Not fixable in the plugin.

## 13. Warszawa, Wrocław, Łódź: no GTFS-RT TripUpdates feed — RT-1 not applicable

**Severity:** n/a. · **Tracker:** [#13](../../issues/13) · **Status:** won't fix — data availability limitation.

These cities publish static GTFS and (in some cases) real-time VehiclePositions or Alerts, but **no GTFS-RT TripUpdates** feed. `RunRealtimeAccessibility` requires TripUpdates to modify scheduled departure/arrival times; without it there is nothing to apply. The algorithm's help text lists the affected cities. The solution for these cities is RT-2 `RecordGtfsRt` + RT-3 `BuildRealizedGtfs` (v0.5).

---

This list is not exhaustive. If you hit something not listed here, please open a GitHub issue.
