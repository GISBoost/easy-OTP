"""Optional second backend: a self-contained interactive page over the same sidecar data.

Not a replacement for the PNG. The PNG is what goes into a paper; this is for the half hour
before that, when the question is "what is that bump at 13:15 and how many observations is it
built on" - which a static figure cannot answer and a hover tooltip can.

Constraints, both deliberate:

- **One file, no network.** All CSS and JS are inline and the reference PNG is embedded as a
  data URI. The page has to keep working from a USB stick, an email attachment, or a machine
  with no internet, because that is where figures actually get looked at.
- **Same numbers as the PNG.** It renders the sidecar table the figure already wrote, so the
  two cannot drift. If they ever disagree, the sidecar is the source of truth for both.
"""
from __future__ import annotations

import base64
import html as html_escape
import json
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class HtmlSpec:
    """How to draw this chart's sidecar interactively. Charts declare it; html.py obeys."""

    x: str
    x_label: str
    y_label: str
    series: list[tuple[str, str, str]] = field(default_factory=list)   # (column, label, colour)
    bands: list[tuple[str, str, str]] = field(default_factory=list)    # (low, high, colour)
    group: str | None = None
    x_is_time_of_day: bool = False


_CSS = """
:root { color-scheme: light dark; }
body { font: 14px/1.5 system-ui, sans-serif; margin: 0 auto; padding: 24px; max-width: 1180px; }
h1 { font-size: 20px; margin: 0 0 4px; }
.sub { color: #666; font-size: 12px; margin-bottom: 18px; }
.panel { border: 1px solid #ddd; border-radius: 6px; padding: 12px; margin-bottom: 18px; }
.notes li { color: #555; font-size: 12px; }
svg { width: 100%; height: 260px; display: block; }
.axis { stroke: #bbb; stroke-width: 1; }
.grid { stroke: #eee; stroke-width: 1; }
.tick { fill: #777; font-size: 10px; }
#tip { position: fixed; pointer-events: none; background: #111; color: #fff; padding: 6px 8px;
       border-radius: 4px; font-size: 11px; display: none; z-index: 10; white-space: pre; }
table { border-collapse: collapse; width: 100%; font-size: 12px; }
th, td { border-bottom: 1px solid #eee; padding: 3px 6px; text-align: right; }
th { cursor: pointer; position: sticky; top: 0; background: #fafafa; text-align: right; }
th:first-child, td:first-child { text-align: left; }
.wrap { max-height: 340px; overflow: auto; }
img { max-width: 100%; border: 1px solid #ddd; border-radius: 4px; }
@media (prefers-color-scheme: dark) {
  body { background: #16181c; color: #e6e6e6; }
  .panel, img { border-color: #333; } th { background: #1e2126; } th, td { border-color: #2a2d33; }
  .grid { stroke: #24272c; } .axis { stroke: #555; } .tick { fill: #999; }
}
"""

