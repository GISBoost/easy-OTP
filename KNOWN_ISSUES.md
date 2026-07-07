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

**Update (RT3-5, pending human verification):** `BuildRealizedGtfs` now has a `MATCHING_MODE` parameter with a `ROUTE_STOP_FALLBACK` mode — when trip_id overlap is too low to use, it instead joins on `route_id` + `stop_id`, gated by an empirical capability sample of the archive (`AUTO` picks it automatically when the sample looks usable). This narrows this issue for feeds like Poznań/Kraków whose trip_id namespace is permanently disjoint from the static feed's. It does **not** fully resolve Poznań yet — that feed also has a separate, independent defect (single-`StopTimeUpdate`-per-`TripUpdate`, see #18) that RT3-5 cannot fix; full correction there is pending RT3-6.

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

## 14. CAR isochrones return null geometry when origin is not on a driveable road

**Severity:** medium (CAR mode unusable if origin placement is wrong). · **Tracker:** [#14](../../issues/14)

OTP 1.5 cannot snap the origin point to a car-accessible edge when the point is inside a pedestrian zone, private compound, or area tagged `motor_vehicle=no`. The shortest-path tree stays at 3 split-vertices and expands nowhere; OTP returns `"geometry": null` for every cutoff. TRANSIT and BICYCLE work from the same point because pedestrian/bicycle edges are almost always available.

Confirmed via OTP server log: `SPTWalker: Generated 0 points from 3 vertices / 3 states`.

As of v0.5.2, `GenerateIsochronesOverTime` emits a targeted warning when all timestamps return null geometry, suggesting the user move the origin to a driveable road. A matching improvement is still needed in `GenerateIsochrones` (N-1).

**Workaround:** place the origin point on a road tagged `highway=residential` or higher, without `access=private` / `motor_vehicle=no`. Verify routing via the OTP debug UI at `http://localhost:<port>` before running the algorithm.

**Status:** partial fix in v0.5.2 (diagnostic warning). Root cause is OTP snapping behaviour — not fixable in the plugin.

## 15. RunOriginDestinationTimes: OTP PATH\_NOT\_FOUND (404) for origins outside transit reach

**Severity:** medium (many cells may show NULL travel time). · **Tracker:** [#15](../../issues/15)

`RunOriginDestinationTimes` returns `status=404` (OTP `PATH_NOT_FOUND`) for origin
cells that cannot be reached with the default `MAX_WALK_DISTANCE=800 m`. Adjacent
cells may alternate OK/404 due to OTP 1.5 network snapping behaviour: the origin
centroid is linked to the nearest graph edge (preferring car edges over pedestrian
ones), so cells in parks, courtyards, or near road-type boundaries can miss the
transit network even when transit stops are nearby. In reference data
(`docs/gisboostgithub/pop_results2.csv`), 30/50 cells returned 404 at the 800 m
default; raising `MAX_WALK_DISTANCE` to 9 999 m yielded 100% OK responses.

**Mitigations built into the algorithm:**
- **`MAX_WALK_DISTANCE`** parameter (default 800 m) — the primary lever. Raise to
  1 500–9 999 m to reduce/eliminate 404s; note that very high values allow
  unrealistically long walk legs (user's methodological choice).
- **`SNAP_ORIGINS_TO_NETWORK`** toggle — snaps centroids to the nearest road edge
  before routing; requires an OSM-lines or roads layer as input.
- **`DIAGNOSE_UNREACHABLE`** mode — adds a `diag` field (`off_network` /
  `no_transit`) by sending a walk-only fallback for each 404 cell.
- **`status` field** — stores the exact OTP error code (`404`, `406`, `409`,
  `410`, `440`, `450`) so users can distinguish true off-network cells from
  transit-gap cells.

**Status:** known; mitigations documented in the UI and in the README.

## 16. RouteViaPoints: OTP leg geometry misaligns with OSM; off-network via-points silently snap

**Severity:** medium (visual/positional). · **Tracker:** [#16](../../issues/16) · **Status:** Under investigation.

Two related sub-issues:

1. **Geometry mismatch** — OTP 1.5.0 simplifies street geometry during graph build (edge splitting, coordinate rounding). The decoded polyline from `/plan` legs follows a simplified version of OSM geometry. On curved streets the offset is visually noticeable even though the route is topologically correct. This is an OTP 1.5 graph-build artefact; not fixable in the plugin.

2. **Silent via-point snapping** — OTP snaps each query point to the nearest graph vertex. A via-point placed slightly off a path (park interior, building footprint, mid-block without a nearby vertex) is silently moved to that vertex, which may be tens or hundreds of metres away. The route succeeds but passes through the wrong location without any warning. This is distinct from the hard NPE failure (fixed in v0.6.1 fix); here OTP succeeds but with a shifted position.

**Workaround:** Use QGIS vertex snapping (Snapping Toolbar) when digitizing via-points, placing them exactly on OSM path vertices. If a segment detours unexpectedly, move the nearest via-point to a clearly walkable OSM vertex and re-run.

## 18. `BuildRealizedGtfs`: single-stop RT feeds yield zero segments (Poznań)

**Severity:** high (RT-3 produces no correction at all for affected cities). · **Tracker:** [#18](../../issues/18) · **Status:** Under investigation.

`BuildRealizedGtfs` reports `Segments observed: 0` on a real Poznań archive in both
`RECONCILE_LAST_SNAPSHOT` modes, despite 98% trip_id overlap against the static feed.
Root cause (confirmed by decoding raw snapshots): every `TripUpdate` in the Poznań feed
carries exactly **one** `StopTimeUpdate` (next-stop-only predictions), so
`collect_segment_times`'s adjacent-pair loop never has two stops to compute a segment
time from — independent of trip_id matching. This is a different root cause from
[#10](../../issues/10) (trip_id do match here). Gdańsk's feed carries 1–28
`StopTimeUpdate`s per trip, which is why it is unaffected.

**Workaround:** none. `RunTemporalAccessibility` on the resulting feed falls back to
scheduled times everywhere (output is still valid, just not RT-corrected).

**Status:** confirmed **not** fixable by RT3-5's route/stop fallback matching — the
problem is the feed shape (no adjacent stop pair per snapshot), not id matching. Scoped
(not yet implemented) as **RT3-6** — cross-snapshot stitching per trip_id, a new
`SEGMENT_SOURCE_MODE` (`AUTO`/`PER_MESSAGE`/`CROSS_SNAPSHOT`) — see `docs/prd/
PR_easy-OTP_v07.md` §RT3-6 and milestone 0.6.7 in `docs/prompts/
easy-OTP_v07_prompts_for-claude-code.md`.

---

This list is not exhaustive. If you hit something not listed here, please open a GitHub issue.
