# tools/ses_income_lodz — income estimation from voting-precinct data, 6 cities

**Standalone research tooling**, not part of the plugin. Polish census (NSP 2021) does not
collect income at any granularity finer than gmina, so this folder builds a **proxy income
index** per census tract (`obwod spisowy`) for 6 Polish cities: 2023 parliamentary election
results per voting precinct, weighted by party-level income data from a CBOS survey, spatially
matched onto census tract geometry. `income_index_pln` is a relative index, not a real PLN
figure — see `METHODOLOGY.md` for the full derivation and its limitations.

Output: `{city}.gpkg` per city (Łódź, Kraków, Warszawa, Poznań, Gdańsk, Szczecin), each with an
`obwody_spisowe` layer (population + `income_index_pln` + family/household fields) and an
`obwody_glosowania` layer (raw election results). GPKGs, CSVs, and downloaded source data are
gitignored — regenerate via the scripts, see `HANDOFF.md` for exact commands.

## Read these in order

1. **[`NEXT_AGENT_BRIEF.md`](NEXT_AGENT_BRIEF.md)** — orientation for a fresh session: what
   this subproject is, what's already done, which `CLAUDE.md` caveats apply here specifically.
2. **[`METHODOLOGY.md`](METHODOLOGY.md)** — what's computed and why: the MRP-style index (not
   naive ecological inference), formulas, sources, known limitations.
3. **[`HANDOFF.md`](HANDOFF.md)** — how it was actually built, step by step, so it can be
   reproduced or extended to a 7th city.
4. **[`cities_teryt.md`](cities_teryt.md)** — verified TERYT/GUS/KBW code reference table for
   the 6 cities, used to join census and election data correctly.

## Where this feeds into

`tools/accessibility_lodz/` and `tools/accessibility_cities/` join this income index against
transit-accessibility measurements to test whether poorer areas have worse transit access
(inspired by Braga, Loureiro & Pereira 2026, *Journal of Transport Geography*, on Fortaleza,
Brazil). Written up on the blog:
[Ile zarabia Twój obwód?](https://gisboost.github.io/analizy/dochod-obwody-spisowe/)
