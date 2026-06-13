"""Tests for easy_otp.core.gtfsrt_config.

Pure stdlib — no QGIS / GDAL dependency. Run with standard pytest:
    py -m pytest easy_otp/test/test_gtfsrt_config.py -v
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from easy_otp.core.gtfsrt_config import (
    suggest_feed_id,
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
