"""Bounded-height display of a chart PNG for the GUI.

Measured on real charts (Gradio 6, 2048x1030 viewport): a plain gr.Image shows the PNG at its
native size, so H29/H30 (1800x5302 / 1950x6363 px - one row per route) made the page ~7800 px
tall; `gr.Image(height=700)` fixes that by shrinking to fit, but then a tall chart ends up
237 px wide and unreadable. So:
  - wide-ish charts (height/width <= FIT_MAX_ASPECT): fitted whole into MAX_VH of the viewport;
  - taller charts: full container width, inside a box of MAX_VH that scrolls on its own,
    so the page itself never gets longer than one screen for the chart.
"""
from __future__ import annotations

import base64
import struct
from pathlib import Path

MAX_VH = 80
# D15 (0.75), C10 (0.65), J39 (0.46) fit; B8 (0.86) and the per-route heatmaps scroll.
FIT_MAX_ASPECT = 0.8


def png_size(path: Path) -> tuple[int, int]:
    """(width, height) from the PNG header - no image library needed."""
    with open(path, "rb") as f:
        head = f.read(24)
    return struct.unpack(">II", head[16:24])


def chart_html(path: str | Path) -> str:
    """HTML for `path` according to the rule in the module docstring."""
    path = Path(path)
    w, h = png_size(path)
    src = "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
    if h / w <= FIT_MAX_ASPECT:
        style = f"display:block;margin:0 auto;max-width:100%;max-height:{MAX_VH}vh;width:auto;height:auto"
        return f'<img src="{src}" style="{style}" alt="chart">'
    return (
        f'<div style="max-height:{MAX_VH}vh;overflow:auto;border:1px solid var(--border-color-primary)">'
        f'<img src="{src}" style="display:block;width:100%;height:auto" alt="chart"></div>'
    )
