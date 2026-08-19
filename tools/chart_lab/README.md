# tools/chart_lab — interactive GUI for transit_charts

> **Standalone tool.** Not part of the QGIS plugin easy-OTP and never imported by
> `easy_otp/`. Imports [`tools/transit_charts`](../transit_charts/README.md) and, through it,
> [`tools/family_a_reconstruction`](../family_a_reconstruction/README.md) by path.

A local, browser-based GUI for `transit_charts chart`, for people who don't want a
terminal or to remember CLI flags: pick a chart, adjust its parameters, see the result.

**What this is not:** it does not run `extract`/`match`/`build`/`record`, and it does not
touch the cloud pipeline (the Termux phone, `easy-GTFS-RT` Actions, `gtfs-dashboard`) in
any way — it is a pure consumer of already-published tidy tables.

Chart-picking, data-source selection, and live parameter widgets land in later milestones
(see `docs/prd/PR_easy-OTP_chart_lab.md`). This package currently only launches an empty
placeholder page.

## Setup (Windows)

```bat
cd tools\chart_lab
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
py -m chart_lab.app
```

## License

GPL-3.0-or-later, same as `transit_charts`/`family_a` — this package imports their code
directly (not a subprocess wrapper).
