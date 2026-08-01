"""Shared rendering scaffolding: backend, palette, axes, and the sidecar contract.

Three things live here because each of them is a promise that has to hold for *every* figure,
and a promise kept in four places is a promise waiting to be broken in a fifth.

- **Headless.** `matplotlib.use("Agg")` runs on import, before any `pyplot` import anywhere.
- **Deterministic colour.** A route's colour comes from its position in the sorted list of
  routes in the figure, never from iteration order, so two runs of the same command produce
  the same picture and a reader can compare yesterday's PNG with today's.
- **Every figure ships its numbers.** `save()` writes `<prefix>.png`, `<prefix>.csv` with
  exactly the values plotted, and `<prefix>.json` with the parameters and input fingerprint.
  A figure whose numbers cannot be re-read is not evidence, and this work is headed for a
  doctorate where that distinction matters.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # noqa: E402 - must precede pyplot, hence the placement

import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker as mticker  # noqa: E402
import pandas as pd  # noqa: E402

# Okabe-Ito, chosen for colour-vision deficiency safety rather than for looks. Long enough for
# the handful of routes any single figure should carry; more than this and the chart is the
# wrong tool anyway.
PALETTE = [
    "#0072B2", "#D55E00", "#009E73", "#CC79A7",
    "#E69F00", "#56B4E9", "#F0E442", "#000000",
]

GRID_KW = dict(alpha=0.25, linewidth=0.6)
THIN_COLOUR = "#BBBBBB"


@dataclass
class ChartResult:
    """Where a figure's artefacts landed. `html` is present only when one was asked for."""

    png: Path
    csv: Path
    json: Path
    html: Path | None = None


@dataclass
class ChartParams:
    """Everything needed to reproduce a figure, serialised beside it.

    `source` and `source_sha256` fingerprint the tidy table actually read, so a figure can
    always be traced back to the extraction that produced it - including which filters that
    extraction ran with.
    """

    chart: str
    source: str
    source_sha256: str
    source_rows: int
    options: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        payload = {
            "chart": self.chart,
            "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": self.source,
            "source_sha256": self.source_sha256,
            "source_rows": self.source_rows,
            "options": self.options,
            "notes": self.notes,
        }
        return json.dumps(payload, indent=2, default=str, ensure_ascii=False)


def chart_params(chart: str, sources, rows: int, options: dict, notes: list[str]) -> ChartParams:
    """Build the sidecar metadata, fingerprinting EVERY input table.

    `sources` may be one path or several. Charts accept repeated --table and concatenate, so a
    sidecar that recorded only the first would assert a provenance the figure does not have.
    """
    paths = [Path(s) for s in (sources if isinstance(sources, (list, tuple)) else [sources])]
    return ChartParams(
        chart=chart,
        source=", ".join(str(p) for p in paths),
        source_sha256=",".join(fingerprint(p) for p in paths),
        source_rows=rows,
        options=options,
        notes=notes,
    )


