"""Unit tests for family_a.calendar_scope (FA-5).

No QGIS, no network - pure stdlib + pytest.
Run: pytest tests/test_calendar_scope.py -v
"""

import csv
import io
import logging
import zipfile
from datetime import date
from pathlib import Path

from family_a.calendar_scope import (
    day_type_for_date,
    load_service_day_types,
    resolve_agency_timezone,
    time_bucket_for_seconds,
)

_AGENCY_FIELDS = ["agency_id", "agency_name", "agency_url", "agency_timezone"]
_CALENDAR_FIELDS = [
    "service_id", "monday", "tuesday", "wednesday", "thursday", "friday",
    "saturday", "sunday", "start_date", "end_date",
]
_CALENDAR_DATES_FIELDS = ["service_id", "date", "exception_type"]


def _make_calendar_zip(
    tmp_path: Path,
    agency_rows: list[dict] | None = None,
    calendar_rows: list[dict] | None = None,
    calendar_dates_rows: list[dict] | None = None,
    name: str = "static.zip",
) -> str:
    """Write only the files whose rows are not None - lets tests exercise a
    file being entirely absent (None) vs. present but empty ([]) as distinct
    cases."""
    zip_path = tmp_path / name
    with zipfile.ZipFile(zip_path, "w") as zf:
        if agency_rows is not None:
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=_AGENCY_FIELDS)
            w.writeheader()
            w.writerows(agency_rows)
            zf.writestr("agency.txt", buf.getvalue())

        if calendar_rows is not None:
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=_CALENDAR_FIELDS)
            w.writeheader()
            w.writerows(calendar_rows)
            zf.writestr("calendar.txt", buf.getvalue())

        if calendar_dates_rows is not None:
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=_CALENDAR_DATES_FIELDS)
            w.writeheader()
            w.writerows(calendar_dates_rows)
            zf.writestr("calendar_dates.txt", buf.getvalue())

    return str(zip_path)


# ---------------------------------------------------------------------------
# day_type_for_date
# ---------------------------------------------------------------------------


def test_day_type_for_date_all_seven_weekdays():
    # 2026-01-05 is a Monday.
    assert day_type_for_date(date(2026, 1, 5)) == "WEEKDAY"    # Monday
    assert day_type_for_date(date(2026, 1, 6)) == "WEEKDAY"    # Tuesday
    assert day_type_for_date(date(2026, 1, 7)) == "WEEKDAY"    # Wednesday
    assert day_type_for_date(date(2026, 1, 8)) == "WEEKDAY"    # Thursday
    assert day_type_for_date(date(2026, 1, 9)) == "WEEKDAY"    # Friday
    assert day_type_for_date(date(2026, 1, 10)) == "SATURDAY"
    assert day_type_for_date(date(2026, 1, 11)) == "SUNDAY"


# ---------------------------------------------------------------------------
# time_bucket_for_seconds
# ---------------------------------------------------------------------------


def test_time_bucket_for_seconds_boundaries():
    assert time_bucket_for_seconds(0, 120) == 0
    assert time_bucket_for_seconds(7199, 120) == 0
    assert time_bucket_for_seconds(7200, 120) == 1


def test_time_bucket_for_seconds_wraps_over_24h():
    # GTFS's >24:00:00 overnight convention still belongs to the previous
    # service day - modulo 86400 must be applied before bucketing.
    assert time_bucket_for_seconds(86400 + 3600, 120) == time_bucket_for_seconds(3600, 120)


# ---------------------------------------------------------------------------
# resolve_agency_timezone
# ---------------------------------------------------------------------------


def test_resolve_agency_timezone_reads_agency_txt(tmp_path):
    path = _make_calendar_zip(
        tmp_path,
        agency_rows=[{
            "agency_id": "1", "agency_name": "Test", "agency_url": "http://example.com",
            "agency_timezone": "Europe/Warsaw",
        }],
    )
    assert resolve_agency_timezone(path) == "Europe/Warsaw"


def test_resolve_agency_timezone_missing_file_falls_back(tmp_path, caplog):
    path = _make_calendar_zip(tmp_path)  # no agency_rows -> agency.txt absent entirely
    with caplog.at_level(logging.WARNING):
        result = resolve_agency_timezone(path)
    assert result == "Europe/Warsaw"
    assert any("agency.txt" in record.message for record in caplog.records)


def test_resolve_agency_timezone_missing_column_falls_back(tmp_path, caplog):
    zip_path = tmp_path / "static.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("agency.txt", "agency_id,agency_name\n1,TestAgency\n")
    with caplog.at_level(logging.WARNING):
        result = resolve_agency_timezone(str(zip_path))
    assert result == "Europe/Warsaw"
    assert len(caplog.records) >= 1


# ---------------------------------------------------------------------------
# load_service_day_types
# ---------------------------------------------------------------------------


