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

**Update (RT3-5, v0.7):** `BuildRealizedGtfs` now has a `MATCHING_MODE` parameter with a `ROUTE_STOP_FALLBACK` mode — when trip_id overlap is too low to use, it instead joins on `route_id` + `stop_id`, gated by an empirical capability sample of the archive (`AUTO` picks it automatically when the sample looks usable). This narrows this issue for feeds like Poznań/Kraków whose trip_id namespace is permanently disjoint from the static feed's. It does **not** fully resolve Poznań yet — that feed also has a separate, independent defect (single-`StopTimeUpdate`-per-`TripUpdate`, see #18) that RT3-5 cannot fix; full correction there is pending RT3-6.

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

**Severity:** high (RT-3 produces no correction at all for affected cities). · **Tracker:** [#18](../../issues/18) · **Status:** Fixed (RT3-6).

`BuildRealizedGtfs` reported `Segments observed: 0` on a real Poznań archive in both
`RECONCILE_LAST_SNAPSHOT` modes, despite 98% trip_id overlap against the static feed.
Root cause (confirmed by decoding raw snapshots): every `TripUpdate` in the Poznań feed
carries exactly **one** `StopTimeUpdate` (next-stop-only predictions), so
`collect_segment_times`'s adjacent-pair loop never has two stops to compute a segment
time from — independent of trip_id matching. This is a different root cause from
[#10](../../issues/10) (trip_id do match here). Gdańsk's feed carries 1–28
`StopTimeUpdate`s per trip, which is why it is unaffected.

**Workaround (pre-RT3-6):** none. `RunTemporalAccessibility` on the resulting feed fell
back to scheduled times everywhere (output was still valid, just not RT-corrected).

**Status:** confirmed **not** fixable by RT3-5's route/stop fallback matching — the
problem was the feed shape (no adjacent stop pair per snapshot), not id matching. Fixed
by **RT3-6** — cross-snapshot stitching per trip_id, via a new, independent
`SEGMENT_SOURCE_MODE` axis (`AUTO`/`PER_MESSAGE`/`CROSS_SNAPSHOT`) orthogonal to
`MATCHING_MODE` (RT3-5). `PER_MESSAGE` (today's pre-RT3-6 code path) is unchanged for
already-verified feeds (Gdańsk, Szczecin, rail); `CROSS_SNAPSHOT` is auto-selected when
the archive's message-shape sample shows a median of <= 1 `StopTimeUpdate` per
`TripUpdate`. Manually verified in QGIS on the same Poznań archive that produced
`Segments observed: 0` — see `docs/prd/PR_easy-OTP_v07.md` §RT3-6 and milestone 0.6.7 in
`docs/prompts/easy-OTP_v07_prompts_for-claude-code.md`.

## 31. `family_a` build silently dropped numeric `trip_id`s from the matched CSV ✅ Fixed

**Severity:** high (silent data loss). · **Tracker:** [#31](../../issues/31)

Applies to `tools/family_a_reconstruction/` (the standalone Family A CLI), not the QGIS plugin.

~~`build` read its `--matched` CSV with `pandas.read_csv` and no explicit dtype. For a feed whose
`trip_id`s are purely numeric, pandas infers the column type per chunk and returns most values as
`int`; every downstream lookup keys `trip_id` against the static feed's *string* keys, so those
trips resolved to nothing and were dropped as "unresolvable" — with no warning.~~

Measured on Boston 2026-07-19 (same matched table, dtype the only difference): 629 → **8,470**
trips processed, 7,842 → **0** skipped, 14,404 → **192,219** segments corrected. Only 7.4% of
distinct `trip_id`s matched as read. Exposure across the 13 monitored cities: Boston 91.6% numeric
`trip_id`s, Rome 5.0%, the other 11 at 0.0% — and the defect was non-deterministic, since Rome
escaped it only through how its rows happened to chunk.

**Consequence for published data — this is a biased subset, not a uniform under-count.** Which
trips survived was decided by pandas' per-chunk type inference over a `trip_id`-sorted file: a
chunk containing any non-numeric id stayed textual and kept its numeric neighbours too, while
all-numeric chunks did not. On Boston 2026-07-19 the 629 survivors are 62.8% numeric themselves,
and they cover only **25 of the 126 observed routes** — clustered, arbitrary, and not correctable
by scaling. **Boston realized feeds built before this fix must not be compared against later runs,
nor treated as ~8% of the truth uniformly.** Historical releases are deliberately *not* recomputed
(fix-forward only).

**Workaround (pre-fix):** use a Parquet matched table (`--out matched.parquet`, needs `pyarrow`) —
Parquet carries dtypes, so it was never affected.

**Status:** **Fixed by FA-16 in the `family_a` CLI** — `build` reads the matched table with
`dtype={"trip_id": str}` (at read time, never a post-hoc `astype`, which would corrupt leading-zero
ids), and additionally reports the share of matched `trip_id`s the static feed does not recognise
(`--max-unknown-trip-share`), so a mismatched matched/static pair can no longer pass silently
either. Verified byte-identical realized output on all eight unaffected cities. See
`docs/prd/PR_easy-OTP_family-a-matching-accuracy.md` §FA-16.

**Scope of that fix:** the shipped CLI only. The ad hoc analysis scripts under
`gtfs-manual-test/` (gitignored, not part of the tool) read matched tables the same unguarded way
and would reproduce the defect if pointed at a numeric-`trip_id` city. Every threshold calibrated
with them (FA-13/FA-14/FA-15) used only cities at 0% numeric `trip_id`s, so those results are
unaffected — but add `dtype={"trip_id": str}` before reusing them on Boston or Rome.

## 32. `family_a`: Poznań stop pair `1467`→`156` stays inflated after three separate fixes

**Severity:** medium (one route/stop pair). · **Tracker:** [#32](../../issues/32)

Route 196, "Rondo Rataje": reports a P50 up to ~3340 s against a scheduled 120 s. Three
hypotheses tested and **falsified**: stop-anchor ambiguity (FA-11 made the anchor more correct and
it stayed inflated), sparse GPS bracketing (FA-14 finds the brackets already tightly spaced), and
origin-terminus layover (stop `1467` is a middle stop in 740 of 740 trips). Passes every current
filter, so it reaches the published P50.

**Status:** Under investigation, cause unknown. Next step: a per-trip trace of a representative
trip.

## 33. `family_a`: FA-12 windowing degrades Vilnius, traced to route `A62`

**Severity:** medium (one city). · **Tracker:** [#33](../../issues/33)

Vilnius is the only monitored city with 0% `current_stop_sequence`, so it relies on `stop_id`
alone — and FA-12 makes it consistently worse (+13% to +26% more >300 s segments across three
days). Ruled out by measurement: one-day fluke, thin samples (the effect *grows* under stricter
filtering), `backward_tolerance_m`, hourly coverage collapse, and the repeated-`stop_id`
disambiguation logic. Every one of the top-30 largest per-observation jumps, on all three days,
belongs to route `A62`.

**Workaround:** set `--position-signal-coverage-threshold` above 1.0 for Vilnius to reproduce
pre-FA-12 behaviour exactly.

**Status:** Under investigation — narrowed to A62's shape geometry.

## 34. `family_a`: Poznań builds use a static feed not yet valid for the recorded day

**Severity:** high (roughly 1 day in 3). · **Tracker:** [#34](../../issues/34)

ZTM Poznań publishes the next period's static GTFS several days early, and Poznań renumbers
`trip_id` per period — so on those days almost nothing matches. Two of six sampled days had a feed
whose validity starts *after* the recording date, and `pct_changed` collapses to 3.2% / 12.8%
against 22–71% on valid days. On 2026-07-17, **76.91% of observations** carried a `trip_id` the
static had never heard of. On 07-25/07-26, no published static of the period matched at all
(best: 4.8% / 2.1%).

**Status:** Detection done in `family_a` (FA-15 warns); the fix — selecting a feed actually valid
for the recording date — belongs in `GISBoost/easy-GTFS-RT`'s build workflow.

## 35. `family_a`: flat 100 km/h plausibility filter rejects legitimate regional rail

**Severity:** medium (Prague rail only). · **Tracker:** [#35](../../issues/35)

FA-13's single flat speed threshold cannot distinguish a genuinely fast rail segment from a
matching artifact. On Prague 2026-07-18 it drops **2,187 of 123,833 segment keys**, almost all
`route_type=2` (R9/R16/R17). Ordinary urban networks stay in the intended 0.01–0.8% band.
Prague's rail is therefore under-corrected relative to its trams and buses.

**Status:** Known, deliberately accepted — a per-`route_type` threshold is a distinct, larger
change.

## 36. `family_a`: one bad GPS ping invalidates several neighbouring stop pairs

**Severity:** low (filter handles it correctly). · **Tracker:** [#36](../../issues/36)

Because every stop whose distance falls inside the same bracketing pair shares it, one anomalous
raw position contaminates several consecutive stop pairs. Measured on Bucharest 2026-07-17:
**1,414 anomalous raw pairs explain 3,613 rejected segment observations — 2.56x amplification**.
Ruled out as a geometry bug; it is isolated raw telemetry glitches, and Bucharest's raw glitch
rate is genuinely ~6–8x Poznań's or Łódź's.

**Status:** Known, deliberately not fixed — only 62 of 40,897 keys lose all data, and the more
surgical fix would reintroduce #35's flat-threshold problem one layer earlier.

## 37. `family_a`: routes whose `shape_id` has no geometry are silently invisible

**Severity:** high (whole routes unmeasurable). · **Tracker:** [#37](../../issues/37)

`resolve_trip_shapes` falls back to straight-line shapes only when `shapes.txt` is missing
*entirely*, so a partially-broken `shapes.txt` leaves the affected trips unmatched. Łódź is
affected on **every archived day** — route `R9` is 100% dangling every time — and on 2026-07-24
it reached **9.11% of all trips**, with route `603` producing 7,478 observations of which **100%
were rejected** while the whole-run rejection share was an unremarkable 9.53%.

**Status:** Visible since FA-15 (a route with observations but none accepted is now named);
measurable only with a per-trip fallback, which does not exist yet.

## 38. `family_a`: multi-day pooling breaks across a `trip_id` republication boundary

**Severity:** medium (affects opt-in pooling only). · **Tracker:** [#38](../../issues/38)

`match --positions-dir dir1 dir2 ...` assumes one static feed's `trip_id` numbering resolves every
pooled day. Łódź renumbers every 1–3 days: pooling 07-20..07-24 against one static gave a **62.7%
`unknown_shape` rejection rate**, three incompatible eras in one week. `route_id`/`stop_id` are
stable, which is why per-era matching merged at the segment-key level works as a workaround.

**Status:** Detected downstream since FA-16 (`build` reports unrecognised trip_ids); `match` still
does not check its own inputs.

## 39. Turin: `VehiclePositions` feed omits `trip_id`, producing empty builds

**Severity:** high (that city's data). · **Tracker:** [#39](../../issues/39)

Upstream defect, not a pipeline bug. 2026-07-20: **335,257 of 337,023 observations had no
`trip_id`**, and the build published anyway — **217 corrected rows out of 1,416,230**, with a
chart, reading as near-perfect punctuality. 2026-07-22: all 344,449 lacked it, and no release was
produced.

**Status:** Upstream. Flagged loudly since FA-15; nothing here can recover a field that was never
published.

## 40. `family_a`: reported delay is a running total, so it rises along a trip

**Severity:** medium (affects interpretation of every delay figure). · **Tracker:** [#40](../../issues/40)

`rebuild_stop_times` anchors each trip to its *scheduled* first departure and only accumulates;
an unobserved segment carries the accumulated error forward unchanged. Median delay by position
within a trip rises monotonically in **every city measured** (Łódź 6.0x, Vilnius 3.9x, Gdańsk
2.4x, Poznań 1.7x, Prague 1.5x). In Prague the effect is mode-independent and **even the metro
shows +158 s**, which is not credible for a closed right-of-way.

**Status:** By design — re-anchoring would need the actual departure time, which is what the
pipeline is inferring. Documented for data consumers in `easy-GTFS-RT`'s `HOW-IT-WORKS.md`.

## 41. `family_a`: `day_type` uses the local calendar date, not the GTFS service day

**Severity:** low (little data in the affected window today). · **Tracker:** [#41](../../issues/41)

An after-midnight trip is filed under the next calendar date's `day_type`, while GTFS attributes
it to the previous service day. Public holidays are not modelled either — a holiday running
Sunday-style service is treated as whatever weekday it falls on.

**Status:** Known, deliberately deferred since 2026-07-10. Recording runs ~06:00–22:00, so almost
no data currently falls in the affected window.

## 42. `family_a`: Prague's constant baseline delay offset is unexplained

**Severity:** high (the original symptom, still open). · **Tracker:** [#42](../../issues/42)

The anomaly that started this whole investigation. A large offset is **already present at the
second stop of a trip**, before accumulation (#40) can contribute. Falsified so far: the
loop-anchoring bug (FA-10 fixed it completely; mean delay went *up*, 211.3 s → 304.4 s),
live-position ambiguity (FA-12 moved Prague 2.8%), sparse bracketing (FA-14), and dwell
double-counting (mechanism real, magnitude negligible — Gdańsk has ~zero encoded dwell and
+191.1 s). Mode-independent, metro included.

**Status:** Under investigation — the longest-standing open question here. Prague's absolute delay
figures should not be read as measured punctuality until this is explained.

## 43. `BuildRealizedGtfs` reads blank `stop_times` as midnight

**Severity:** high (corrupt output, silent). · **Tracker:** [#43](../../issues/43)

`gtfsrt_realizer.py:125` resolves a stop time through
`row.get("arrival_time") or row.get("departure_time") or "0:0:0"`. An empty string is falsy, so a
stop with **both** fields blank becomes midnight. Blank times at non-timepoint stops are legal
GTFS the consumer is meant to interpolate. `rebuild_stop_times` then books the *next* timepoint's
absolute clock time as a travel duration, compounding at every timepoint after a run of blanks —
measured on Bucharest's TPBI feed at rows of up to **141 hours** (1.93% of rows blank, in 4.6% of
trips).

The standalone `tools/family_a_reconstruction` fixed this as FA-19 (`interpolate_blank_stop_times`,
spreading blanks evenly between the surrounding timepoints). `build_gtfs.py` declares itself a
reimplementation of this module's logic, so the two have diverged and the plugin carries the
defect. A divergence comment marks the spot in the source.

**Status:** Fix planned — port the FA-19 parsing and interpolation. Feeds without blank times are
unaffected (11 of 12 monitored feeds have 0.00%).

---

This list is not exhaustive. If you hit something not listed here, please open a GitHub issue.
