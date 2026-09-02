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
| [`chart_lab/`](chart_lab/README.md) | Local browser GUI (Gradio) for `transit_charts chart` — no terminal, no CLI flags. Own venv. Imports `transit_charts`/`family_a_reconstruction` by path. |
| [`analysis/`](analysis/README.md) | Ad-hoc population/GTFS-RT analysis scripts — some run in the QGIS Python Console, some are plain CLI. One-off research scripts kept for reuse, not a packaged tool. |
| [`rt_diagnose/`](rt_diagnose/README.md) | Standalone diagnostic for the "RT-1 applied 0 trip updates" blocker — checks whether a static GTFS and a live GTFS-RT feed actually share `trip_id`s. |
| [`network/`](network/README.md) | Guide for preparing a custom `.osm.pbf` network (manual edits, converting a QGIS vector layer, tag/speed edits, merging into an existing extract) — for modelling network changes without touching the plugin. `zasieg.gpkg` alongside it is local scratch data, not tracked. |

## Moved to easy-R5 (2026-09-02)

The R5-based research tooling — `accessibility_lodz/`, `accessibility_cities/`,
`isochrones_lodz/`, `ses_income_lodz/` and the `isochrones-cities.yml` workflow — now lives in
**[GISBoost/easy-R5](https://github.com/GISBoost/easy-R5/tree/main/tools)**, alongside the QGIS
plugin built on the same engine. Folder names and layout are unchanged; commit history up to
2026-09-02 stays here.

One repository per routing engine: OTP work here, R5 work there. `network/` stays in this repo
and is engine-agnostic — easy-R5 links to it rather than duplicating it.
