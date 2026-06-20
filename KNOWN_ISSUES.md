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

---

This list is not exhaustive. If you hit something not listed here, please open a GitHub issue.
