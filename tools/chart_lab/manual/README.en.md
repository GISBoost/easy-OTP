# chart_lab — user guide

English version of [README.md](README.md) (Polish). Keep the two in sync.

`chart_lab` is a local app with a browser interface that replaces the terminal when working with
the tools in `tools/`: it draws punctuality and regularity charts and, since version 0.2, it can
also build the data from scratch out of a GTFS-RT recording. It runs on your computer — nothing is
sent over the network except downloads from the online catalogue (see
[§ 8](#8-where-to-get-data-gtfs-dashboard)).

> Worked analyses with ready-made charts: [EXAMPLES.md](EXAMPLES.md) (Polish).
> Technical write-up and design decisions: `docs/prd/PR_easy-OTP_chart_lab_v02.md` (kept locally).

## 1. Running it

**Ready-made Windows build** (no Python needed): download `chart_lab-windows.zip` from the
repository's *Releases* tab (tag `chart_lab-v*`), unzip it and run `chart_lab.exe`. A console
window opens (leave it open — it is the app's "server") together with a browser tab.

**From source:**

```bat
cd tools\chart_lab
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
py -m chart_lab.app
```

The app lives at `http://127.0.0.1:7860`. Closing the console window stops the app.

## 2. What is in the app

| Tab | What for | CLI equivalent |
|---|---|---|
| **Charts** | 16 charts of punctuality, regularity, bunching and speed | `transit_charts chart` |
| **Pipeline** | from a recording and a static GTFS to a ready tidy table | `family_a match` → `family_a build` → `transit_charts extract` |
| **Stop headway** | per-stop headways for the hex map + the H31 chart | `transit_charts stop-headway` |
| **Realized (TripUpdates)** | realized GTFS from a TripUpdates archive (e.g. ŁKA) | `family_b_realized/build_realized.py` |
| **Diagnose RT** | does a static GTFS match a live RT feed? | `rt_diagnose/compare_rt_vs_static.py` |

What the app does **not** do: it does not record feeds (`record` — the phone running Termux does
that), it does not run the scripts in `tools/analysis/`, and it does not touch the cloud pipeline
(`easy-GTFS-RT`).

The forms in the data tabs are generated from the CLI parsers, so they have exactly the same
options and descriptions as the terminal commands. A field marked `*` is required; the rest of
the options sit in the collapsed **Advanced** panel. A field that is empty or equal to the CLI
default is not passed to the command at all — behaviour is the same as running the CLI without
that flag.

## 3. The Charts tab

1. **Data.** The bundled example table (Łódź, 2026-07-23) is active by default. You can add your
   own tidy table (drop a `.csv`, `.csv.gz` or `.parquet`) or download a ready one from the
   **Online catalogue** panel. All ticked tables are used together by every chart.
2. **Chart.** Pick a chart; one sentence under the list says what it shows. Only the parameters
   that chart uses are visible.
3. **Parameters.** Changing any of them redraws the chart immediately (there is no "generate"
   button). Routes and direction are picked by clicking buttons. Hover a label for its description.
4. **Downloads** below the chart: the PNG, a CSV with the plotted numbers and a JSON with the
   parameters (plus HTML for C9/C10/B6 if you tick "Also write interactive HTML"). Files are
   written to `%TEMP%\chart_lab_output` (the button next to it opens that folder).
   A chart takes at most 80% of the window height on the page: wide ones fit whole, tall ones
   (e.g. H29/H30 with many routes) scroll inside their own panel instead of lengthening the page.

Data requirements:

| Chart | Needs |
|---|---|
| most (C9, C10, C11, A2, B5–B8, D14, D17, H28–H30) | 1 table (one day of one city) |
| **D15** (systematic vs random delay) | ≥ 3 tables from **different days** |
| **E20, J39** (cross-city comparisons) | ≥ 2 tables from **different cities** |

When there are too few tables a ⚠️ message says what is missing instead of drawing the chart.

**Bunching:** B8 (stop × hour, one route) and H30 (route × hour, whole city) compute the share of
headways shorter than `Bunching threshold` (default 0.25) of **that stop pair's own scheduled
headway** — a fraction, not minutes, so a 5-minute and a 20-minute route are comparable. Cells
with fewer than 3 headways are hatched (no data).

## 4. The Pipeline tab — from a recording to a tidy table

Use it when you have **your own recording** of VehiclePositions (a folder of
`snapshot_YYYYmmdd-HHMMSS.pb` files) and a static GTFS. If a ready day from the online catalogue
is enough, you do not need this tab.

1. Type the **City** (a label stored in the table, e.g. `lodz`).
2. Point to the **Recording folder(s)**: one folder per line. Several folders = merging several
   days (`--positions-dir` with multiple values). The *Add recording folder…* button opens a
   system dialog.
3. Point to the **Static GTFS .zip** (*Browse…*). **It must be the same publication that was in
   force on the recording day** — `trip_id`s of a different edition do not match and the result is
   worthless (see the FA-15/FA-16 warnings below).
4. **Work folder** (optional): where to write the results. Empty = `~/Documents/chart_lab/<city>/`.
5. Click **Run all**, or run the steps one by one: **Run match → Run build → Run extract**. Each
   step's output is the next step's input; you retype nothing.

Files in the work folder:

| File | Step | Contents |
|---|---|---|
| `matched.csv` | match | vehicle positions matched to route shapes |
| `realized_p50.zip`, `realized_p85.zip` | build | realized GTFS (median and P85 travel times) |
| `<city>_tidy.csv.gz` | extract | the tidy table for the charts; when finished it joins the active tables in **Charts** |

The **options** panels of each step are the CLI flags; in `extract` the `--route` field limits the
table to chosen routes (`55*` = everything starting with `55`), which speeds that step up a lot.

**Quality warnings.** A "Quality warnings — read before trusting the output" list appears above
the log and the status icon changes from ✅ to ⚠️. Do not ignore it:

- `WARNING (FA-15)` — a large share of observations rejected, or routes that had observations but
  none was accepted (such routes later look perfectly on time because they keep the schedule), or
  too few routes got any correction at all.
- `WARNING (FA-16)` — the static GTFS does not recognise most `trip_id`s of the table: almost
  certainly a different GTFS edition than the one `match` was run against. Recompute with the
  right file.
- "N library warning(s) in the log" — hundreds of repetitive warnings from the matching module
  (e.g. `FA-12 windowed search found nothing`); they are collapsed to one line, the full list is in
  the log.

Time and memory (one Łódź day, ~960 snapshots every 30 s): `match` about 1.5 min and up to ~1 GB
RAM, `build` about 2 min and ~0.9 GB. Memory returns to the system after every job.

## 5. The Stop headway tab

Point to `matched.csv`, the static GTFS, the city and an output prefix. It produces
`<prefix>_stops.csv` (stop, coordinates, `n`, median headway — input for the hex map) and the chart
`<prefix>_H31.png/.csv/.json` (city-wide headways through the day). It always uses the whole feed —
a route filter would understate the frequency of stops served by several routes.

## 6. The Realized (TripUpdates) tab

For operators without VehiclePositions (e.g. ŁKA): from an archive of TripUpdates snapshots
(`polish_trains_updates_<day>.pb[.gz]`) it builds a realized GTFS `<prefix>_p50.zip` and
`<prefix>_p85.zip`. Fields: the snapshots folder, the static GTFS from the recording day, the
output prefix; optionally `--service-days` (YYYY-MM-DD, comma-separated) and `--agency-id`
(default `LKA`; an empty field = no filter, the whole national network). The tool exits with code 0
even when its own checks print `PROBLEM` — the tab then adds a note to check the `[p50]`/`[p85]`
lines in the log.

## 7. The Diagnose RT tab

Two fields: a TripUpdates `.pb` file and a static GTFS `.zip`. The result is a verdict:
**EXACT-MATCH POSSIBLE** (the static GTFS matches the feed), **FUZZY POSSIBLE** (`trip_id`s
differ, but trips can be matched by `route_id` and start time) or **NEITHER** (the pair is not
usable for RT in OTP; use recording and reconstruction instead).

## 8. Where to get data (gtfs-dashboard)

The recordings catalogue is <https://gisboost.github.io/gtfs-dashboard/>. The data is published
by `easy-GTFS-RT` as daily releases; the site reads them from
[`manifest.json`](https://gisboost.github.io/gtfs-dashboard/manifest.json) (state of 2026-10-03:
28 cities, 1799 days, 1578 tidy tables; refreshed daily). `chart_lab` uses only that manifest and
the file URLs inside it (never the GitHub API, which is limited to 60 requests/h).

For every city and day the catalogue has these files:

| File (manifest field) | Size | What for |
|---|---|---|
| **tidy table** `<city>_tidy_<date>.csv.gz` (`tidy_table`) | 9–20 MB | **all charts in chart_lab**; your own pandas analyses |
| realized GTFS `…_p50.zip`, `…_p85.zip` | on the order of 10 MB (varies by city) | routing (OTP/R5), the easy-OTP plugin — "how it really ran" accessibility analyses |
| static GTFS `…_static_gtfs_<date>.zip` | varies by city | pair for the realized feed; needed for your own `extract`/`stop-headway` |
| `…_diff_<date>_p50_summary.csv`, `…_chart.png` | small | ready-made delay summary of the day |
| raw snapshots `<city>_snapshots_<YYYY-MM>.tar.xz` | 10–390 MB | only if you want your own `match` with other parameters |

Availability notes:

- A **tidy table** exists from 2026-08-03; earlier days only have realized and static GTFS. ŁKA
  (`lka`) has no tidy tables at all — it is a TripUpdates source (the Realized tab).
- A day's `status` field: `ok` — full recording window; `partial` — gaps in the recording
  (`coverage_ranges` tells which hours are covered, e.g. `08:52-22:00`). Prefer `ok` days for
  comparisons.

**How to download in the app:** the **Charts → Online catalogue** tab → *1. Fetch available
cities* → pick City → Month → Day → *2. Download and add to active tables*. The file goes to the
cache (`%TEMP%\chart_lab_cache`), so using it again is instant. By hand: on the catalogue page
pick a city and a day and download the files from the list.

**What to download for which analysis:**

| You want to see… | Download |
|---|---|
| punctuality, regularity, bunching of one city on one day | 1 tidy table |
| whether delay is systematic or random (D15) | tidy tables of one route from ≥ 3 days (preferably `ok`) |
| a cross-city comparison (E20, J39) | 1 tidy table per city, preferably from **the same date** |
| working week vs weekend | several days of each type, same calendar |
| impact of delays on accessibility (OTP/R5) | realized p50/p85 + the static GTFS of that day |

## 9. Troubleshooting

| Symptom | What to do |
|---|---|
| The browser tab does not open | open `http://127.0.0.1:7860` by hand (if the port is busy Gradio picks the next one — the address is in the console) |
| The tab "freezes" (e.g. after a very long log) | reload the page; the job's results are in the work folder anyway |
| "Another job is already running" | one job runs at a time — wait or click **Cancel** |
| Cancel seems to do nothing between "Run all" steps | the click is remembered and stops the next step before it starts |
| `matched.csv not found - run match first` | run **match** first, or point to the same work folder as before |
| `FA-16` / almost everything rejected | wrong static GTFS for this recording — download the edition from the recording day |
| The *Realized* tab says "unavailable" | `gtfs-realtime-bindings` or the `easy_otp` module is missing — `pip install -r requirements.txt` |
| The *Browse…* window is not visible | it can be behind other windows; type the path by hand (quotes and trailing spaces are stripped) |
| D15/E20/J39: "needs at least…" | add more tables (days or cities), see § 3 |

Paths: downloaded tables — `%TEMP%\chart_lab_cache`; chart outputs — `%TEMP%\chart_lab_output`;
Pipeline outputs — the work folder. None of them is inside the repository.

## 10. License and sources

GPL-3.0-or-later (the app imports `transit_charts` and `family_a` code directly). Source:
<https://github.com/GISBoost/easy-OTP/tree/main/tools/chart_lab>. Reconstruction methodology:
[`tools/family_a_reconstruction/README.md`](../../family_a_reconstruction/README.md), chart
descriptions: [`tools/transit_charts/README.en.md`](../../transit_charts/README.en.md).