def fingerprint(path: Path) -> str:
    """First 16 hex chars of the file's SHA-256 - enough to tell two extractions apart."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()[:16]


def new_figure(width: float = 12.0, height: float = 6.0):
    fig, ax = plt.subplots(figsize=(width, height))
    ax.grid(True, **GRID_KW)
    ax.set_axisbelow(True)
    return fig, ax


def colour_for(keys: list[str]) -> dict[str, str]:
    """Stable colour per key, assigned by sorted order (see the module docstring)."""
    return {key: PALETTE[i % len(PALETTE)] for i, key in enumerate(sorted(keys))}


def time_of_day_axis(ax, minute_ticks: int = 60) -> None:
    """Format an x axis carrying minutes-since-local-midnight as HH:MM."""
    ax.xaxis.set_major_formatter(
        mticker.FuncFormatter(lambda v, _pos: f"{int(v) // 60 % 24:02d}:{int(v) % 60:02d}")
    )
    ax.xaxis.set_major_locator(mticker.MultipleLocator(minute_ticks))
    ax.tick_params(axis="x", rotation=45)


MINUTE = 60.0


def to_minutes(seconds):
    """Seconds -> minutes. Delay and headway are read by humans, and humans think in minutes.

    Applied at the *rendering* boundary only: the tidy table stays in seconds, which is the
    unit every threshold in family_a is expressed in, so there is exactly one conversion point
    rather than a unit question at every call site.
    """
    return seconds / MINUTE


def on_full_bucket_grid(part, x: str, bucket_minutes: int):
    """Reindex a per-bucket series onto the full regular grid, inserting NaN for the gaps.

    Without this, matplotlib joins the points either side of a suppressed or missing bucket
    with a straight line - so a bucket marked "insufficient data" on the axis is simultaneously
    drawn as an interpolated value, which is worse than leaving it out.
    """
    import numpy as np

    if part.empty:
        return part
    low, high = int(part[x].min()), int(part[x].max())
    grid = np.arange(low, high + bucket_minutes, bucket_minutes)
    return part.set_index(x).reindex(grid).rename_axis(x).reset_index()


def mark_thin_buckets(ax, x_values, thin_mask, label: str | None = None) -> None:
    """Draw a visible marker where a bucket fell below min_n.

    A hole in a line chart reads as zero, or as "nothing happened". An explicit mark reads as
    "not enough data", which is the truth.

    *label* puts it in the legend. Without one the marks look like stray ink - which is exactly
    how they read to the first person who saw them, so the label is now the default path and
    the caption text alone is not considered sufficient explanation.
    """
    thin_x = [x for x, thin in zip(x_values, thin_mask) if thin]
    if not thin_x:
        return
    ax.plot(
        thin_x,
        [ax.get_ylim()[0]] * len(thin_x),
        marker="^", linestyle="none", color=THIN_COLOUR, markersize=5,
        label=label if label else "_nolegend_",
    )


def thin_legend_handle(min_n: int):
    """Proxy artist so the 'insufficient data' marker can be named even on an axes that has
    none - otherwise the legend changes shape depending on the data, which is worse."""
    from matplotlib.lines import Line2D

    return Line2D(
        [], [], marker="^", linestyle="none", color=THIN_COLOUR, markersize=5,
        label=f"n < {min_n} (not plotted)",
    )


def hatch_thin_cells(ax, thin_positions) -> None:
    """Hatch heatmap cells that fell below min_n - the 2-D counterpart of the rug above."""
    for x, y in thin_positions:
        ax.add_patch(
            plt.Rectangle(
                (x - 0.5, y - 0.5), 1, 1,
                fill=False, hatch="///", edgecolor=THIN_COLOUR, linewidth=0.0,
            )
        )


def thin_grid_warning(chart: str, counts, min_n: int, threshold: float = 0.5) -> str | None:
    """Return a warning when a grid chart is mostly suppressed, else None.

    Written after D14 rendered 14 usable cells out of 424 and looked, at a glance, like a route
    with almost no data. The data were fine: line 11 runs ~4 vehicles an hour, so a segment-hour
    cell can never reach a threshold of 5. A chart in that state must say why it is empty and
    what would fix it - silently drawing a mostly-blank grid invites the reader to conclude
    something about the service instead of about the parameters.
    """
    import statistics

    values = [int(c) for c in counts if c == c]
    if not values:
        return f"{chart}: no cell had any observation at all"
    suppressed = sum(1 for c in values if c < min_n) / len(values)
    if suppressed < threshold:
        return None
    achievable = statistics.median(values)
    return (
        f"{chart}: {suppressed:.0%} of cells fall below min_n={min_n}. The median cell has "
        f"n={achievable:.0f} at this granularity, so the threshold is unreachable here - widen "
        f"--bucket-minutes or lower --min-n."
    )


CAPTION_FONTSIZE = 7.5


def caption(ax, lines: list[str], width_chars: int = 150) -> None:
    """Put the provenance under the axes: window, filters, n, and the known biases.

    Deliberately part of the figure rather than of the surrounding prose. These PNGs end up
    pasted into documents and slide decks where the caveats do not travel with them - which is
    also why the text is wrapped rather than allowed to run off the right edge, as the first
    version of B6's caption did, truncating the sentence that warned about smoothing.
    """
    import textwrap

    wrapped: list[str] = []
    for line in lines:
        wrapped.extend(textwrap.wrap(line, width=width_chars) or [""])
    figure = ax.figure
    figure.text(
        0.01, 0.005, "\n".join(wrapped),
        fontsize=CAPTION_FONTSIZE, color="#555555", va="bottom", ha="left",
    )
    # Recorded so save() can reserve the room this caption actually needs. A caption is not a
    # fixed height - it grows when a chart has more to warn about - and a fixed bottom margin
    # meant the longest ones climbed over the x axis label, which is where the guard belongs.
    figure._transit_charts_caption_lines = len(wrapped)


def facet_legend(fig, handles, ncols: int) -> None:
    """Put the legend on the figure, between title and panels, never inside an axes.

    On a faceted chart an in-axes legend lands on top of the first panel's data - which is
    exactly where the eye goes first. Costs a strip of white space and buys never having to
    choose between a readable legend and a readable top panel.
    """
    fig.legend(
        handles=handles, loc="upper center", bbox_to_anchor=(0.5, 0.955),
        ncols=ncols, fontsize=8.5, framealpha=0.0,
    )


def _caption_room(fig, requested_bottom: float, ceiling: float = 0.25) -> float:
    """How much of the figure to leave below the axes, given the caption that was written.

    Scales with the number of wrapped lines and with the figure's own height, because the same
    four-line caption is a third of a short figure and a tenth of a tall one. Capped, so a
    pathologically long caption starves the text rather than the chart.
    """
    lines = getattr(fig, "_transit_charts_caption_lines", 0)
    if not lines:
        return requested_bottom
    line_height_inches = CAPTION_FONTSIZE * 1.45 / 72
    needed = 0.025 + lines * line_height_inches / fig.get_figheight()
    return min(max(requested_bottom, needed), ceiling)


def save(
    fig,
    data: pd.DataFrame,
    params: ChartParams,
    out_prefix: Path,
    rect: tuple[float, float, float, float] = (0, 0.06, 1, 1),
    html_spec=None,
) -> ChartResult:
    """Write the figure and its sidecars; returns where everything went.

    *rect* leaves room at the bottom for the caption, and at the top for a figure-level legend
    when one is used (see facet_legend).

    *html_spec* (an html.HtmlSpec) additionally emits a self-contained interactive page built
    from **the same sidecar table**, so the two renderings cannot drift apart. Charts that have
    no sensible interactive form simply pass None and only get a PNG.
    """
    rect = (rect[0], _caption_room(fig, rect[1]), rect[2], rect[3])
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    png = out_prefix.with_suffix(".png")
    csv = out_prefix.with_suffix(".csv")
    meta = out_prefix.with_suffix(".json")

    fig.tight_layout(rect=rect)
    fig.savefig(png, dpi=150)
    plt.close(fig)
    data.to_csv(csv, index=False)
    meta.write_text(params.to_json(), encoding="utf-8")

    page = None
    if html_spec is not None:
        from transit_charts.render import html as html_mod

        page = html_mod.write(
            out_path=out_prefix.with_suffix(".html"), png_path=png,
            data=data, params=params, spec=html_spec,
        )
    return ChartResult(png=png, csv=csv, json=meta, html=page)


# A recording that has not begun by this hour cannot contain the morning peak, and a figure
# that says "across the day" without saying so is overclaiming.
MORNING_PEAK_START_HOUR = 7


def window_note(table: pd.DataFrame) -> str:
    """One line describing the recording window actually present in the data.

    The missing-morning caveat is conditional, not fixed text. Most recordings in this archive
    start mid-morning, but not all of them do - and printing "no morning peak" under a chart
    that plainly shows 06:00 discredits every other caveat in the same caption.
    """
    observed = table.obs_local.dropna()
    if observed.empty:
        return "no observations in window"
    start = observed.min()
    caveat = " (no morning peak in this window)" if start.hour >= MORNING_PEAK_START_HOUR else ""
    return (
        f"recording window {start:%H:%M}-{observed.max():%H:%M} local "
        f"on {start:%Y-%m-%d}{caveat}"
    )
