"""Tests for easy_otp.core.gtfsrt_config.

Pure stdlib — no QGIS / GDAL dependency. Run with standard pytest:
    py -m pytest easy_otp/test/test_gtfsrt_config.py -v
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from easy_otp.core.gtfsrt_config import (
    assess_rt_effectiveness,
    count_rt_polls,
    suggest_feed_id,
    summarize_rt_log,
    summarize_trip_update_log,
    validate_rt_url,
    write_router_config,
)


# ---------------------------------------------------------------------------
# suggest_feed_id
# ---------------------------------------------------------------------------

def _make_gtfs_zip(tmp_path: Path, name: str, files: dict[str, str]) -> str:
    """Build a minimal GTFS zip from {arcname: content} and return its path."""
    zip_path = tmp_path / name
    with zipfile.ZipFile(zip_path, "w") as zf:
        for arcname, content in files.items():
            zf.writestr(arcname, content)
    return str(zip_path)


def test_suggest_feed_id_happy(tmp_path: Path):
    """feed_info.txt with a feed_id column returns the value."""
    zip_path = _make_gtfs_zip(
        tmp_path,
        "gtfs.zip",
        {
            "feed_info.txt": (
                "feed_publisher_name,feed_lang,feed_id\n"
                "ZTM Poznan,pl,ztm-poznan\n"
            ),
            "stops.txt": "stop_id,stop_name\n1,Test\n",
        },
    )
    assert suggest_feed_id(zip_path) == "ztm-poznan"


def test_suggest_feed_id_no_file(tmp_path: Path):
    """Archive without feed_info.txt returns None."""
    zip_path = _make_gtfs_zip(
        tmp_path, "gtfs.zip", {"stops.txt": "stop_id,stop_name\n1,Test\n"}
    )
    assert suggest_feed_id(zip_path) is None


def test_suggest_feed_id_no_column(tmp_path: Path):
    """feed_info.txt without a feed_id column returns None."""
    zip_path = _make_gtfs_zip(
        tmp_path,
        "gtfs.zip",
        {"feed_info.txt": "feed_publisher_name,feed_lang\nZTM,pl\n"},
    )
    assert suggest_feed_id(zip_path) is None


def test_suggest_feed_id_bom(tmp_path: Path):
    """A leading UTF-8 BOM on the header is stripped."""
    zip_path = tmp_path / "gtfs.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(
            "feed_info.txt",
            "﻿feed_id,feed_lang\nbom-feed,pl\n".encode("utf-8"),
        )
    assert suggest_feed_id(str(zip_path)) == "bom-feed"


# ---------------------------------------------------------------------------
# write_router_config
# ---------------------------------------------------------------------------

def test_write_router_config_path_and_structure(tmp_path: Path):
    """File lands in graph_dir with OTP 1.5.0 updater + routingDefaults."""
    graph_dir = tmp_path / "graphs" / "abc12345"
    url = "https://www.ztm.poznan.pl/trip_updates.pb"
    feed_id = "ztm-poznan"
    polling = 45

    write_router_config(graph_dir, url, feed_id, polling)

    config_path = graph_dir / "router-config.json"
    assert config_path.is_file()

    config = json.loads(config_path.read_text(encoding="utf-8"))

    assert "routingDefaults" in config

    updater = config["updaters"][0]
    assert updater["type"] == "stop-time-updater"
    assert updater["sourceType"] == "gtfs-http"
    assert updater["frequencySec"] == polling
    assert updater["url"] == url
    assert updater["feedId"] == feed_id


def test_write_router_config_creates_missing_dir(tmp_path: Path):
    """graph_dir is created if it does not yet exist."""
    graph_dir = tmp_path / "does" / "not" / "exist"
    write_router_config(graph_dir, "https://x/u.pb", "1", 60)
    assert (graph_dir / "router-config.json").is_file()


def test_write_router_config_default_omits_fuzzy(tmp_path: Path):
    """Without fuzzy_matching the updater has no fuzzyTripMatching key."""
    graph_dir = tmp_path / "g"
    write_router_config(graph_dir, "https://x/u.pb", "1", 60)
    config = json.loads((graph_dir / "router-config.json").read_text(encoding="utf-8"))
    assert "fuzzyTripMatching" not in config["updaters"][0]


def test_write_router_config_fuzzy_matching(tmp_path: Path):
    """fuzzy_matching=True adds the OTP 1.5 fuzzyTripMatching token."""
    graph_dir = tmp_path / "g"
    write_router_config(graph_dir, "https://x/u.pb", "1", 60, fuzzy_matching=True)
    config = json.loads((graph_dir / "router-config.json").read_text(encoding="utf-8"))
    assert config["updaters"][0]["fuzzyTripMatching"] is True


def test_write_router_config_fuzzy_nesting(tmp_path: Path):
    """fuzzyTripMatching must sit ON the stop-time-updater object (A2).

    OTP 1.5's PollingStoptimeUpdater reads fuzzyTripMatching from the updater block;
    placing it at the top level (or anywhere else) is silently ignored. Lock the
    nesting so a future refactor cannot quietly break fuzzy matching.
    """
    graph_dir = tmp_path / "g"
    write_router_config(graph_dir, "https://x/u.pb", "1", 60, fuzzy_matching=True)
    config = json.loads((graph_dir / "router-config.json").read_text(encoding="utf-8"))
    updater = config["updaters"][0]
    assert updater["type"] == "stop-time-updater"
    assert updater["fuzzyTripMatching"] is True
    # Not leaked to the top level, where OTP would ignore it.
    assert "fuzzyTripMatching" not in config


# ---------------------------------------------------------------------------
# count_rt_polls
# ---------------------------------------------------------------------------

def test_count_rt_polls_none():
    """No 'Applied N' summary line → 0 completed polls."""
    assert count_rt_polls("WARN No pattern found for tripId X, skipping.\n") == 0


def test_count_rt_polls_counts_summaries():
    """Each 'Applied N trip updates' line counts as one completed poll, incl. zero."""
    text = (
        "INFO Applied 0 trip updates.\n"
        "WARN No pattern found for tripId X, skipping.\n"
        "INFO Applied 5 trip updates.\n"
    )
    assert count_rt_polls(text) == 2


# ---------------------------------------------------------------------------
# summarize_trip_update_log
# ---------------------------------------------------------------------------

def test_summarize_log_zero_applied():
    """All TripUpdates skipped → (0, N) signalling an edition mismatch."""
    text = (
        "WARN No pattern found for tripId 7_17508^N+, skipping TripUpdate.\n"
        "WARN Failed to apply TripUpdate.\n"
        "INFO Applied 0 trip updates.\n"
        "WARN No pattern found for tripId 5_17528^F,N, skipping TripUpdate.\n"
        "INFO Applied 0 trip updates.\n"
    )
    assert summarize_trip_update_log(text) == (0, 2)


def test_summarize_log_some_applied():
    """Applied counts are summed across polls."""
    text = (
        "INFO Applied 5 trip updates.\n"
        "INFO Applied 3 trip updates.\n"
        "WARN No pattern found for tripId X, skipping TripUpdate.\n"
    )
    applied, skipped = summarize_trip_update_log(text)
    assert applied == 8
    assert skipped == 1


def test_summarize_log_empty():
    """No RT lines → (0, 0)."""
    assert summarize_trip_update_log("INFO Grizzly server running.\n") == (0, 0)


# ---------------------------------------------------------------------------
# summarize_rt_log
# ---------------------------------------------------------------------------

_POZNAN_SNIPPET = (
    "WARN (TimetableSnapshotSource.java:318) No pattern found for tripId 7_17509^N+, "
    "skipping TripUpdate.\n"
    "WARN (TimetableSnapshotSource.java:245) Failed to apply TripUpdate.\n"
    "INFO (TimetableSnapshotSource.java:250) Applied 0 trip updates.\n"
    "WARN (TimetableSnapshotSource.java:318) No pattern found for tripId 5_18096^N+, "
    "skipping TripUpdate.\n"
    "WARN (TimetableSnapshotSource.java:245) Failed to apply TripUpdate.\n"
)

_GDANSK_GOOD_SNIPPET = (
    "ERROR (TripTimes.java:447) Negative running time in TripTimes after stop index 5.\n"
    "ERROR (Timetable.java:619) TripTimes are non-increasing after applying GTFS-RT "
    "delay propagation to trip 184202606202049_212_184-04.\n"
    "WARN (TimetableSnapshotSource.java:245) Failed to apply TripUpdate.\n"
)


def test_summarize_rt_log_poznan_signals():
    """Poznań data-mismatch: no_pattern>0, applied_polling==0, non_increasing==0."""
    signals = summarize_rt_log(_POZNAN_SNIPPET)
    assert signals["no_pattern"] == 2
    assert signals["applied_polling"] == 0
    assert signals["non_increasing"] == 0
    assert signals["failed_apply"] == 2
    assert signals["polls"] == 1  # one "Applied 0" line


def test_summarize_rt_log_gdansk_good_signals():
    """Gdańsk matched-but-rejected: non_increasing>0, no_pattern==0."""
    signals = summarize_rt_log(_GDANSK_GOOD_SNIPPET)
    # "Negative running time" + "non-increasing" — both match _NON_INCREASING_RE
    assert signals["non_increasing"] == 2
    assert signals["no_pattern"] == 0
    assert signals["failed_apply"] == 1
    assert signals["applied_polling"] == 0
    assert signals["polls"] == 0


def test_summarize_rt_log_polling_path_positive():
    """PollingStoptimeUpdater path: Applied N>0 → applied_polling summed correctly."""
    text = (
        "INFO Applied 5 trip updates.\n"
        "INFO Applied 3 trip updates.\n"
        "WARN No pattern found for tripId X, skipping TripUpdate.\n"
    )
    signals = summarize_rt_log(text)
    assert signals["applied_polling"] == 8
    assert signals["no_pattern"] == 1
    assert signals["polls"] == 2


def test_summarize_rt_log_empty_log():
    """Startup-only log (no RT lines) → all zeros."""
    signals = summarize_rt_log("INFO Grizzly server running.\n")
    assert signals == {
        "applied_polling": 0,
        "no_pattern": 0,
        "failed_apply": 0,
        "non_increasing": 0,
        "polls": 0,
    }


def test_summarize_rt_log_caps_at_2mb():
    """Text longer than 2 MB is tail-truncated before matching."""
    # Build >2 MB of junk followed by one signal line.
    padding = "X" * (2_100_000)
    text = padding + "\nWARN (TimetableSnapshotSource.java:245) Failed to apply TripUpdate.\n"
    signals = summarize_rt_log(text)
    assert signals["failed_apply"] == 1


# ---------------------------------------------------------------------------
# assess_rt_effectiveness
# ---------------------------------------------------------------------------

def _sigs(**kwargs) -> dict:
    """Build a signals dict with zero defaults, overridden by kwargs."""
    base = {
        "applied_polling": 0,
        "no_pattern": 0,
        "failed_apply": 0,
        "non_increasing": 0,
        "polls": 0,
    }
    base.update(kwargs)
    return base


def test_assess_poznan_data_mismatch():
    """no_pattern>0, non_increasing==0 → not effective (0)."""
    signals = _sigs(no_pattern=170, failed_apply=171, polls=1)
    assert assess_rt_effectiveness(signals, {}) == 0


def test_assess_gdansk_good_non_increasing():
    """non_increasing>0 → effective (1), regardless of failed_apply or updaters."""
    signals = _sigs(non_increasing=9, failed_apply=9)
    assert assess_rt_effectiveness(signals, {}) == 1


def test_assess_polling_path_positive():
    """applied_polling>0 → effective (1)."""
    signals = _sigs(applied_polling=5, no_pattern=1)
    assert assess_rt_effectiveness(signals, {}) == 1


def test_assess_silent_success_with_updater():
    """All signals zero but updater registered → effective (1, best-effort)."""
    signals = _sigs()
    updaters = {"0": "Streaming stoptime updater with update source = GtfsRealtime"}
    assert assess_rt_effectiveness(signals, updaters) == 1


def test_assess_inconclusive_no_signals_no_updater():
    """All signals zero, no updater info → inconclusive (-1)."""
    assert assess_rt_effectiveness(_sigs(), {}) == -1


def test_assess_mixed_no_pattern_and_non_increasing():
    """non_increasing>0 beats no_pattern>0 — rule 2 fires before rule 3."""
    signals = _sigs(no_pattern=5, non_increasing=3, failed_apply=8)
    assert assess_rt_effectiveness(signals, {}) == 1


def test_assess_failed_apply_no_no_pattern():
    """failed_apply>0 with no_pattern==0 → effective (rule 4)."""
    signals = _sigs(failed_apply=2)
    assert assess_rt_effectiveness(signals, {}) == 1


def test_assess_updater_present_but_data_mismatch():
    """no_pattern>0 beats non-empty updaters — rule 3 fires before rule 5."""
    signals = _sigs(no_pattern=5, failed_apply=5)
    updaters = {"0": "Streaming stoptime updater with update source = GtfsRealtime"}
    assert assess_rt_effectiveness(signals, updaters) == 0


# ---------------------------------------------------------------------------
# Fixture-based integration tests (skipped if log files absent)
# ---------------------------------------------------------------------------

def test_summarize_rt_log_poznan_fixture():
    """Verdict against the committed Poznań evidence log."""
    log = Path(__file__).parent.parent.parent / "docs" / "serverlog" / "otp_server_56053e4b_(poznan).log"
    if not log.exists():
        import pytest
        pytest.skip("Poznań fixture log not present in docs/serverlog/")
    signals = summarize_rt_log(log.read_text(encoding="utf-8", errors="replace"))
    assert signals["no_pattern"] > 0
    assert signals["applied_polling"] == 0
    assert signals["non_increasing"] == 0
    assert assess_rt_effectiveness(signals, {}) == 0


def test_summarize_rt_log_gdansk_good_fixture():
    """Verdict against the committed Gdańsk good-run evidence log."""
    log = Path(__file__).parent.parent.parent / "docs" / "serverlog" / "otp_server_e3327fe0_20260620-205143(gdansk).log"
    if not log.exists():
        import pytest
        pytest.skip("Gdańsk fixture log not present in docs/serverlog/")
    signals = summarize_rt_log(log.read_text(encoding="utf-8", errors="replace"))
    assert signals["non_increasing"] > 0
    assert signals["no_pattern"] == 0
    assert assess_rt_effectiveness(signals, {}) == 1


# ---------------------------------------------------------------------------
# validate_rt_url — scheme guard (offline, deterministic)
# ---------------------------------------------------------------------------

def test_validate_rt_url_rejects_file_scheme():
    """A file:// URL is rejected without a network call."""
    ok, msg = validate_rt_url("file:///etc/passwd")
    assert ok is False
    assert msg == "URL must use http or https"


def test_validate_rt_url_rejects_ftp_scheme():
    """An ftp:// URL is rejected without a network call."""
    ok, msg = validate_rt_url("ftp://example.com/feed.pb")
    assert ok is False
    assert msg == "URL must use http or https"
