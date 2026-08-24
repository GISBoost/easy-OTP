# tools/accessibility_cities — transit accessibility, 6 cities

**Standalone research tooling**, not part of the plugin. Generalizes the Łódź pilot
(`tools/accessibility_lodz/`) to the other 5 SES cities: Warszawa, Kraków, Gdańsk, Poznań,
Szczecin. Same engine (r5r/R5), same method (500 m hex grid, realized GTFS P50, 30 min cutoff),
same two questions: does income predict service accessibility, and which university is easiest
to reach from where. Method A/C (intra-day reliability) is deliberately out of scope here, kept
Łódź-only — see `MULTI_CITY_ANALYSIS.md` §Ograniczenia.

Generated data (network build caches, `.osm.pbf`, GTFS `.zip`, `.gpkg`, `.csv`, logs) is
gitignored — see `HOWTO_MANUAL.md` for exact commands to regenerate, or `run_city_pipeline.sh
<city>` to run the whole thing for one city at once.

## Read these in order

1. **[`MULTI_CITY_ANALYSIS.md`](MULTI_CITY_ANALYSIS.md)** — what's different from Łódź, the
   results across all 6 cities, and the scope limitations (no reliability method here, Kraków
   effectively 2 universities, etc.).
2. **[`HOWTO_MANUAL.md`](HOWTO_MANUAL.md)** — step-by-step manual walkthrough of the whole
   pipeline (install R5/r5r, fetch OSM/GTFS, build the grid, run accessibility, join results) —
   for adding a 7th city or changing something mid-pipeline, not just reproducing the result.

`lodz/` is a **copy** of key result files from `tools/accessibility_lodz/`, kept here only so
Łódź sits under the same naming convention as the other 5 cities for `analyze_cross_city.py`.
It has its own short README explaining that — the source of truth stays in
`tools/accessibility_lodz/`.

## Where this is published

[Czy bieda oznacza gorszy dojazd?](https://gisboost.github.io/analizy/dostepnosc-dochod-lodz/)
and [61% obszaru zamieszkanego przez studentów bez dostępu do uczelni w pół
godziny](https://gisboost.github.io/analizy/dostepnosc-uczelnie/) on the GISBoost blog, and the
university-accessibility result as an interactive map (all 6 cities, tabbed) in
[mapy-analizy/uczelnie-dostepnosc](https://gisboost.github.io/mapy-analizy/uczelnie-dostepnosc/)
(code: [github.com/GISBoost/mapy-analizy](https://github.com/GISBoost/mapy-analizy)).
