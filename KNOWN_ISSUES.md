# Known issues — easy-OTP

This list is **not exhaustive** and is a living document. It records the limitations
confirmed so far; details and progress are tracked in
[GitHub Issues](../../issues). Each entry gives a severity, a short description, and a
status or workaround.

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

## 2. Sampling interval limited to 1 / 15 / 60 min

**Severity:** low (quality-of-life). · **Tracker:** [#2](../../issues/2)

An intermediate interval (e.g. 5 or 10 min) cannot be set. Planned to be addressed on the
QoL branch.

## 3. `maxWalkDistance` has no effect in analyst mode

**Severity:** low / won't-fix.

An OTP 1.5.0 limitation (the shortest-path tree is bounded by time, not distance).
Documented in the UI; not fixed.

## 4. `surfaces` folder doesn't encode the origin point

**Severity:** low. · **Tracker:** [#3](../../issues/3)

Two runs with different origin points but the same router/data/interval/window can overwrite
each other's surfaces. The risk is low (the surface-count guard catches a mismatch).
**Workaround:** use a separate WORK_DIR per origin.

## 5. `CountFromExistingSurfaces` trusts the folder contents

**Severity:** medium. · **Tracker:** [#4](../../issues/4)

A folder mixing surfaces from different runs (different intervals/dates) produces wrong
results — e.g. 961 files recomputed with a ×60 multiplier leads to drastic inflation of
`service_min`. **Workaround:** point the algorithm at a clean single-run subfolder.

## 6. Apple Silicon / ARM not supported by the automated downloader

**Severity:** low.

The Java/OTP download algorithm supports x64 (Windows / Linux / macOS Intel).
**Workaround:** install the native build manually.

---

This list is not exhaustive. If you hit something not listed here, please open a GitHub issue.
