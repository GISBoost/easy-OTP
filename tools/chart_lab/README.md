# tools/chart_lab — interactive GUI for transit_charts and the data pipeline

> **Standalone tool.** Not part of the QGIS plugin easy-OTP and never imported by
> `easy_otp/`. Imports [`tools/transit_charts`](../transit_charts/README.md) and, through it,
> [`tools/family_a_reconstruction`](../family_a_reconstruction/README.md) by path; the
> extra tabs also load `tools/family_b_realized`, `tools/rt_diagnose` and
> `easy_otp/core/gtfsrt_realizer.py` (imports no QGIS) the same way.

A local, browser-based GUI for `transit_charts chart`, for people who don't want a
terminal or to remember CLI flags: pick a chart, adjust its parameters, see the result. All
16 `transit_charts` charts are available, with the exact parameter set each one needs shown
automatically (driven by `transit_charts/registry.py` — a new chart added there needs no
change here to appear).

**Tabs (v0.2):** *Charts* (the original chart GUI, below), *Pipeline* (`family_a match` →
`family_a build` → `transit_charts extract` in one form), *Stop headway* (`transit_charts
stop-headway`), *Realized (TripUpdates)* (`family_b_realized`) and *Diagnose RT*
(`rt_diagnose`). The forms are generated from the CLI parsers, so a new CLI flag appears in the
GUI without changes here. Each job runs in its own child process: it can be cancelled, and its
memory is returned to the system when it ends.

**What this is not:** it does not run `record` (the phone/Termux does that), and it does not
touch the cloud pipeline (the Termux phone, `easy-GTFS-RT` Actions, `gtfs-dashboard`) in any
way. `analysis/*` scripts are not covered either.

**Data sources**, any combination active at once:
- The bundled example (Łódź, 2026-07-23, 7 routes) — active by default, zero setup.
- Your own tidy table file, produced by `transit_charts extract` (upload button).
- `gtfs-dashboard`'s published catalogue of already-recorded city-days (fetched from its
  `manifest.json` on GitHub Pages — never the GitHub REST API — then downloaded and cached
  locally on first use).

**Documentation:** full user guide — [`manual/README.en.md`](manual/README.en.md) (Polish version:
[`manual/README.md`](manual/README.md)); worked analyses on gtfs-dashboard data (bunching, D15,
city comparison) — [`manual/EXAMPLES.md`](manual/EXAMPLES.md) (Polish).

## Running

**Option A — ready-made Windows build** (simplest, no Python needed): download `chart_lab-windows.zip` from the [Releases](https://github.com/GISBoost/easy-OTP/releases) page (latest `chart_lab-v*`), unzip the whole folder, run `chart_lab.exe`. The app opens in your browser at `http://127.0.0.1:7860`. The exe is unsigned, so SmartScreen may need *More info → Run anyway*.

**Option B — from source.** Needs Python 3.10+ and the **whole repository** (`git clone https://github.com/GISBoost/easy-OTP.git` or *Download ZIP*), because the app imports the sibling `tools/` folders. Then double-click `run_chart_lab.bat` in `tools\chart_lab` (creates a venv on first run), or by hand:
```bat
cd tools\chart_lab
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
py -m chart_lab.app
```

## Quick guide

The app is a single page, top to bottom:

1. **Data** — upload your own tidy table (`.csv`/`.csv.gz`/`.parquet` from `transit_charts
   extract`) and tick the tables to use under **Active tables**. The bundled example (Łódź,
   2026-07-23) is ticked by default; several tables can be active at once.
2. **Online catalogue** (collapsed panel) — already-recorded city-days from `gtfs-dashboard`:
   *1. Fetch available cities* → **City** → **Month** → **Day** → *2. Download and add to active
   tables* (cached locally after the first download).
3. **Chart** — pick one of the 16 charts; a one-line description appears below, and only the
   parameters that chart uses are shown.
4. **Parameters** — routes and direction are picked with buttons, the rest are sliders. Any
   change redraws the chart immediately; hover a label for its description.
5. D15 needs ≥ 3 tables from different days; E20/J39 need ≥ 2 different cities. Otherwise a ⚠️
   message says what is missing.
6. **Downloads** under the chart: PNG, CSV with the plotted numbers, JSON with parameters (plus
   HTML for C9/C10/B6 if "Also write interactive HTML" is ticked).

## Data tabs (v0.2)

- **Pipeline** — city, recording folder(s) (`snapshot_*.pb`) and static GTFS; optional work folder
  (default `~/Documents/chart_lab/<city>/`). **Run match / build / extract** run single steps,
  **Run all** runs them in order; each step's output feeds the next. The resulting tidy table is
  added to the active tables in **Charts**. **Read the quality warnings
  (`WARNING (FA-15/FA-16)`) shown above the log before trusting the output.**
- **Stop headway**, **Realized (TripUpdates)**, **Diagnose RT** — one command each: form, *Run*,
  log; the result is shown below the form.
- **Cancel** stops the current job; only one job runs at a time.

For the full details (data requirements, troubleshooting, the online catalogue) see the
[user guide](manual/README.en.md).

## Building the Windows executable yourself

```bat
cd tools\chart_lab
build_installer.bat
```

Produces `dist\chart_lab\chart_lab.exe` (a `--onedir` PyInstaller build — a folder, not a
single file, for faster startup and easier debugging of a first build). The same script runs
in CI (`.github/workflows/chart_lab_release.yml`) on every `chart_lab-v*` tag push.

`chart_lab.spec` bundles the CL-2 example data and the repo's `LICENSE` alongside the code.
It also has to work around one real PyInstaller/Gradio interaction: Gradio reads its own
`.py` source files back off disk at import time (for `.pyi` stub generation), which a normal
PyInstaller build strips down to compiled bytecode — the spec bundles gradio's raw source
via `collect_data_files("gradio", include_py_files=True)` to keep that working frozen.

## License

GPL-3.0-or-later, same as `transit_charts`/`family_a` — this package imports their code
directly (not a subprocess wrapper). Source: https://github.com/GISBoost/easy-OTP/tree/main/tools/chart_lab
