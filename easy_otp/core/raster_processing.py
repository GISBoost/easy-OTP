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

    DEBUG ARTIFACT ONLY — do NOT use for counting. Reading through a VRT can
    produce different NoData semantics than reading each source file directly
    (see milestone-4 P0 bug). Use :func:`count_below_threshold` with a list of
    paths instead. The VRT is still generated as a cheap (~1 s) visual aid for
    inspecting the surface stack in QGIS.

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
        vrt_path.unlink(missing_ok=True)
        raise RuntimeError(_tr(
            f"VRT band count ({band_count}) does not match surface "
            f"count ({len(surfaces)})."
        ))
    return vrt_path


def count_below_threshold(
    surfaces: list[Path],
    threshold_min: int,
    out_count_tif: Path,
    feedback,
) -> Path:
    """For each pixel, count surfaces where value ≤ ``threshold_min``.

    Reads each surface GeoTIFF directly (not via VRT) to avoid any VRT
    NoData-propagation edge cases. NoData pixels are excluded from the count
    via a proper mask — the critical fix over the earlier VRT-based approach
    where NoData=0 would satisfy ``0 <= threshold`` and inflate every pixel.

    Writes a single-band Int32 GeoTIFF with NoData = 0, folding in the manual
    GRASS ``r.null`` step (PR step 8) so zero-count pixels become NoData in the
    output and are skipped by downstream zonal stats.

    Geotransform and projection are taken from the first surface; all surfaces
    from one OTP serve share the same grid.

    Reports progress per surface and honours ``feedback.isCanceled()``.
    """
    if not surfaces:
        raise RuntimeError(_tr("No surfaces provided for counting."))

    out_ds = None
    geo_transform = None
    projection = None
    cols = rows = None
    accumulator = None

    try:
        total = len(surfaces)
        for idx, surf_path in enumerate(surfaces):
            if feedback.isCanceled():
                raise RuntimeError(_tr("Run cancelled by user."))
            feedback.setProgress(int(idx / total * 100))

            ds = gdal.Open(str(surf_path), gdal.GA_ReadOnly)
            if ds is None:
                raise RuntimeError(_tr(f"Cannot open surface raster: {surf_path}"))

            try:
                band = ds.GetRasterBand(1)
                data = band.ReadAsArray()
                nodata_val = band.GetNoDataValue()

                if idx == 0:
                    rows, cols = data.shape
                    geo_transform = ds.GetGeoTransform()
                    projection = ds.GetProjection()
                    accumulator = np.zeros((rows, cols), dtype=np.int32)
                    feedback.pushInfo(
                        f"Surface dtype={gdal.GetDataTypeName(band.DataType)} "
                        f"NoData={nodata_val} (n_surfaces={total})"
                    )

                if nodata_val is not None:
                    # Cast nodata to the array's own dtype to avoid float/int
                    # comparison surprises (e.g. nodata=0.0 on uint8 array).
                    nodata_cmp = np.array(nodata_val, dtype=data.dtype)
                    valid = (data != nodata_cmp) & (data <= threshold_min)
                else:
                    valid = data <= threshold_min
                accumulator += valid.astype(np.int32)
            finally:
                ds = None  # explicit GDAL close after each surface

        out_count_tif.parent.mkdir(parents=True, exist_ok=True)
        driver = gdal.GetDriverByName("GTiff")
        out_ds = driver.Create(
            str(out_count_tif), cols, rows, 1, gdal.GDT_Int32
        )
        if out_ds is None:
            raise RuntimeError(_tr(
                f"Failed to create output count raster: {out_count_tif}"
            ))
        out_ds.SetGeoTransform(geo_transform)
        out_ds.SetProjection(projection)
        out_band = out_ds.GetRasterBand(1)
        out_band.SetNoDataValue(0)
        out_band.WriteArray(accumulator)
        out_band.FlushCache()

        feedback.setProgress(100)
        return out_count_tif
    except BaseException:
        if out_ds is not None:
            out_ds = None  # close before unlink — Windows holds file lock on open datasets
        out_count_tif.unlink(missing_ok=True)
        raise
    finally:
        if out_ds is not None:
            out_ds = None  # noqa: F841 — explicit GDAL close