_JS = """
const D = window.__CHART__;
const fmt = v => (v === null || v === undefined || Number.isNaN(v)) ? '-' :
  (Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(2));
const timeLabel = m => String(Math.floor(m / 60) % 24).padStart(2, '0') + ':' +
  String(Math.round(m) % 60).padStart(2, '0');

function draw(container, rows, spec) {
  const W = 1100, H = 260, P = {t: 12, r: 14, b: 26, l: 52};
  const xs = rows.map(r => r[spec.x]).filter(v => v !== null);
  const values = [];
  for (const s of spec.series) for (const r of rows) if (r[s[0]] !== null) values.push(r[s[0]]);
  for (const b of spec.bands) for (const r of rows) {
    if (r[b[0]] !== null) values.push(r[b[0]]);
    if (r[b[1]] !== null) values.push(r[b[1]]);
  }
  if (!xs.length || !values.length) return;
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  let y0 = Math.min(...values), y1 = Math.max(...values);
  if (y0 === y1) { y0 -= 1; y1 += 1; }
  const pad = (y1 - y0) * 0.08; y0 -= pad; y1 += pad;
  const sx = v => P.l + (v - x0) / (x1 - x0 || 1) * (W - P.l - P.r);
  const sy = v => H - P.b - (v - y0) / (y1 - y0) * (H - P.t - P.b);

  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  const add = (tag, attrs, text) => {
    const el = document.createElementNS(ns, tag);
    for (const k in attrs) el.setAttribute(k, attrs[k]);
    if (text !== undefined) el.textContent = text;
    svg.appendChild(el); return el;
  };

  for (let i = 0; i <= 4; i++) {
    const v = y0 + (y1 - y0) * i / 4;
    add('line', {class: 'grid', x1: P.l, x2: W - P.r, y1: sy(v), y2: sy(v)});
    add('text', {class: 'tick', x: P.l - 6, y: sy(v) + 3, 'text-anchor': 'end'}, fmt(v));
  }
  for (let i = 0; i <= 6; i++) {
    const v = x0 + (x1 - x0) * i / 6;
    add('text', {class: 'tick', x: sx(v), y: H - 8, 'text-anchor': 'middle'},
        spec.x_is_time_of_day ? timeLabel(v) : fmt(v));
  }
  add('line', {class: 'axis', x1: P.l, x2: W - P.r, y1: sy(Math.max(y0, Math.min(y1, 0))),
               y2: sy(Math.max(y0, Math.min(y1, 0)))});

  const sorted = rows.slice().sort((a, b) => a[spec.x] - b[spec.x]);
  for (const [lo, hi, colour] of spec.bands) {
    const usable = sorted.filter(r => r[lo] !== null && r[hi] !== null);
    if (usable.length < 2) continue;
    const up = usable.map(r => `${sx(r[spec.x])},${sy(r[hi])}`).join(' ');
    const down = usable.slice().reverse().map(r => `${sx(r[spec.x])},${sy(r[lo])}`).join(' ');
    add('polygon', {points: `${up} ${down}`, fill: colour, 'fill-opacity': 0.22});
  }
  for (const [col, , colour] of spec.series) {
    const usable = sorted.filter(r => r[col] !== null);
    if (usable.length < 2) continue;
    add('polyline', {points: usable.map(r => `${sx(r[spec.x])},${sy(r[col])}`).join(' '),
                     fill: 'none', stroke: colour, 'stroke-width': 2});
  }

  const tip = document.getElementById('tip');
  svg.addEventListener('mousemove', ev => {
    const box = svg.getBoundingClientRect();
    const vx = x0 + ((ev.clientX - box.left) / box.width * W - P.l) / (W - P.l - P.r) * (x1 - x0);
    let best = sorted[0];
    for (const r of sorted) if (Math.abs(r[spec.x] - vx) < Math.abs(best[spec.x] - vx)) best = r;
    const head = spec.x_is_time_of_day ? timeLabel(best[spec.x]) : `${spec.x} ${best[spec.x]}`;
    const lines = [head];
    if (best.n !== undefined) lines.push(`n = ${best.n}`);
    for (const [col, label] of spec.series) lines.push(`${label}: ${fmt(best[col])}`);
    for (const [lo, hi] of spec.bands) lines.push(`${lo}-${hi}: ${fmt(best[lo])} .. ${fmt(best[hi])}`);
    tip.textContent = lines.join('\\n');
    tip.style.display = 'block';
    tip.style.left = (ev.clientX + 14) + 'px';
    tip.style.top = (ev.clientY + 14) + 'px';
  });
  svg.addEventListener('mouseleave', () => { tip.style.display = 'none'; });
  container.appendChild(svg);
}

const charts = document.getElementById('charts');
const groups = D.spec.group
  ? [...new Set(D.rows.map(r => r[D.spec.group]))].sort()
  : [null];
for (const g of groups) {
  const panel = document.createElement('div');
  panel.className = 'panel';
  const title = document.createElement('div');
  title.textContent = (g === null ? D.spec.y_label : `${D.spec.group} ${g} — ${D.spec.y_label}`);
  title.style.fontWeight = '600';
  panel.appendChild(title);
  charts.appendChild(panel);
  draw(panel, g === null ? D.rows : D.rows.filter(r => r[D.spec.group] === g), D.spec);
}

// Table sorting: click a header, click again to reverse. Enough to answer "which bucket was
// worst" without leaving the page.
const table = document.getElementById('data');
let sortDesc = {};
table.querySelectorAll('th').forEach((th, i) => th.addEventListener('click', () => {
  const body = table.tBodies[0];
  const rows = [...body.rows];
  sortDesc[i] = !sortDesc[i];
  rows.sort((a, b) => {
    const x = a.cells[i].dataset.v, y = b.cells[i].dataset.v;
    const nx = parseFloat(x), ny = parseFloat(y);
    const cmp = (isNaN(nx) || isNaN(ny)) ? String(x).localeCompare(String(y)) : nx - ny;
    return sortDesc[i] ? -cmp : cmp;
  });
  rows.forEach(r => body.appendChild(r));
}));
"""


def _safe_json(payload) -> str:
    """JSON for inline <script>. A stop called `</script>` would otherwise close the block."""
    return json.dumps(payload).replace("</", "<\/")


def write(
    *,
    out_path: Path,
    png_path: Path,
    data: pd.DataFrame,
    params,
    spec: HtmlSpec,
) -> Path:
    """Emit the standalone page. Returns the path written."""
    payload = {
        "spec": {
            "x": spec.x, "x_label": spec.x_label, "y_label": spec.y_label,
            "series": [list(s) for s in spec.series],
            "bands": [list(b) for b in spec.bands],
            "group": spec.group, "x_is_time_of_day": spec.x_is_time_of_day,
        },
        "rows": json.loads(data.to_json(orient="records")),
    }
    png_b64 = base64.b64encode(png_path.read_bytes()).decode("ascii")
    escape = html_escape.escape

    head = "".join(f"<th>{escape(c)}</th>" for c in data.columns)
    body = "".join(
        "<tr>" + "".join(
            f'<td data-v="{escape(str(v))}">{escape("" if pd.isna(v) else str(v))}</td>'
            for v in row
        ) + "</tr>"
        for row in data.itertuples(index=False)
    )
    notes = "".join(f"<li>{escape(n)}</li>" for n in params.notes)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{escape(params.chart)} · transit_charts</title><style>{_CSS}</style></head><body>"
        f"<h1>{escape(params.chart)}</h1>"
        f"<div class=\"sub\">source {escape(params.source)} · sha256 "
        f"{escape(params.source_sha256)} · {params.source_rows:,} rows</div>"
        "<div id=\"tip\"></div>"
        "<div id=\"charts\"></div>"
        f"<div class=\"panel\"><ul class=\"notes\">{notes}</ul>"
        f"<pre style=\"font-size:11px;color:#666\">{escape(json.dumps(params.options, indent=2, default=str))}</pre></div>"
        f"<div class=\"panel\"><div class=\"wrap\"><table id=\"data\"><thead><tr>{head}</tr></thead>"
        f"<tbody>{body}</tbody></table></div></div>"
        "<div class=\"panel\"><div style=\"font-weight:600;margin-bottom:6px\">"
        "reference figure (identical numbers)</div>"
        f"<img alt=\"{escape(params.chart)}\" src=\"data:image/png;base64,{png_b64}\"></div>"
        "<script>window.__CHART__=" + _safe_json(payload) + ";</script>"
        f"<script>{_JS}</script>"
        "</body></html>",
        encoding="utf-8",
    )
    return out_path
