# example_data — bundled tidy table for chart_lab

`lodz_2026-07-23_example.csv.gz` (2.0 MB) is the tidy table `chart_lab` loads automatically on
startup so the app shows a real chart with zero user interaction. It is a 7-route subset of
Łódź, 2026-07-23 — the same city-day already used for the rendered PNGs in
`tools/transit_charts/assets/full-day-example/` (routes 10A, 11, 14, 15, 52, 55, 69).

## Provenance

- Matched positions: `tools/family_a_reconstruction/gtfs-manual-test/out_fa18/matched_lodz_2026-07-23.csv`
- Static GTFS: `tools/family_a_reconstruction/gtfs-manual-test/static_gtfs/lodz_static_gtfs_2026-07-23.zip`

Routes 55 and 69 have no exact `route_short_name` of their own in this feed — only lettered
variants exist (`55A`/`55B`/`55C`, `69A`/`69B`), confirmed via the static GTFS's `routes.txt`.
`--route 55`/`--route 69` would therefore match nothing and `extract` treats that as a hard
error (by design — a pattern matching nothing must never silently produce an empty chart), so
the command below uses `55*`/`69*` prefix matches instead.

## Regeneration command

Run from `tools/transit_charts`, using its own venv:

```bat
cd tools\transit_charts
.venv\Scripts\python.exe -m transit_charts.cli extract ^
  --matched ..\family_a_reconstruction\gtfs-manual-test\out_fa18\matched_lodz_2026-07-23.csv ^
  --static ..\family_a_reconstruction\gtfs-manual-test\static_gtfs\lodz_static_gtfs_2026-07-23.zip ^
  --city lodz --route 10A --route 11 --route 14 --route 15 --route 52 --route 55* --route 69* ^
  --out ..\chart_lab\example_data\lodz_2026-07-23_example.csv.gz
```

Re-run this whenever `transit_charts`'s extraction logic changes in a way that would make the
cached table stale (new tidy columns, changed matching/interpolation logic, etc.).
