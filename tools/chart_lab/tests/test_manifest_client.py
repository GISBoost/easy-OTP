"""Unit tests for chart_lab.manifest_client (CL-5: gtfs-dashboard's online catalogue).

    cd tools\\chart_lab
    .venv\\Scripts\\python.exe -m pytest tests -q

Most of these run against a small hand-written fixture manifest, not the network - fast and
deterministic. `test_fetch_manifest_against_the_real_gtfs_dashboard_pages_site` does hit the
real network and is skipped (not failed) if it can't reach it, since CI/offline environments
must not fail a build over a third party being briefly down.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from chart_lab import paths  # noqa: F401 - side effect: wires sys.path
from chart_lab import manifest_client
from chart_lab.manifest_client import CityDay, ManifestError, fetch_manifest, list_available_city_days

_FIXTURE_MANIFEST = {
    "generated_at": "2026-08-19T00:00:00Z",
    "cities": {
        "lodz": {
            "display_name": "Łódź",
            "days": [
                {"date": "2026-08-05", "assets": {"tidy_table": "https://example/lodz_tidy_2026-08-05.csv.gz"}},
                # Recorded before easy-GTFS-RT started publishing this asset (2026-08-03) -
                # tidy_table is null and this day must never be offered as pickable.
                {"date": "2026-07-20", "assets": {"tidy_table": None}},
            ],
        },
        "warsaw": {
            "display_name": "Warsaw",
            "days": [
                {"date": "2026-08-06", "assets": {"tidy_table": "https://example/warsaw_tidy_2026-08-06.csv.gz"}},
                # Malformed: no "assets" key at all - must be skipped, not raised on.
                {"date": "2026-08-07"},
            ],
        },
    },
}


def test_list_available_city_days_filters_null_and_malformed_entries():
    days = list_available_city_days(_FIXTURE_MANIFEST)
    assert len(days) == 2
    assert all(d.tidy_table_url for d in days)
    dates = {d.date for d in days}
    assert dates == {"2026-08-05", "2026-08-06"}
    assert "2026-07-20" not in dates  # null tidy_table
    assert "2026-08-07" not in dates  # missing assets


def test_city_day_label_combines_display_name_and_date():
    day = CityDay(city_id="lodz", display_name="Łódź", date="2026-08-05",
                  tidy_table_url="https://example/x.csv.gz")
    assert day.label == "Łódź — 2026-08-05"


def test_list_available_city_days_handles_empty_manifest():
    assert list_available_city_days({}) == []
    assert list_available_city_days({"cities": {}}) == []


def test_fetch_manifest_wraps_http_error():
    from urllib.error import HTTPError

    def _raise(*a, **kw):
        raise HTTPError("https://x", 404, "Not Found", {}, None)

    with patch("urllib.request.urlopen", side_effect=_raise):
        with pytest.raises(ManifestError, match="404"):
            fetch_manifest("https://x")


def test_fetch_manifest_wraps_connection_failure():
    from urllib.error import URLError

    def _raise(*a, **kw):
        raise URLError("nodename nor servname provided")

    with patch("urllib.request.urlopen", side_effect=_raise):
        with pytest.raises(ManifestError, match="check your internet connection"):
            fetch_manifest("https://x")


def test_fetch_manifest_wraps_invalid_json():
    import io

    class _FakeResponse:
        status = 200

        def read(self):
            return b"not json"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    with patch("urllib.request.urlopen", return_value=_FakeResponse()):
        with pytest.raises(ManifestError, match="valid JSON"):
            fetch_manifest("https://x")


def test_download_tidy_table_rejects_empty_response(tmp_path):
    class _FakeResponse:
        status = 200

        def read(self):
            return b""

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    with patch("urllib.request.urlopen", return_value=_FakeResponse()):
        with pytest.raises(ManifestError, match="did not complete"):
            manifest_client.download_tidy_table("https://example/city_tidy_2026-08-05.csv.gz", tmp_path)


def test_download_tidy_table_reuses_cached_file(tmp_path):
    cached = tmp_path / "city_tidy_2026-08-05.csv.gz"
    cached.write_bytes(b"already here")
    with patch("urllib.request.urlopen") as mock_urlopen:
        result = manifest_client.download_tidy_table(
            "https://example/city_tidy_2026-08-05.csv.gz", tmp_path,
        )
    mock_urlopen.assert_not_called()
    assert result == cached


def test_fetch_manifest_against_the_real_gtfs_dashboard_pages_site():
    try:
        manifest = fetch_manifest()
    except ManifestError as exc:
        pytest.skip(f"no network access in this environment: {exc}")
    days = list_available_city_days(manifest)
    assert len(days) > 0
    assert all(d.tidy_table_url.startswith("https://github.com/") for d in days)
