# tools/ — standalone tools outside the plugin

Everything under `tools/` is **outside the QGIS plugin** — none of it is imported by
`easy_otp/`, none of it ships in the plugin ZIP, and none of it needs to run inside QGIS
unless a subfolder's own README says otherwise. It's the workshop around the plugin: data
reconstruction, ad-hoc analysis, charting, and developer tooling, each with its own Python
environment where one is needed. See the main [`README.md`](../README.md) for the plugin
itself, and the root `CLAUDE.md` for the constraints that apply repo-wide (English in code,
`py` not `python`, no `pip install` inside QGIS's own environment, etc. — none of that
"no pip install" rule applies here, since nothing in `tools/` runs inside QGIS's interpreter).

## Folders

| Folder | What it is |
|---|---|
| [`family_a_reconstruction/`](family_a_reconstruction/README.md) | CLI tool that reconstructs an observed GTFS (P50/P85) from recorded GTFS-RT VehiclePositions, for cities with no `TripUpdates` feed. Own venv. |
| [`transit_charts/`](transit_charts/README.md) | Charts scheduled-vs-observed transit (punctuality, regularity, speed) from `family_a_reconstruction`'s `matched.csv` + static GTFS. Own venv (matplotlib). Main README is in Polish, [`README.en.md`](transit_charts/README.en.md) is the English mirror. |
| [`analysis/`](analysis/README.md) | Ad-hoc population/GTFS-RT analysis scripts — some run in the QGIS Python Console, some are plain CLI. One-off research scripts kept for reuse, not a packaged tool. |
| [`rt_diagnose/`](rt_diagnose/README.md) | Standalone diagnostic for the "RT-1 applied 0 trip updates" blocker — checks whether a static GTFS and a live GTFS-RT feed actually share `trip_id`s. |
| [`network/`](network/README.md) | Guide for preparing a custom `.osm.pbf` network (manual edits, converting a QGIS vector layer, tag/speed edits, merging into an existing extract) — for modelling network changes without touching the plugin. `zasieg.gpkg` alongside it is local scratch data, not tracked. |
| `i18n/` | QGIS plugin UI translation pipeline (`.ts` ↔ local LLM ↔ `.qm`). Has its own README, but the whole folder is gitignored — developer-only tooling, never distributed. |
| `poznan-rt-feed/` | Local OTP working directory used while testing Poznań's GTFS-RT feed (graphs, pointsets, surfaces, logs). Pure runtime output, not tracked, no code. |

## Untracked scratch at the top level

`converter-pdf.py` (a one-off PDF→Markdown conversion script for a single paper) and any
stray `test.qgs` / `test_attachments.zip` files are personal scratch, not part of any
tool above — ignore them unless you know why you're touching them.
