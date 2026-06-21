"""Tests for easy_otp/core/dependencies.py — offline only (no network, no QGIS)."""

import hashlib
import io
import json
import os
import sys
import zipfile
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_pypi_response(pkg: str, version: str, filename: str, url: str,
                        sha256: str, packagetype: str = "bdist_wheel") -> bytes:
    payload = {
        "info": {"name": pkg, "version": version},
        "urls": [
            {
                "filename": filename,
                "url": url,
                "packagetype": packagetype,
                "digests": {"sha256": sha256},
            }
        ],
    }
    return json.dumps(payload).encode()


def _make_wheel_zip(pkg: str) -> bytes:
    """Return minimal wheel ZIP bytes containing pkg/__init__.py."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{pkg}/__init__.py", f"# {pkg}\n")
    return buf.getvalue()


def _sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fake_urlopen(responses: dict):
    """Context-manager factory; maps URL → bytes."""
    def _opener(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        data = responses[url]
        cm = mock.MagicMock()
        cm.__enter__ = lambda s: io.BytesIO(data)
        cm.__exit__ = mock.MagicMock(return_value=False)
        return cm
    return _opener


# ---------------------------------------------------------------------------
# _resolve_wheel
# ---------------------------------------------------------------------------

class TestResolveWheel:
    def test_selects_none_any(self, tmp_path):
        from easy_otp.core.dependencies import _resolve_wheel, _PYPI_JSON_URL

        pkg, version = "openpyxl", "3.1.5"
        expected_url = "https://files.example.com/openpyxl-3.1.5-none-any.whl"
        expected_sha = "abc123"
        payload = {
            "info": {},
            "urls": [
                {   # sdist — must be ignored
                    "filename": "openpyxl-3.1.5.tar.gz",
                    "url": "https://files.example.com/openpyxl-3.1.5.tar.gz",
                    "packagetype": "sdist",
                    "digests": {"sha256": "deadbeef"},
                },
                {   # none-any wheel — must be selected
                    "filename": "openpyxl-3.1.5-py2.py3-none-any.whl",
                    "url": expected_url,
                    "packagetype": "bdist_wheel",
                    "digests": {"sha256": expected_sha},
                },
            ],
        }
        pypi_url = _PYPI_JSON_URL.format(pkg=pkg, version=version)
        responses = {pypi_url: json.dumps(payload).encode()}

        with mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen(responses)):
            url, sha = _resolve_wheel(pkg, version)

        assert url == expected_url
        assert sha == expected_sha

    def test_raises_if_no_none_any(self):
        from easy_otp.core.dependencies import _resolve_wheel, _PYPI_JSON_URL

        pkg, version = "openpyxl", "3.1.5"
        payload = {
            "info": {},
            "urls": [
                {
                    "filename": "openpyxl-3.1.5-cp39-cp39-win_amd64.whl",
                    "url": "https://files.example.com/openpyxl-3.1.5-cp39-cp39-win_amd64.whl",
                    "packagetype": "bdist_wheel",
                    "digests": {"sha256": "deadbeef"},
                },
            ],
        }
        pypi_url = _PYPI_JSON_URL.format(pkg=pkg, version=version)
        responses = {pypi_url: json.dumps(payload).encode()}

        with mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen(responses)):
            with pytest.raises(RuntimeError, match="-none-any.whl"):
                _resolve_wheel(pkg, version)


# ---------------------------------------------------------------------------
# _fetch_and_extract_wheel
# ---------------------------------------------------------------------------

class TestFetchAndExtractWheel:
    def test_sha_mismatch_raises(self, tmp_path):
        from easy_otp.core.dependencies import (
            _fetch_and_extract_wheel, _PYPI_JSON_URL,
        )

        pkg, version = "openpyxl", "3.1.5"
        wheel_bytes = _make_wheel_zip(pkg)
        real_sha = _sha256_of(wheel_bytes)
        wrong_sha = "0" * 64

        pypi_url = _PYPI_JSON_URL.format(pkg=pkg, version=version)
        wheel_url = "https://files.example.com/openpyxl-3.1.5-py2.py3-none-any.whl"
        pypi_payload = {
            "info": {},
            "urls": [{
                "filename": "openpyxl-3.1.5-py2.py3-none-any.whl",
                "url": wheel_url,
                "packagetype": "bdist_wheel",
                "digests": {"sha256": wrong_sha},
            }],
        }
        responses = {
            pypi_url: json.dumps(pypi_payload).encode(),
            wheel_url: wheel_bytes,
        }

        with mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen(responses)):
            with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
                _fetch_and_extract_wheel(pkg, version, str(tmp_path))

    def test_correct_sha_extracts(self, tmp_path):
        from easy_otp.core.dependencies import (
            _fetch_and_extract_wheel, _PYPI_JSON_URL,
        )

        pkg, version = "openpyxl", "3.1.5"
        wheel_bytes = _make_wheel_zip(pkg)
        sha = _sha256_of(wheel_bytes)

        pypi_url = _PYPI_JSON_URL.format(pkg=pkg, version=version)
        wheel_url = "https://files.example.com/openpyxl-3.1.5-py2.py3-none-any.whl"
        pypi_payload = {
            "info": {},
            "urls": [{
                "filename": "openpyxl-3.1.5-py2.py3-none-any.whl",
                "url": wheel_url,
                "packagetype": "bdist_wheel",
                "digests": {"sha256": sha},
            }],
        }
        responses = {
            pypi_url: json.dumps(pypi_payload).encode(),
            wheel_url: wheel_bytes,
        }

        with mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen(responses)):
            _fetch_and_extract_wheel(pkg, version, str(tmp_path))

        assert (tmp_path / pkg / "__init__.py").exists()


# ---------------------------------------------------------------------------
# _safe_zipextract
# ---------------------------------------------------------------------------

class TestSafeZipextract:
    def test_blocks_path_traversal(self, tmp_path):
        from easy_otp.core.dependencies import _safe_zipextract

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("../evil.txt", "pwned")
        buf.seek(0)

        target = tmp_path / "extract"
        target.mkdir()
        parent_evil = tmp_path / "evil.txt"

        with zipfile.ZipFile(buf) as zf:
            _safe_zipextract(zf, str(target))

        assert not parent_evil.exists(), "Path traversal was not blocked"

    def test_normal_member_extracted(self, tmp_path):
        from easy_otp.core.dependencies import _safe_zipextract

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("openpyxl/__init__.py", "# ok\n")
        buf.seek(0)

        target = tmp_path / "extract"
        target.mkdir()

        with zipfile.ZipFile(buf) as zf:
            _safe_zipextract(zf, str(target))

        assert (target / "openpyxl" / "__init__.py").exists()


# ---------------------------------------------------------------------------
# _install_openpyxl_via_urllib (integration — mocked network + target dir)
# ---------------------------------------------------------------------------

class TestInstallOpenpyxlOffline:
    def test_success_path(self, tmp_path, monkeypatch):
        """After offline install the target dir is in sys.path and openpyxl importable."""
        from easy_otp.core import dependencies as dep

        # Build minimal wheels
        et_bytes = _make_wheel_zip("et_xmlfile")
        ox_bytes = _make_wheel_zip("openpyxl")
        et_sha = _sha256_of(et_bytes)
        ox_sha = _sha256_of(ox_bytes)

        et_wheel_url = "https://files.example.com/et_xmlfile-2.0.0-py2.py3-none-any.whl"
        ox_wheel_url = "https://files.example.com/openpyxl-3.1.5-py2.py3-none-any.whl"

        def fake_resolve(pkg, version):
            if pkg == "et_xmlfile":
                return et_wheel_url, et_sha
            return ox_wheel_url, ox_sha

        responses = {et_wheel_url: et_bytes, ox_wheel_url: ox_bytes}

        monkeypatch.setattr(dep, "_writable_target_dir", lambda: str(tmp_path))
        monkeypatch.setattr(dep, "_resolve_wheel", fake_resolve)

        with mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen(responses)):
            with mock.patch.object(dep, "ensure_openpyxl", return_value=True):
                ok, msg = dep._install_openpyxl_via_urllib()

        assert ok
        assert "via urllib into" in msg
        assert str(tmp_path) in sys.path

        # cleanup sys.path so other tests aren't affected
        sys.path.remove(str(tmp_path))

    def test_sha_failure_returns_false(self, tmp_path, monkeypatch):
        from easy_otp.core import dependencies as dep

        et_wheel_url = "https://files.example.com/et_xmlfile-2.0.0-py2.py3-none-any.whl"

        def fake_resolve(pkg, version):
            return et_wheel_url, "0" * 64  # wrong hash

        responses = {et_wheel_url: _make_wheel_zip("et_xmlfile")}

        monkeypatch.setattr(dep, "_writable_target_dir", lambda: str(tmp_path))
        monkeypatch.setattr(dep, "_resolve_wheel", fake_resolve)

        with mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen(responses)):
            ok, msg = dep._install_openpyxl_via_urllib()

        assert not ok
        assert "In-process urllib wheel install failed" in msg
