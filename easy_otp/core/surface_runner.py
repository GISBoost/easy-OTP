"""Sequential loop that generates one travel-time surface per timestamp.

Python replacement for the R loop in reference/Surface_analysis_wro.R
(``for (i in 1:total) otp_create_surface(...)``). Stays sequential
(no concurrency) — out of scope for v1.

Cancellation: the loop checks ``feedback.isCanceled()`` at the top of
every iteration AND between create/download for the current timestamp,
so a Cancel click stops within at most one surface. Partial GeoTIFFs
left by a failed download are removed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from qgis.core import QgsProcessingException
from qgis.PyQt.QtCore import QCoreApplication

from .otp_client import OtpClient, OtpClientError
from .time_utils import time_to_filename_slug


def _tr(string: str) -> str:
    return QCoreApplication.translate("Processing", string)


@dataclass(frozen=True)
class SurfaceJobParams:
    """Routing parameters reused for every surface in the time window."""
    from_place_lat_lon: tuple[float, float]
    date_mmddyyyy: str
    max_walk_distance: float
    walk_reluctance: float
    wait_reluctance: float
    transfer_penalty: int
    min_transfer_time: int
    walk_speed: float
    arrive_by: bool = False


def run_surface_loop(
    client: OtpClient,
    time_list: list[str],
    job: SurfaceJobParams,
    surfaces_dir: Path,
    feedback,
    download_timeout_s: float = 180.0,
) -> list[Path]:
    """For each ``HH:MM:SS`` in ``time_list`` create one GeoTIFF surface.

    Output files are named ``surface_HH-MM-SS.tiff`` (deterministic
    ordering = future raster band order in milestone 4). Returns the
    list of written paths in the same order as ``time_list``.
    """
    surfaces_dir.mkdir(parents=True, exist_ok=True)
    total = len(time_list)
    if total == 0:
        raise QgsProcessingException(
            _tr("Time list is empty — nothing to generate.")
        )

    written: list[Path] = []
    for i, t in enumerate(time_list, start=1):
        if feedback.isCanceled():
            raise QgsProcessingException(_tr("Run cancelled by user."))

        feedback.setProgress(int((i - 1) / total * 100))
        feedback.pushInfo(_tr(f"Surface {i}/{total} at {t}"))

        out_path = surfaces_dir / f"surface_{time_to_filename_slug(t)}.tiff"

        try:
            surface_id = client.create_surface(
                from_place_lat_lon=job.from_place_lat_lon,
                date_mmddyyyy=job.date_mmddyyyy,
                time_hhmmss=t,
                max_walk_distance=job.max_walk_distance,
                walk_reluctance=job.walk_reluctance,
                wait_reluctance=job.wait_reluctance,
                transfer_penalty=job.transfer_penalty,
                min_transfer_time=job.min_transfer_time,
                walk_speed=job.walk_speed,
                arrive_by=job.arrive_by,
                log_fn=None,
            )
            if feedback.isCanceled():
                raise QgsProcessingException(_tr("Run cancelled by user."))
            client.download_surface_raster(
                surface_id,
                out_path,
                timeout_s=download_timeout_s,
                log_fn=None,
            )
        except OtpClientError as e:
            out_path.unlink(missing_ok=True)
            raise QgsProcessingException(
                _tr(f"OTP surface generation failed at {t}: {e}")
            ) from e
        except BaseException:
            out_path.unlink(missing_ok=True)
            raise

        written.append(out_path)

    feedback.setProgress(100)
    return written