def test_load_service_day_types_weekly_pattern(tmp_path):
    calendar_rows = [{
        "service_id": "svc1", "monday": "1", "tuesday": "1", "wednesday": "1",
        "thursday": "1", "friday": "1", "saturday": "0", "sunday": "0",
        "start_date": "20260105", "end_date": "20260111",  # one full week
    }]
    path = _make_calendar_zip(tmp_path, calendar_rows=calendar_rows, calendar_dates_rows=[])
    result = load_service_day_types(path)
    assert result["svc1"] == {"WEEKDAY"}


def test_load_service_day_types_all_zero_calendar_relies_on_calendar_dates(tmp_path):
    # Real Lodz shape: calendar.txt has all-zero weekday flags over a wide
    # range; calendar_dates.txt does 100% of the activation work.
    calendar_rows = [{
        "service_id": "svc1", "monday": "0", "tuesday": "0", "wednesday": "0",
        "thursday": "0", "friday": "0", "saturday": "0", "sunday": "0",
        "start_date": "20260702", "end_date": "20261231",
    }]
    calendar_dates_rows = [{"service_id": "svc1", "date": "20260110", "exception_type": "1"}]
    path = _make_calendar_zip(
        tmp_path, calendar_rows=calendar_rows, calendar_dates_rows=calendar_dates_rows
    )
    result = load_service_day_types(path)
    # 2026-01-10 is a Saturday.
    assert result["svc1"] == {"SATURDAY"}


def test_load_service_day_types_exception_removes_only_date(tmp_path):
    calendar_rows = [{
        "service_id": "svc1", "monday": "1", "tuesday": "0", "wednesday": "0",
        "thursday": "0", "friday": "0", "saturday": "0", "sunday": "0",
        "start_date": "20260105", "end_date": "20260105",  # exactly one Monday
    }]
    calendar_dates_rows = [{"service_id": "svc1", "date": "20260105", "exception_type": "2"}]
    path = _make_calendar_zip(
        tmp_path, calendar_rows=calendar_rows, calendar_dates_rows=calendar_dates_rows
    )
    result = load_service_day_types(path)
    # Proves the removal actually took effect - the service has zero active
    # dates left, not just "one of several dates remains".
    assert result.get("svc1", set()) == set()


def test_load_service_day_types_service_id_with_no_active_dates_is_empty(tmp_path):
    path = _make_calendar_zip(tmp_path, calendar_rows=[], calendar_dates_rows=[])
    result = load_service_day_types(path)
    assert result.get("svc_unknown", set()) == set()


def test_load_service_day_types_missing_calendar_txt_file(tmp_path):
    calendar_dates_rows = [{"service_id": "svc1", "date": "20260105", "exception_type": "1"}]
    path = _make_calendar_zip(
        tmp_path, calendar_rows=None, calendar_dates_rows=calendar_dates_rows
    )
    result = load_service_day_types(path)
    assert result["svc1"] == {"WEEKDAY"}  # 2026-01-05 is a Monday


def test_load_service_day_types_missing_calendar_dates_txt_file(tmp_path):
    calendar_rows = [{
        "service_id": "svc1", "monday": "1", "tuesday": "0", "wednesday": "0",
        "thursday": "0", "friday": "0", "saturday": "0", "sunday": "0",
        "start_date": "20260105", "end_date": "20260105",
    }]
    path = _make_calendar_zip(tmp_path, calendar_rows=calendar_rows, calendar_dates_rows=None)
    result = load_service_day_types(path)
    assert result["svc1"] == {"WEEKDAY"}


def test_load_service_day_types_malformed_date_is_skipped_not_a_crash(tmp_path, caplog):
    # A single malformed date in an unvalidated real-world feed must be
    # skipped (treated like an absent date), not crash the whole build.
    calendar_rows = [{
        "service_id": "svc1", "monday": "1", "tuesday": "0", "wednesday": "0",
        "thursday": "0", "friday": "0", "saturday": "0", "sunday": "0",
        "start_date": "not-a-date", "end_date": "20260105",
    }]
    calendar_dates_rows = [
        {"service_id": "svc2", "date": "2026-01-05", "exception_type": "1"},  # wrong format
        {"service_id": "svc2", "date": "20260106", "exception_type": "1"},  # valid, Tuesday
    ]
    path = _make_calendar_zip(
        tmp_path, calendar_rows=calendar_rows, calendar_dates_rows=calendar_dates_rows
    )
    with caplog.at_level(logging.WARNING):
        result = load_service_day_types(path)

    assert result.get("svc1", set()) == set()  # malformed start_date -> row contributes nothing
    assert result["svc2"] == {"WEEKDAY"}  # the one valid date still resolves correctly
    assert any("Could not parse GTFS date" in record.message for record in caplog.records)
