"""Time-window helpers: turn (start, end, interval) into the list of
HH:MM:SS timestamps fed to OTP's surface endpoint.

Replaces the manual time_6_22.csv used in the historical R pipeline
(see reference/Surface_analysis_wro.R).

stdlib only — no PyQt / QGIS imports — so this module is trivially
unit-testable outside the QGIS interpreter.
"""

from __future__ import annotations

from datetime import datetime, timedelta

# Map QgsProcessingParameterEnum index (see INTERVAL_CHOICES in
# RunTemporalAccessibility) to the corresponding interval in minutes.
INTERVAL_MINUTES: dict[int, int] = {0: 1, 1: 15, 2: 60}

_ALLOWED_INTERVALS = frozenset(INTERVAL_MINUTES.values())


def build_time_list(
    start_h: int,
    start_m: int,
    end_h: int,
    end_m: int,
    interval_minutes: int,
) -> list[str]:
    """Build a deterministic list of HH:MM:SS timestamps from a time window.

    The end is inclusive when it lands exactly on the interval grid
    (the usual case: 06:00 -> 22:00 with interval 1/15/60 min gives
    961 / 65 / 17 entries respectively, as required by PR section 8.2).

    The returned format matches what ``OtpClient.create_surface``
    sends as the ``time`` query parameter.

    Raises ValueError when arguments are invalid; the caller is
    responsible for translating that into a user-facing message.
    """
    if interval_minutes not in _ALLOWED_INTERVALS:
        raise ValueError(
            f"interval_minutes must be one of {sorted(_ALLOWED_INTERVALS)}, "
            f"got {interval_minutes}"
        )
    if not (0 <= start_h < 24 and 0 <= end_h < 24):
        raise ValueError(
            f"Hours must be in 0..23 (got start_h={start_h}, end_h={end_h})."
        )
    if not (0 <= start_m < 60 and 0 <= end_m < 60):
        raise ValueError(
            f"Minutes must be in 0..59 (got start_m={start_m}, end_m={end_m})."
        )

    anchor = datetime(2000, 1, 1)
    start = anchor.replace(hour=start_h, minute=start_m)
    end = anchor.replace(hour=end_h, minute=end_m)
    if end < start:
        raise ValueError(
            f"Window end ({end_h:02d}:{end_m:02d}) is before start "
            f"({start_h:02d}:{start_m:02d})."
        )

    step = timedelta(minutes=interval_minutes)
    out: list[str] = []
    current = start
    while current <= end:
        out.append(current.strftime("%H:%M:%S"))
        current += step
    return out


def time_to_filename_slug(t: str) -> str:
    """``"06:30:00"`` -> ``"06-30-00"`` so the timestamp can sit in a filename."""
    return t.replace(":", "-")
