"""Unit tests for GenerateIsochronesOverTime (pure stdlib, no QGIS, no network)."""

from __future__ import annotations

import csv
import io
import urllib.parse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from easy_otp.core.isochrone_client import IsochroneClient
from easy_otp.core.time_utils import build_time_list

FIXTURE_PATH = (
    Path(__file__).parent.parent.parent
    / "docs" / "gisboostgithub" / "OpenTripPlanner" / "isochrone.geojson"
)
_FIXTURE_GEOJSON = FIXTURE_PATH.read_text(encoding="utf-8")

# ── helpers ──────────────────────────────────────────────────────────────────

_BASE_CALL = dict(
    from_lat=51.0,
    from_lon=17.0,
    cutoffs_sec=[900, 1800],
    mode="TRANSIT",
    date_mmddyyyy="01-15-2024",
    direction="FROM",
    arrive_by=False,
    max_walk_distance=800,
    walk_reluctance=3.0,
    wait_reluctance=2.0,
    transfer_penalty=60,
    min_transfer_time=60,
)


def _make_mock_resp(body: bytes):
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


# ── timestamp window tests ────────────────────────────────────────────────────

def test_timestamp_count_60min():
    """06:00–22:00 at 60 min → 17 timestamps, inclusive at both ends."""
    times = build_time_list(6, 0, 22, 0, 60)
    assert len(times) == 17
    assert times[0] == "06:00:00"
    assert times[-1] == "22:00:00"


def test_timestamp_count_15min():
    """06:00–22:00 at 15 min → 65 timestamps."""
    times = build_time_list(6, 0, 22, 0, 15)
    assert len(times) == 65
    assert times[0] == "06:00:00"


def test_timestamp_count_1min():
    """06:00–22:00 at 1 min → 961 timestamps (same count as temporal surface loop)."""
    times = build_time_list(6, 0, 22, 0, 1)
    assert len(times) == 961


def test_timestamp_format():
    """All timestamps must be in HH:MM:SS format."""
    times = build_time_list(8, 0, 8, 30, 15)
    for t in times:
        parts = t.split(":")
        assert len(parts) == 3
        assert all(len(p) == 2 for p in parts)


# ── timestamp wiring (mock client) ───────────────────────────────────────────

def test_get_isochrone_called_per_timestamp():
    """urlopen is called once per timestamp; the time query param matches build_time_list."""
    times = build_time_list(8, 0, 8, 30, 15)
    assert times == ["08:00:00", "08:15:00", "08:30:00"]

    client = IsochroneClient(port=8801, router="default")
    captured_urls: list[str] = []

    def fake_urlopen(req, timeout=None):
        captured_urls.append(req.full_url)
        return _make_mock_resp(_FIXTURE_GEOJSON.encode())

    with patch(
        "easy_otp.core.isochrone_client.urllib.request.urlopen",
        side_effect=fake_urlopen,
    ):
        feature_groups = []
        for t in times:
            resp = client.get_isochrone(**{**_BASE_CALL, "time_hhmmss": t})
            feature_groups.append((t, IsochroneClient.parse_isochrones(resp)))

    assert len(captured_urls) == 3
    assert len(feature_groups) == 3

    for url, expected_time in zip(captured_urls, ["08:00:00", "08:15:00", "08:30:00"]):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        assert params["time"][0] == expected_time


def test_feature_groups_match_timestamps():
    """One parse result per timestamp; each result has the same number of items as the fixture."""
    times = build_time_list(8, 0, 9, 0, 30)  # 08:00, 08:30, 09:00
    assert len(times) == 3

    client = IsochroneClient(port=8801, router="default")
    fixture_item_count = len(IsochroneClient.parse_isochrones(_FIXTURE_GEOJSON))

    def fake_urlopen(req, timeout=None):
        return _make_mock_resp(_FIXTURE_GEOJSON.encode())

    with patch(
        "easy_otp.core.isochrone_client.urllib.request.urlopen",
        side_effect=fake_urlopen,
    ):
        groups = []
        for t in times:
            resp = client.get_isochrone(**{**_BASE_CALL, "time_hhmmss": t})
            groups.append(IsochroneClient.parse_isochrones(resp))

    assert len(groups) == len(times)
    assert all(len(g) == fixture_item_count for g in groups)


def test_parse_isochrones_sorted_ascending():
    """parse_isochrones returns entries sorted by cutoff_sec ascending."""
    items = IsochroneClient.parse_isochrones(_FIXTURE_GEOJSON)
    assert len(items) > 0
    assert all("cutoff_sec" in it and "geometry" in it for it in items)
    cutoffs = [it["cutoff_sec"] for it in items]
    assert cutoffs == sorted(cutoffs)


def test_cutoff_min_conversion():
    """cutoff_sec values from fixture divide evenly into whole minutes."""
    items = IsochroneClient.parse_isochrones(_FIXTURE_GEOJSON)
    for it in items:
        assert it["cutoff_sec"] % 60 == 0, (
            f"Expected cutoff_sec divisible by 60, got {it['cutoff_sec']}"
        )


# ── area CSV tests ────────────────────────────────────────────────────────────

def test_area_csv_row_count(tmp_path):
    """CSV has exactly timestamps × cutoffs data rows (plus header)."""
    times = ["06:00:00", "07:00:00", "08:00:00"]
    cutoffs = [15, 30]
    rows = [(t, c, round(42.0 + i * 0.5, 4)) for i, (t, c) in
            enumerate((t, c) for t in times for c in cutoffs)]

    csv_file = tmp_path / "area.csv"
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time", "cutoff_min", "area_km2"])
        w.writerows(rows)

    with open(csv_file, newline="", encoding="utf-8") as f:
        all_rows = list(csv.reader(f))

    assert all_rows[0] == ["time", "cutoff_min", "area_km2"]
    assert len(all_rows) - 1 == len(times) * len(cutoffs)


def test_area_csv_values_roundtrip():
    """Values written to the CSV are the same when read back."""
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["time", "cutoff_min", "area_km2"])
    w.writerow(["08:00:00", 30, 12.3456])
    out.seek(0)
    rows = list(csv.reader(out))
    assert rows[0] == ["time", "cutoff_min", "area_km2"]
    assert rows[1][0] == "08:00:00"
    assert int(rows[1][1]) == 30
    assert float(rows[1][2]) == pytest.approx(12.3456)


def test_area_csv_empty_when_no_rows(tmp_path):
    """Writing zero data rows still produces a valid CSV with only a header."""
    csv_file = tmp_path / "area_empty.csv"
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time", "cutoff_min", "area_km2"])
        w.writerows([])

    with open(csv_file, newline="", encoding="utf-8") as f:
        all_rows = list(csv.reader(f))

    assert len(all_rows) == 1
    assert all_rows[0] == ["time", "cutoff_min", "area_km2"]
