"""Time-window helpers: turn (start, end, interval) into the list of
HH:MM:SS timestamps fed to OTP's surface endpoint.

Replaces the manual time_6_22.csv used in the historical R pipeline
(see reference/Surface_analysis_wro.R).

stdlib only — no PyQt / QGIS imports — so this module is trivially
unit-testable outside the QGIS interpreter.
"""

from __future__ import annotations

from datetime import datetime, timedelta


def build_time_list(
    start_h: int,
    start_m: int,
    end_h: int,
    end_m: int,
    interval_minutes: int,
) -> list[str]:
    """Build a deterministic list of HH:MM:SS timestamps from a time window.

    The end is inclusive when it lands exactly on the interval grid
    (e.g. 06:00–22:00 at 1 min → 961 entries, at 5 min → 193 entries).
    Any positive integer interval is accepted.

    The returned format matches what ``OtpClient.create_surface``
    sends as the ``time`` query parameter.

    Raises ValueError when arguments are invalid; the caller is
    responsible for translating that into a user-facing message.
    """
    if interval_minutes < 1:
        raise ValueError(
            f"interval_minutes must be >= 1, got {interval_minutes}"
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


def forward_window(
    now: datetime, horizon_min: int
) -> tuple[int, int, int, int, bool]:
    """Anchor a realtime window at ``now`` and extend ``horizon_min`` minutes ahead.

    Returns ``(start_h, start_m, end_h, end_m, truncated)`` where the start is
    ``now`` truncated to the minute and the end is ``now + horizon_min``. Live
    GTFS-RT only carries predictions near the present, so RunRealtimeAccessibility
    measures forward from the current moment rather than over a fixed historical
    window.

    RT-1 does not span calendar days: if the end crosses midnight it is clamped to
    ``23:59`` of the start day and ``truncated`` is True so the caller can warn.

    Raises ValueError when ``horizon_min`` is not positive.
    """
    if horizon_min <= 0:
        raise ValueError(f"horizon_min must be positive, got {horizon_min}")

    start = now.replace(second=0, microsecond=0)
    end = start + timedelta(minutes=horizon_min)
    truncated = False
    if end.date() != start.date():
        end = start.replace(hour=23, minute=59)
        truncated = True
    return (start.hour, start.minute, end.hour, end.minute, truncated)


def time_to_filename_slug(t: str) -> str:
    """``"06:30:00"`` -> ``"06-30-00"`` so the timestamp can sit in a filename."""
    return t.replace(":", "-")
