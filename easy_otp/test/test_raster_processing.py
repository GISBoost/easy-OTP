"""Tests for easy_otp.core.raster_processing.count_below_threshold.

Run inside the QGIS Python interpreter:
    python -m pytest easy_otp/test/test_raster_processing.py -v

Surfaces are written to a temporary directory as real GeoTIFFs so that
GDAL can read back NoData metadata exactly as it would during a real run.
Using the MEM driver would lose NoData on re-open, which is precisely the
behaviour we need to test against.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock

import numpy as np
import pytest

gdal = pytest.importorskip("osgeo.gdal", reason="Must run inside QGIS Python interpreter")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_surface(
    directory: Path,
    name: str,
    data: np.ndarray,
    nodata: Optional[float],
    gdal_dtype=None,
) -> Path:
    """Write a single-band GeoTIFF with given data and NoData value."""
    if gdal_dtype is None:
        gdal_dtype = gdal.GDT_Byte

    path = directory / name
    driver = gdal.GetDriverByName("GTiff")
    rows, cols = data.shape
    ds = driver.Create(str(path), cols, rows, 1, gdal_dtype)
    ds.SetGeoTransform((0.0, 1.0, 0.0, 0.0, 0.0, -1.0))
    ds.SetProjection("")
    band = ds.GetRasterBand(1)
    if nodata is not None:
        band.SetNoDataValue(nodata)
    band.WriteArray(data)
    band.FlushCache()
    ds = None
    return path


def _mock_feedback(cancel_after: int = 9999) -> MagicMock:
    """Feedback that cancels after ``cancel_after`` isCanceled() calls."""
    fb = MagicMock()
    call_count = {"n": 0}

    def _is_canceled():
        call_count["n"] += 1
        return call_count["n"] > cancel_after

    fb.isCanceled.side_effect = _is_canceled
    return fb


def _count(surfaces, threshold, tmpdir) -> np.ndarray:
    from easy_otp.core.raster_processing import count_below_threshold

    out = Path(tmpdir) / "count.tif"
    count_below_threshold(surfaces, threshold, out, _mock_feedback())
    ds = gdal.Open(str(out))
    assert ds is not None, f"GDAL could not open output raster: {out}"
    arr = ds.GetRasterBand(1).ReadAsArray()
    ds = None
    return arr


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

def test_basic_count():
    """3 surfaces, no NoData — pixels counted correctly."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        # 1×3 raster, one row, three cols:
        #   col 0: s1=10, s2=10, s3=10 — all ≤ 30 → count=3
        #   col 1: s1=20, s2=20, s3=35 — two ≤ 30 → count=2
        #   col 2: s1=35, s2=35, s3=35 — none ≤ 30 → count=0
        s1 = _make_surface(td, "s1.tif", np.array([[10, 20, 35]], dtype=np.uint8), nodata=None)
        s2 = _make_surface(td, "s2.tif", np.array([[10, 20, 35]], dtype=np.uint8), nodata=None)
        s3 = _make_surface(td, "s3.tif", np.array([[10, 35, 35]], dtype=np.uint8), nodata=None)

        result = _count([s1, s2, s3], 30, td)

        assert result[0, 0] == 3
        assert result[0, 1] == 2
        assert result[0, 2] == 0


def test_nodata_zero_excluded():
    """NoData=0 pixels must NOT be counted (the P0 bug fix)."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        # 1×3: col 0 → NoData(0), col 1 → 20 (≤30), col 2 → 35 (>30)
        data = np.array([[0, 20, 35]], dtype=np.uint8)
        s1 = _make_surface(td, "s1.tif", data, nodata=0.0)
        s2 = _make_surface(td, "s2.tif", data, nodata=0.0)

        result = _count([s1, s2], 30, td)

        assert result[0, 0] == 0, "NoData=0 pixel must not be counted"
        assert result[0, 1] == 2, "Valid pixel ≤ threshold must be counted twice"
        assert result[0, 2] == 0, "Pixel > threshold must not be counted"


def test_nodata_negative_excluded():
    """NoData=-9999 pixels must NOT be counted."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        data = np.array([[-9999, 25, 50]], dtype=np.int32)
        s1 = _make_surface(td, "s1.tif", data, nodata=-9999.0, gdal_dtype=gdal.GDT_Int32)

        result = _count([s1], 30, td)

        assert result[0, 0] == 0, "NoData=-9999 pixel must not be counted"
        assert result[0, 1] == 1
        assert result[0, 2] == 0


def test_threshold_boundary():
    """Values at exactly the threshold are included; one above is not."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        data = np.array([[29, 30, 31]], dtype=np.uint8)
        s1 = _make_surface(td, "s1.tif", data, nodata=None)

        result = _count([s1], 30, td)

        assert result[0, 0] == 1, "29 ≤ 30"
        assert result[0, 1] == 1, "30 ≤ 30"
        assert result[0, 2] == 0, "31 > 30"


def test_output_zero_is_nodata():
    """Pixels with zero count are written as 0; the output NoData value is 0."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        from easy_otp.core.raster_processing import count_below_threshold

        data = np.array([[50, 60]], dtype=np.uint8)  # both > threshold
        s1 = _make_surface(td, "s1.tif", data, nodata=None)

        out = td / "count.tif"
        count_below_threshold([s1], 30, out, _mock_feedback())

        ds = gdal.Open(str(out))
        band = ds.GetRasterBand(1)
        arr = band.ReadAsArray()
        nodata_val = band.GetNoDataValue()
        ds = None

        assert np.all(arr == 0)
        assert nodata_val == 0.0, "Output NoData must be 0 (folds GRASS r.null step)"


def test_empty_surfaces_raises():
    """Passing an empty list raises RuntimeError immediately."""
    with tempfile.TemporaryDirectory() as td:
        from easy_otp.core.raster_processing import count_below_threshold

        out = Path(td) / "count.tif"
        with pytest.raises(RuntimeError):
            count_below_threshold([], 30, out, _mock_feedback())


def test_count_below_threshold_bands_as_service_points():
    """count_below_threshold works identically when bands represent service points (N-3).

    3 surfaces = 3 service points at a fixed time.
    Pixel A: all 3 within threshold (value 5 ≤ 10) → count=3.
    Pixel B: 1 within threshold (value 9 ≤ 10), 2 above → count=1.
    Pixel C: all above threshold (value 120, OTP sentinel) → count=0 → NoData.
    Pixel D: outside graph (value 128, unreachable sentinel) → count=0 → NoData.
    """
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        # 1×4 raster (one row, four cols: A, B, C, D)
        s1 = _make_surface(td, "pt1.tif", np.array([[5,  9, 120, 128]], dtype=np.uint8), nodata=None)
        s2 = _make_surface(td, "pt2.tif", np.array([[5, 15, 120, 128]], dtype=np.uint8), nodata=None)
        s3 = _make_surface(td, "pt3.tif", np.array([[5, 15, 120, 128]], dtype=np.uint8), nodata=None)

        result = _count([s1, s2, s3], 10, td)

        assert result[0, 0] == 3, "pixel A: all 3 points within threshold"
        assert result[0, 1] == 1, "pixel B: only 1 point within threshold"
        assert result[0, 2] == 0, "pixel C: OTP 120-min sentinel — not within threshold"
        assert result[0, 3] == 0, "pixel D: OTP 128 unreachable sentinel — not within threshold"
