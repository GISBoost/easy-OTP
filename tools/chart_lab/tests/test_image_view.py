"""Unit tests for chart_lab.image_view: how a chart PNG is bounded in the page.

    cd tools/chart_lab
    py -m pytest tests/test_image_view.py -q
"""
from __future__ import annotations

import pytest

from chart_lab import image_view

# Real chart sizes measured in the GUI (width, height): J39, C10, D15, B8, H30, H29.
_MEASURED = {
    "J39": ((1800, 825), "fit"), "C10": ((1800, 1170), "fit"), "D15": ((1500, 1125), "fit"),
    "B8": ((1800, 1551), "scroll"), "H30": ((1800, 5302), "scroll"), "H29": ((1950, 6363), "scroll"),
}


def _png(path, w, h):
    # minimal PNG signature + IHDR; image_view only reads the header
    import struct
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", w, h)
                     + b"\x08\x02\x00\x00\x00" + b"\x00\x00\x00\x00")
    return path


@pytest.mark.parametrize("name", _MEASURED)
def test_fit_or_scroll_by_aspect(tmp_path, name):
    (w, h), mode = _MEASURED[name]
    html = image_view.chart_html(_png(tmp_path / f"{name}.png", w, h))
    assert f"max-height:{image_view.MAX_VH}vh" in html          # never taller than the cap
    assert ("overflow:auto" in html) == (mode == "scroll")


def test_png_size_reads_header(tmp_path):
    assert image_view.png_size(_png(tmp_path / "a.png", 123, 456)) == (123, 456)
