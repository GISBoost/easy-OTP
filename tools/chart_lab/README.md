# tools/chart_lab — interactive GUI for transit_charts

> **Standalone tool.** Not part of the QGIS plugin easy-OTP and never imported by
> `easy_otp/`. Imports [`tools/transit_charts`](../transit_charts/README.md) and, through it,
> [`tools/family_a_reconstruction`](../family_a_reconstruction/README.md) by path.

A local, browser-based GUI for `transit_charts chart`, for people who don't want a
terminal or to remember CLI flags: pick a chart, adjust its parameters, see the result. All
15 `transit_charts` charts are available, with the exact parameter set each one needs shown
automatically (driven by `transit_charts/registry.py` — a new chart added there needs no
change here to appear).

**What this is not:** it does not run `extract`/`match`/`build`/`record`, and it does not
touch the cloud pipeline (the Termux phone, `easy-GTFS-RT` Actions, `gtfs-dashboard`) in any
way — it is a pure consumer of already-published tidy tables.

**Data sources**, any combination active at once:
- The bundled example (Łódź, 2026-07-23, 7 routes) — active by default, zero setup.
- Your own tidy table file, produced by `transit_charts extract` (upload button).
- `gtfs-dashboard`'s published catalogue of already-recorded city-days (fetched from its
  `manifest.json` on GitHub Pages — never the GitHub REST API — then downloaded and cached
  locally on first use).

## Running from source (Windows)

```bat
cd tools\chart_lab
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
py -m chart_lab.app
```

## Prebuilt Windows executable

Windows builds are published as GitHub Releases (tag prefix `chart_lab-v*`) — see the
repo's Releases page. Download, unzip, run `chart_lab.exe`; no Python installation needed.

### Building it yourself

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
