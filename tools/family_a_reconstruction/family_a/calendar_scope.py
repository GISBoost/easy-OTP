"""Resolve agency timezone and day-type/time-bucket scoping for segment keys (FA-5).

Fixes a Family A finding on real Łódź data: a single afternoon recording, keyed only by
(route_id, direction_id, from_stop_id, to_stop_id), corrected 74% of a 6-month static feed's
stop_times.txt rows - including trips departing at 3:48 AM that could never have been observed
in the recording window. This module adds the day-type (WEEKDAY/SATURDAY/SUNDAY) and
time-of-day-bucket dimensions build_gtfs.py/segment_stats.py use to scope corrections to
static trips actually comparable to what was observed.

Standalone tool code: never imports easy_otp/, never imports osgeo/QGIS, and is never imported
by the plugin.

No QGIS / GDAL imports. Run tests: pytest tests/test_calendar_scope.py -v
"""

from __future__ import annotations

import csv
import io
import logging
import zipfile
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)

_FALLBACK_TIMEZONE = "Europe/Warsaw"
_WEEKDAY_COLUMNS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def resolve_agency_timezone(gtfs_zip_path: str) -> str:
    """Read agency.txt's first data row's agency_timezone column.

    Falls back to "Europe/Warsaw" with a logged warning if agency.txt is missing, empty, or
    lacks the column - every Family A target city so far (Warszawa, Wrocław, Łódź) is in this
    zone. Returns an IANA zone name suitable for zoneinfo.ZoneInfo(...).
    """
    with zipfile.ZipFile(gtfs_zip_path) as zf:
        if "agency.txt" not in zf.namelist():
            logger.warning(
                "agency.txt not found in static GTFS %s; falling back to %s",
                gtfs_zip_path,
                _FALLBACK_TIMEZONE,
            )
            return _FALLBACK_TIMEZONE

        with zf.open("agency.txt") as fh:
            reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig"))
            row = next(reader, None)

    if row is None or not row.get("agency_timezone"):
        logger.warning(
            "agency.txt in static GTFS %s has no agency_timezone column/row; falling back to %s",
            gtfs_zip_path,
            _FALLBACK_TIMEZONE,
        )
        return _FALLBACK_TIMEZONE

    return row["agency_timezone"]


def day_type_for_date(d: date) -> str:
    """Monday-Friday -> "WEEKDAY", Saturday -> "SATURDAY", Sunday -> "SUNDAY".

    Day-of-week only - no public holiday awareness. Documented MVP simplification (PRD open
    question 9, deliberately deferred 2026-07-10) - Polish holidays run Sunday-style service
    but this function has no calendar-of-holidays input.
    """
    weekday = d.weekday()  # Monday=0 .. Sunday=6
    if weekday == 5:
        return "SATURDAY"
    if weekday == 6:
        return "SUNDAY"
    return "WEEKDAY"


def time_bucket_for_seconds(seconds_since_midnight: int, bucket_minutes: int = 120) -> int:
    """Bucket index for a time-of-day, default 2-hour (120 min) blocks, 12 buckets/day.

    seconds_since_midnight may exceed 86400 (GTFS's >24:00:00 overnight convention) -
    normalized via modulo 86400 before bucketing, consistent with GTFS's own semantics that
    such a time still belongs to the previous service day.
    """
    return (seconds_since_midnight % 86400) // (bucket_minutes * 60)


def load_service_day_types(gtfs_zip_path: str) -> dict[str, set[str]]:
    """service_id -> set of day_types present among its active service dates.

    Expands calendar.txt's weekly pattern x [start_date, end_date] day-by-day (if
    calendar.txt is present and the service_id's row has any weekday flags set), then applies
    calendar_dates.txt exceptions (exception_type 1 adds a date, 2 removes it) per service_id.
    calendar.txt is optional per the GTFS spec - a service_id with no calendar.txt row (or an
    all-zero weekly pattern, e.g. Lodz's real feed) relies entirely on calendar_dates.txt
    additions. Each resulting active date is mapped through day_type_for_date; the return
    value is the SET of distinct day_types seen (usually one, occasionally more for a
    "runs every day" service_id). A service_id with zero active dates anywhere (a static-feed
    data-quality issue) maps to an empty set - callers must treat that as "never matches any
    day_type", not as "matches everything".
    """
    active_dates: dict[str, set[date]] = {}

    with zipfile.ZipFile(gtfs_zip_path) as zf:
        names = set(zf.namelist())

        if "calendar.txt" in names:
            with zf.open("calendar.txt") as fh:
                reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig"))
                for row in reader:
                    service_id = row.get("service_id", "")
                    if not service_id:
                        continue
                    weekday_flags = [row.get(col, "0") == "1" for col in _WEEKDAY_COLUMNS]
                    if not any(weekday_flags):
                        # No weekday runs - this row contributes no dates; avoid the
                        # day-by-day expansion below entirely (Lodz's real calendar.txt
                        # rows span 6 months with all-zero flags).
                        continue
                    start = _parse_gtfs_date(row.get("start_date", ""))
                    end = _parse_gtfs_date(row.get("end_date", ""))
                    if start is None or end is None:
                        continue
                    dates = active_dates.setdefault(service_id, set())
                    current = start
                    while current <= end:
                        if weekday_flags[current.weekday()]:
                            dates.add(current)
                        current += timedelta(days=1)

        if "calendar_dates.txt" in names:
            with zf.open("calendar_dates.txt") as fh:
                reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig"))
                for row in reader:
                    service_id = row.get("service_id", "")
                    if not service_id:
                        continue
                    d = _parse_gtfs_date(row.get("date", ""))
                    if d is None:
                        continue
                    exception_type = row.get("exception_type", "")
                    dates = active_dates.setdefault(service_id, set())
                    if exception_type == "1":
                        dates.add(d)
                    elif exception_type == "2":
                        dates.discard(d)

    return {
        service_id: {day_type_for_date(d) for d in dates}
        for service_id, dates in active_dates.items()
        if dates
    }


def _parse_gtfs_date(s: str) -> date | None:
    s = s.strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y%m%d").date()
    except ValueError:
        # A malformed date in one row of an unvalidated real-world feed must not crash the
        # whole `build` run - treat it the same as an absent date (callers already skip
        # rows/dates that come back None).
        logger.warning("Could not parse GTFS date %r (expected YYYYMMDD); skipping it", s)
        return None
