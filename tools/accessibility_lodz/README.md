# tools/accessibility_lodz — transit accessibility pilot, Łódź

**Standalone research tooling**, not part of the plugin. Pilot city for a transit-accessibility
study built on [r5r](https://github.com/ipeaGIT/r5r) (R5 routing engine via R): first general
service accessibility vs. the income index from `tools/ses_income_lodz/`, then narrowed to
student-age population (20-29) and university buildings specifically. Generalized afterwards to
5 more cities in `tools/accessibility_cities/` — this folder is the source of truth for Łódź,
copied (not re-derived) into that folder's `lodz/` subfolder for cross-city comparison.

Generated data (`network_data/` r5r build cache, `.gpkg`, `.csv`, logs) is gitignored — see
`HANDOFF.md`/`STUDENTS_ANALYSIS.md` for exact commands to regenerate.

## Read these in order

1. **[`RESEARCH_LOG.md`](RESEARCH_LOG.md)** — chronological narrative of the whole study
   (income layer, then general accessibility, then students/universities): what was tried,
   what the results were, what decisions were made and why. Start here for the story.
2. **[`HANDOFF.md`](HANDOFF.md)** — how the general service-accessibility pipeline (income vs.
   accessibility to schools/health/culture/groceries) was built technically, step by step.
3. **[`STUDENTS_ANALYSIS.md`](STUDENTS_ANALYSIS.md)** — the student/university-specific
   extension: population 20-29 as a student proxy, accessibility to the 3 Łódź universities,
   day-to-day reliability methods (P50 vs P85), the bivariate dominant-university map.
4. **[`COLUMNS.md`](COLUMNS.md)** — what every column in the output CSVs/GPKG actually means
   (easy to confuse "opportunity count" with "population covered", read before using the data).

## Where this is published

[Ile zarabia Twój obwód?](https://gisboost.github.io/analizy/dochod-obwody-spisowe/),
[Czy bieda oznacza gorszy dojazd?](https://gisboost.github.io/analizy/dostepnosc-dochod-lodz/),
[61% obszaru zamieszkanego przez studentów...](https://gisboost.github.io/analizy/dostepnosc-uczelnie/)
on the GISBoost blog, and the university-accessibility result as an interactive map in
[mapy-analizy/uczelnie-dostepnosc](https://gisboost.github.io/mapy-analizy/uczelnie-dostepnosc/)
(code: [github.com/GISBoost/mapy-analizy](https://github.com/GISBoost/mapy-analizy)).
