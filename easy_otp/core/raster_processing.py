"""Raster stacking, counting, and zero null-out (milestone 4).

Ports the manual pipeline steps 7-9 (PR section 8.2 / section 3) into
PyQGIS:

- step 7 (``gdal:buildvirtualraster``) → :func:`build_surface_vrt`
- step 8 (channel-wise count below threshold, reference logic in
  ``reference/skrypt_wro.py``) → :func:`count_below_threshold`
- step 9 (GRASS ``r.null`` zeroing) → fold into ``count_below_threshold``
  by writing the count raster with ``NoData = 0`` (no GRASS dependency).

OTP surfaces carry travel-time in **minutes** with a hard-coded ceiling
of 120 (cells unreachable or ≥120 min all read 120). The threshold
comparison stays in minutes, exactly like ``skrypt_wro.py``.
"""

from __future__ import annotations

from pathlib import Path

try:
    from osgeo import gdal
except ImportError as e:
    raise ImportError(
        "osgeo/GDAL is not available. easy-OTP must run inside the "
        "QGIS Python interpreter, which ships GDAL bindings."
    ) from e

import numpy as np
from qgis.PyQt.QtCore import QCoreApplication


def _tr(s: str) -> str:
    return QCoreApplication.translate("Processing", s)


def build_surface_vrt(surfaces: list[Path], vrt_path: Path) -> Path:
    """Assemble per-minute GeoTIFFs into a multi-band VRT (one band per surface).

    Equivalent to ``gdal:buildvirtualraster`` with the "separate" option
    in the manual pipeline (PR step 7). Raises ``RuntimeError`` if the
    surface grids disagree (different extent / CRS / pixel size) — GDAL
    rejects the build in that case.
    """
    if not surfaces:
        raise RuntimeError(_tr("No surface rasters to stack."))

    vrt_path.parent.mkdir(parents=True, exist_ok=True)
    src_list = [str(p) for p in surfaces]

    opts = gdal.BuildVRTOptions(separate=True)
    ds = gdal.BuildVRT(str(vrt_path), src_list, options=opts)
    if ds is None:
        raise RuntimeError(_tr(
            f"Failed to build VRT at {vrt_path}. Surfaces may have "
            f"mismatched extent/CRS/pixel size. First surface: "
            f"{surfaces[0].name}"
        ))
    band_count = ds.RasterCount
    ds.FlushCache()
    ds = None  # close

    if band_count != len(surfaces):
        raise RuntimeError(_tr(
            f"VRT band count ({band_count}) does not match surface "
            f"count ({len(surfaces)})."
        ))
    return vrt_path


def count_below_threshold(
    vrt_path: Path,
    threshold_min: int,
    out_count_tif: Path,
    feedback,
) -> Path:
    """For each pixel, count bands where value ≤ ``threshold_min``.

    Direct port of ``reference/skrypt_wro.py``: per-band NoData replaced
    with 0, accumulator ``+= (data <= threshold).astype(int32)``. Writes
    a single-band Int32 GeoTIFF with NoData = 0 — folds in the manual
    GRASS ``r.null`` step (PR step 8) so zero-count pixels become NoData
    in the output and are skipped by downstream zonal stats.

    Reports progress per band and honours ``feedback.isCanceled()``.
    """
    src = None
    out_ds = None
    try:
        src = gdal.Open(str(vrt_path), gdal.GA_ReadOnly)
        if src is None:
            raise RuntimeError(_tr(f"Cannot open stack VRT: {vrt_path}"))

        cols = src.RasterXSize
        rows = src.RasterYSize
        total_bands = src.RasterCount
        if total_bands == 0:
            raise RuntimeError(_tr("Stack VRT contains zero bands."))

        accumulator = np.zeros((rows, cols), dtype=np.int32)

        for i in range(1, total_bands + 1):
            if feedback.isCanceled():
                raise RuntimeError(_tr("Run cancelled by user."))
            feedback.setProgress(int((i - 1) / total_bands * 100))

            band = src.GetRasterBand(i)
            data = band.ReadAsArray()
            nodata = band.GetNoDataValue()
            if nodata is not None:
                data[data == nodata] = 0
            accumulator += (data <= threshold_min).astype(np.int32)

        out_count_tif.parent.mkdir(parents=True, exist_ok=True)
        driver = gdal.GetDriverByName("GTiff")
        out_ds = driver.Create(
            str(out_count_tif), cols, rows, 1, gdal.GDT_Int32
        )
        if out_ds is None:
            raise RuntimeError(_tr(
                f"Failed to create output count raster: {out_count_tif}"
            ))
        out_ds.SetGeoTransform(src.GetGeoTransform())
        out_ds.SetProjection(src.GetProjection())
        out_band = out_ds.GetRasterBand(1)
        out_band.SetNoDataValue(0)
        out_band.WriteArray(accumulator)
        out_band.FlushCache()

        feedback.setProgress(100)
        return out_count_tif
    except BaseException:
        out_count_tif.unlink(missing_ok=True)
        raise
    finally:
        if out_ds is not None:
            out_ds = None  # noqa: F841 — explicit GDAL close
        if src is not None:
            src = None  # noqa: F841 — explicit GDAL close
