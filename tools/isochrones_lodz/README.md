# tools/isochrones_lodz — interactive isochrone map data pipeline

**Standalone research tooling**, not part of the plugin. Feeds the web map at
[mapy-analizy/izochrony-transport](https://github.com/GISBoost/mapy-analizy/tree/main/izochrony-transport)
([live](https://gisboost.github.io/mapy-analizy/izochrony-transport/), also mirrored on
[Cloudflare Pages](https://mapy-analizy.pages.dev/izochrony-transport/)) — hover anywhere on
the map to preview a transit isochrone from that point, click to pin it, scrub a
time-of-day slider to watch the shape change, toggle 15/30/45-min cutoff bands and
scheduled-vs-realized (GTFS-RT) GTFS (all 6 cities now have both variants — see
status below).

**Status (2026-08-26): all 6 cities computed and live** (Lodz, Szczecin,
Kraków, Poznań, Gdańsk, Warszawa — via `.github/workflows/isochrones-cities.yml`,
GitHub Actions, artifacts downloaded and copied into `mapy-analizy/izochrony-transport/data/`).
**Warszawa is the odd one out on grid density** — see below.

**Warszawa uses a 1000m origin grid (668 origins), not the SES study's 500m
(2546 origins)** that every other city keeps. Two 500m CI runs on Warszawa
each cost ~4h+ before failing (an OOM at 12G heap/800-batch, then an isoband
contour bug at hour 21:00 that a retry-at-half-batch-size workaround got past
but still took the full ~4h10m) — explicit call to cut both the compute time
and the surface complexity that's triggering these edge cases, rather than
keep fighting them at 500m. Grid built the same way as every other city's
(`native:creategrid` TYPE=4 + `native:extractbylocation` whole-hex, see
`tools/accessibility_cities/HOWTO_MANUAL.md` step 4, just HSPACING/VSPACING=
1000): `tools/accessibility_cities/warszawa/warszawa_hex_origins_1000m.csv`
(668 origins) and `warszawa_hex_boundary_1000m.geojson` (use this one, not
the 500m boundary, when copying Warszawa's boundary into the site). Both
`compute_isochrones_city.R` and `export_isochrone_data.py` branch on
`city == "warszawa"` to read this file instead of the standard
`<city>_hex_origins.csv` — keep them in sync if this ever changes.

Uses `r5r::isochrone()` (real concave polygons per origin/cutoff/departure time),
not `travel_time_matrix()` — chosen after a dry run showed `isochrone()`'s cost
scales with origin count (like OTP) rather than being batched per departure time
like the matrix function, which set the real budget for how dense/how many time
steps the web map could ship. See `RESEARCH_LOG.md`-style narrative: none written
yet, this file plus the scripts' own comments are the record for now.

## Inputs (gitignored, copied from `tools/accessibility_lodz/`)

- `network_static/`, `network_rt/` — each a copy of `lodz.osm.pbf` + exactly one
  GTFS zip (static schedule vs. `..._p50.zip` realized). r5r builds one graph per
  folder; static and RT GTFS reuse the same trip_id/stop_id (RT is a corrected
  copy of the same service day) so they cannot share a folder without colliding —
  same reason `tools/analysis/generate_isochrones_multi_city.py` isolates them.
- `lodz_origins_500.csv` — copy of `accessibility_lodz/lodz_hex_origins.csv`
  (500m hex centroids, 1479 points, id/lon/lat WGS84). Chosen over the denser
  250m grid (`lodz_hex250.gpkg`/`lodz_hex250_origins.csv`, 6175 points, built but
  unused) after the dry run showed 250m origins would blow the time/size budget —
  see decision log below.

## Pipeline (Lodz — already run, both variants)

1. `dry_run_isochrone.R <variant> <n_sample> [sample_size]` — measure real
   cost (s/origin, MB/origin) on a small spread sample before committing to a
   full sweep. Keep this script; re-run it if origins/cutoffs/time-window change.
2. `compute_isochrones.R <variant: static|rt>` — full sweep: all 1479 origins ×
   17 hourly departures (06:00–22:00) × cutoffs (15/30/45 min) →
   `<variant>_isochrones_all.gpkg` (gitignored, ~75k features).
3. **Simplify via `ogr2ogr` directly** (not qgis-mcp — the bridge choked on a
   75k-feature/445MB layer, silently kept running server-side after its own
   tool call reported failure; see decision log):
   ```
   "C:\Program Files\QGIS 3.44.11\bin\ogr2ogr.exe" -f GeoJSON \
     <variant>_isochrones_ogr.geojson <variant>_isochrones_all.gpkg isochrones \
     -simplify 0.000269 -lco COORDINATE_PRECISION=5
   ```
   (`0.000269` deg ≈ 30m at this latitude; `COORDINATE_PRECISION=5` ≈ 1.1m,
   both applied in one pass — no separate Python rounding step needed.)
4. `py export_isochrone_data.py <city> <variant>` — splits the simplified
   GeoJSON into one file per origin (`data/<city>/<variant>/<origin_id>.geojson`,
   all 17 hours × 3 cutoffs bundled so the browser fetches once per
   hovered/clicked point, not once per slider tick), rounds coordinates to 5
   decimals, writes `data/<city>/manifest.json`. For Lodz, run this for BOTH
   variants before converting either to geobuf (step 5 deletes the .geojson
   this script's manifest-presence scan looks for).
5. **`node geobuf_pack/convert.js <city> <variant>`** — re-encodes every
   per-origin `.geojson` into geobuf (`.pbf`, binary protobuf encoding of
   GeoJSON) and deletes the `.geojson`. Measured on this dataset: geobuf is
   **~18% of raw GeoJSON size**, and even gzipped (which GitHub Pages applies
   automatically in transit) it's still **~47% of gzipped-GeoJSON size** — see
   decision log. `geobuf_pack/` is a tiny standalone `npm install geobuf pbf`
   (not part of the plugin, `node_modules/` gitignored). The browser side
   needs `pbf@3.2.1` + `geobuf@3.0.2` before `app.js` — self-hosted from
   `izochrony-transport/vendor/` since 2026-08-27 (was CDN via unpkg; pin
   these versions either way, `geobuf@3.0.2`'s browser bundle expects `pbf`'s old
   unified-class API, not the `PbfReader`/`PbfWriter` split introduced in
   newer `pbf` releases, which is what the Node conversion script itself
   needs to work around via `new Pbf.PbfWriter()`); `app.js` fetches `.pbf`
   as an `arrayBuffer()` and decodes with `geobuf.decode(new Pbf(bytes))`.
6. Copy `data/<city>/` into `mapy-analizy/izochrony-transport/data/<city>/`
   (manual, matches the other two analyses' "refresh = rerun here, re-export
   there" convention), and add `{ "id": "<city>", "label": "<Display Name>" }`
   to the top-level `mapy-analizy/izochrony-transport/data/manifest.json` — that's
   the only site-side code change needed to light up a new city, `app.js`
   handles any number of cities generically.

## Pipeline (Warszawa/Kraków/Gdańsk/Poznań/Szczecin — run, both variants)

Same steps 3–6 as above, but step 2 is `compute_isochrones_city.R <city>`
instead of `compute_isochrones.R` — a separate script because these 5 cities:
- **Correction (2026-08-27):** this section previously said these 5 cities
  only ever get the `rt` variant. That's stale — `data/<city>/manifest.json`
  for all 6 cities now lists `variants: ["rt", "static"]`, and the deployed
  `rt/<id>.pbf` vs `static/<id>.pbf` files are verified byte-distinct (not a
  duplicate/copy), so the site's scheduled-vs-realized toggle is live and
  fetches real data for all 6, not just Lodz. Exactly which run added the
  `static` variant for these 5 isn't recorded here — verify against
  `compute_isochrones_city.R`'s current variant handling before assuming
  anything below about network folder layout still matches.
- reuse each city's existing 500m `<city>_hex_origins.csv` and
  `<city>_hex_boundary.geojson` from the SES study (`tools/accessibility_cities`)
  — nothing new to fetch or grid.
- use departure date **24-08-2026** (a Monday), not the GTFS recording day
  (2026-08-22, a Saturday) — `run_city_pipeline.sh` already hit and fixed this
  exact bug for the SES accessibility runs; `compute_isochrones_city.R` bakes
  the fix in rather than re-discovering it.

Cost estimate (not yet spent — this is what the go-ahead signal commits to):
origin counts warszawa 2546, krakow 1633, gdansk 1389, poznan 1350,
szczecin 1567 (≈8485 total, vs. Lodz's 1479) — at Lodz's observed throughput
(~0.045 s/origin/hour-step) that's **~1.8h sequential compute** for all 5
(one variant each, so less total than Lodz's two-variant run despite more
origins). Data: Lodz measured ~41.5 KB/origin/variant as geobuf, so ~8485
origins × 1 variant ≈ **~350MB** added to `mapy-analizy` (124MB today → ~470MB
total). If that's too much when the time comes, the lever is subsampling the
existing hex grids (e.g. every other point) rather than recomputing anything —
not applied preemptively since it trades hover granularity for size and that's
the user's call, not an engineering default.

## Decision log (dry run, 2026-08-25)

## Decision log (dry run, 2026-08-25)

- 250m origins × 65 (15-min) steps × 2 variants was the original target —
  measured extrapolation: **~85-95h compute, ~14.5GB raw output**. Not viable.
- `isochrone()` cost scales ~linearly with origin count even in one batched
  call (unlike `travel_time_matrix()`); best observed throughput ~0.05 s/origin
  at large batch sizes (600+ origins/call) on a 6-core/12-thread machine.
- Geometry simplification barely moves the needle on size (dominant cost is
  the *number* of origin×time×cutoff×variant records, not per-record weight);
  gzip/simplify together got ~2-3 KB per (origin × hour × variant) unit
  (bundling all 3 cutoffs), vs. ~18 KB raw.
- Settled budget: 500m origins (1479) × 17 hourly steps × 2 variants =
  50,286 units ≈ 40-45 min compute — chosen explicitly by the user over
  denser-origins/coarser-time alternatives after seeing the dry-run numbers.
  **Actual measured output: 644MB as simplified GeoJSON** (322MB/variant,
  1479 files × ~51 features each) — larger than the dry-run's ~100-150MB
  extrapolation, because real departure times (rush hour especially) produce
  more complex/fragmented polygons than the small dry-run sample suggested.
  Geometry simplification tolerance barely moves this (tested 5m→60m: only
  ~26-48% size reduction) since the dominant cost is record *count*, not
  per-record weight.
- **Geobuf cut this to 124MB** (61.3MB/variant) with zero visual/precision
  loss — measured directly on this dataset (not just trusting the format's
  marketing numbers): geobuf is 18.3% of raw GeoJSON size, and even after
  gzip (which is what actually crosses the network, applied automatically by
  GitHub Pages) it's still 46.6% of gzipped-GeoJSON size — i.e. roughly
  2.1x smaller than shipping gzipped GeoJSON would already have been, on top
  of a 5.2x smaller on-disk/git footprint. This was the single highest-leverage
  optimization found — see step 5 in the pipeline above. Considered and
  rejected: (a) adjacent-hour geometry dedup — measured only ~26% of
  adjacent-hour pairs are byte-identical on this data, not worth the added
  decode complexity in `app.js` for that return; (b) vector tiles/PMTiles —
  designed for one large spatially-tiled dataset rendered all at once (e.g. a
  basemap), doesn't fit our access pattern (fetch one origin's full time
  series on hover/click, nothing spatially adjacent needed at once); (c)
  FlatGeobuf — binary + spatial index, but the spatial index buys nothing
  here since each per-origin file is already the unit of fetch (no
  within-file spatial subsetting happens), so it would only match geobuf's
  size at best while requiring a heavier browser reader.
- The qgis-mcp bridge's `execute_code` on the real 445MB/75k-feature RT
  GeoPackage confirmed the exact failure mode `tools/analysis/
  generate_isochrones_multi_city.py`'s docstring already warned about: the
  tool call reported "failed" after its own timeout, but QGIS kept running
  the simplify+write server-side regardless (the output file kept growing
  for minutes after the "failure"). Switched to calling `ogr2ogr.exe`
  directly (found under a QGIS install's `bin/`) for this step instead —
  same GEOS simplification, runs as a plain subprocess with no bridge
  timeout to race against.
