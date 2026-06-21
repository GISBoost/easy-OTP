"""Bootstrap helpers for optional dependencies in the QGIS Python environment.

No QGIS or Qt imports — safe to call before QgsApplication is initialised.
Two bootstrapped packages are permitted:
  - openpyxl: called by EasyOtpPlugin.initGui() at plugin startup.
  - google.protobuf + gtfs-realtime-bindings: called lazily by BuildRealizedGtfs
    (RT-3 only).  No other algorithm needs this dependency.
"""

import hashlib
import importlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

_OPENPYXL_VERSION = "3.1.5"          # requires Python >=3.8; pure-python wheel confirmed
_ET_XMLFILE_VERSION = "2.0.0"        # sole dependency of openpyxl; requires Python >=3.8
_PROTOBUF_VERSION = "3.20.3"         # last 3.x; py2.py3-none-any.whl confirmed on PyPI
_GTFSRT_BINDINGS_VERSION = "1.0.0"   # requires protobuf>=3.13,<4.0dev; py3-none-any.whl confirmed
_PYPI_JSON_URL = "https://pypi.org/pypi/{pkg}/{version}/json"
_WHEEL_USER_AGENT = "easy-OTP/0.3 urllib"
_HASH_CHUNK = 1 * 1024 * 1024        # 1 MB blocks for SHA-256


def ensure_openpyxl() -> bool:
    """Return True if openpyxl is importable, False otherwise."""
    try:
        import openpyxl  # noqa: F401
        return True
    except ImportError:
        return False


def _get_python_executable() -> str:
    """Return the real Python interpreter, not the QGIS application launcher.

    On Windows QGIS, sys.executable is qgis-bin.exe (the Qt app), not python.exe.
    Passing it to subprocess would launch a new QGIS window instead of pip.
    The real interpreter lives in sys.exec_prefix.
    """
    exe = sys.executable
    if "python" in os.path.basename(exe).lower():
        return exe
    # Windows QGIS: look for python.exe / python3.exe in sys.exec_prefix
    for name in ("python.exe", "python3.exe"):
        candidate = os.path.join(sys.exec_prefix, name)
        if os.path.isfile(candidate):
            return candidate
    # Linux / macOS: look in exec_prefix/bin/
    for name in ("python3", "python"):
        candidate = os.path.join(sys.exec_prefix, "bin", name)
        if os.path.isfile(candidate):
            return candidate
    return exe  # last resort — may still be wrong, but we tried


def _add_user_site_to_path() -> None:
    """Inject user site-packages into sys.path if QGIS omitted it.

    A --user pip install writes to %APPDATA%/Python/Python3xx/site-packages on
    Windows, but QGIS does not add that directory to sys.path at startup.
    Without this call, importlib.invalidate_caches() is not enough for the
    newly installed package to be importable in the same session.
    """
    try:
        import site
        user_site = site.getusersitepackages()
        if user_site and user_site not in sys.path:
            sys.path.insert(0, user_site)
    except (AttributeError, TypeError):
        pass


def _safe_zipextract(zf: zipfile.ZipFile, dest_path: str) -> None:
    """Extract zip safely, skipping members with path traversal (zip slip)."""
    dest_root = os.path.realpath(dest_path)
    prefix = dest_root + os.sep
    for member in zf.infolist():
        target = os.path.realpath(os.path.join(dest_root, member.filename))
        if target != dest_root and not target.startswith(prefix):
            continue  # skip zip-slip attempt
        zf.extract(member, dest_root)


def _writable_target_dir() -> str:
    """Return a writable directory for wheel extraction.

    Tries user site-packages first; falls back to easy_otp/_vendor/.
    """
    try:
        import site
        user_site = site.getusersitepackages()
        if user_site and site.ENABLE_USER_SITE:
            os.makedirs(user_site, exist_ok=True)
            probe = os.path.join(user_site, ".easy_otp_write_test")
            with open(probe, "w"):
                pass
            os.remove(probe)
            return user_site
    except Exception:
        pass
    # Fallback: easy_otp/_vendor/ (sibling of core/)
    vendor = os.path.join(os.path.dirname(os.path.dirname(__file__)), "_vendor")
    os.makedirs(vendor, exist_ok=True)
    return vendor


def _resolve_wheel(pkg: str, version: str) -> tuple[str, str]:
    """Query PyPI JSON API; return (wheel_url, sha256) for the pure-python wheel."""
    url = _PYPI_JSON_URL.format(pkg=pkg, version=version)
    req = urllib.request.Request(url, headers={"User-Agent": _WHEEL_USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310 — HTTPS URL hardcoded to pypi.org; stdlib urllib only, no requests
        data = json.loads(resp.read().decode())
    for entry in data.get("urls", []):
        if (entry.get("packagetype") == "bdist_wheel"
                and entry["filename"].endswith("-none-any.whl")):
            return entry["url"], entry["digests"]["sha256"]
    raise RuntimeError(
        f"No pure-python wheel (-none-any.whl) found for {pkg}=={version} on PyPI."
    )


def _fetch_and_extract_wheel(pkg: str, version: str, target_dir: str) -> None:
    """Download wheel from PyPI, verify SHA-256, extract safely into target_dir."""
    wheel_url, expected_sha256 = _resolve_wheel(pkg, version)
    with tempfile.NamedTemporaryFile(dir=target_dir, suffix=".whl", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        req = urllib.request.Request(wheel_url, headers={"User-Agent": _WHEEL_USER_AGENT})
        with urllib.request.urlopen(req, timeout=120) as resp:  # nosec B310 — HTTPS URL returned by PyPI JSON API; integrity verified by SHA-256 below
            h = hashlib.sha256()
            with open(tmp_path, "wb") as fh:
                while True:
                    chunk = resp.read(_HASH_CHUNK)
                    if not chunk:
                        break
                    fh.write(chunk)
                    h.update(chunk)
        got = h.hexdigest()
        if got.lower() != expected_sha256.lower():
            raise RuntimeError(
                f"SHA-256 mismatch for {pkg} wheel: expected {expected_sha256}, got {got}."
            )
        with zipfile.ZipFile(tmp_path) as zf:
            _safe_zipextract(zf, target_dir)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def _install_openpyxl_via_urllib() -> tuple[bool, str]:
    """Download and extract openpyxl + et_xmlfile wheels via in-process urllib.

    Bypasses the child python.exe subprocess (which has no ssl module on
    QGIS 3.22/Windows OSGeo4W). Still requires internet access.
    """
    try:
        target = _writable_target_dir()
        for pkg, ver in [
            ("et_xmlfile", _ET_XMLFILE_VERSION),
            ("openpyxl", _OPENPYXL_VERSION),
        ]:
            _fetch_and_extract_wheel(pkg, ver, target)
        if target not in sys.path:
            sys.path.insert(0, target)
        importlib.invalidate_caches()
        if ensure_openpyxl():
            return True, f"openpyxl installed via urllib into {target}"
        return False, "Wheel extracted but openpyxl still not importable — restart QGIS."
    except Exception as exc:
        return False, f"In-process urllib wheel install failed: {exc}"


def install_openpyxl() -> tuple[bool, str]:
    """Install openpyxl into the QGIS Python environment.

    Attempt 0: in-process urllib wheel download (works even when child pip has no SSL).
    Attempt 1: <python> -m pip install --user openpyxl (no admin rights needed).
    Attempt 2: <python> -m pip install openpyxl (system-wide; may need admin on Windows).

    Returns (success: bool, message: str).
    """
    # Attempt 0 — in-process urllib wheel download (bypasses child-pip SSL requirement)
    ok, msg = _install_openpyxl_via_urllib()
    if ok:
        return True, msg

    python_exe = _get_python_executable()
    base_cmd = [python_exe, "-m", "pip", "install", "openpyxl"]

    # Attempt 1 — with --user
    try:
        result = subprocess.run(  # nosec S603
            base_cmd + ["--user"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            _add_user_site_to_path()
            importlib.invalidate_caches()
            if ensure_openpyxl():
                return True, "openpyxl installed successfully."
            # pip exited 0 but import still fails — fall through to attempt 2
        # non-zero exit — fall through to attempt 2
    except subprocess.TimeoutExpired:
        return False, "pip --user timed out after 120 s."
    except OSError:
        pass  # fall through to attempt 2

    # Attempt 2 — without --user (system-wide; may need admin on Windows)
    try:
        result = subprocess.run(  # nosec S603
            base_cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            importlib.invalidate_caches()
            if ensure_openpyxl():
                return True, "openpyxl installed successfully."
            return (
                False,
                "pip reported success but openpyxl is still not importable. "
                "Please restart QGIS and try again.",
            )
        stderr = (result.stderr or "").strip()[-500:]
        return (
            False,
            f"Could not install openpyxl automatically:\n\n{stderr}\n\n"
            "Both the in-process urllib download and pip have failed.\n"
            "Possible causes: no internet access, or pip SSL unavailable.\n\n"
            "Install manually from the OSGeo4W Shell (Windows):\n\n"
            "    python -m pip install openpyxl\n\n"
            "Then restart QGIS.",
        )
    except subprocess.TimeoutExpired:
        return False, "pip timed out after 120 s."
    except OSError as exc:
        return False, f"Could not launch pip: {exc}"


# ---------------------------------------------------------------------------
# google.protobuf + gtfs-realtime-bindings — RT-3 (BuildRealizedGtfs) only
# ---------------------------------------------------------------------------

def ensure_gtfsrt_bindings() -> bool:
    """Return True if google.transit.gtfs_realtime_pb2 is importable."""
    try:
        from google.transit import gtfs_realtime_pb2  # noqa: F401
        return True
    except ImportError:
        return False


def _install_gtfsrt_bindings_via_urllib() -> tuple[bool, str]:
    """Download and extract protobuf + gtfs-realtime-bindings wheels via in-process urllib.

    Same mechanism as _install_openpyxl_via_urllib.  Bypasses child-pip SSL issues.
    """
    try:
        target = _writable_target_dir()
        for pkg, ver in [
            ("protobuf", _PROTOBUF_VERSION),
            ("gtfs-realtime-bindings", _GTFSRT_BINDINGS_VERSION),
        ]:
            _fetch_and_extract_wheel(pkg, ver, target)
        if target not in sys.path:
            sys.path.insert(0, target)
        importlib.invalidate_caches()
        if ensure_gtfsrt_bindings():
            return True, f"gtfs-realtime-bindings installed via urllib into {target}"
        return False, "Wheels extracted but gtfs_realtime_pb2 still not importable — restart QGIS."
    except Exception as exc:
        return False, f"In-process urllib wheel install failed: {exc}"


def install_gtfsrt_bindings() -> tuple[bool, str]:
    """Install google.protobuf + gtfs-realtime-bindings into the QGIS Python environment.

    Called lazily by BuildRealizedGtfs (RT-3) — the ONLY algorithm that needs this.
    Three-attempt pattern mirrors install_openpyxl.

    Returns (success: bool, message: str).
    """
    ok, msg = _install_gtfsrt_bindings_via_urllib()
    if ok:
        return True, msg

    python_exe = _get_python_executable()
    pkgs = ["protobuf==" + _PROTOBUF_VERSION, "gtfs-realtime-bindings==" + _GTFSRT_BINDINGS_VERSION]
    base_cmd = [python_exe, "-m", "pip", "install"] + pkgs

    # Attempt 1 — with --user
    try:
        result = subprocess.run(  # nosec S603
            base_cmd + ["--user"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            _add_user_site_to_path()
            importlib.invalidate_caches()
            if ensure_gtfsrt_bindings():
                return True, "gtfs-realtime-bindings installed successfully."
    except subprocess.TimeoutExpired:
        return False, "pip --user timed out after 120 s."
    except OSError:
        pass

    # Attempt 2 — without --user
    try:
        result = subprocess.run(  # nosec S603
            base_cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            importlib.invalidate_caches()
            if ensure_gtfsrt_bindings():
                return True, "gtfs-realtime-bindings installed successfully."
            return (
                False,
                "pip reported success but gtfs_realtime_pb2 is still not importable. "
                "Please restart QGIS and try again.",
            )
        stderr = (result.stderr or "").strip()[-500:]
        return (
            False,
            f"Could not install gtfs-realtime-bindings automatically:\n\n{stderr}\n\n"
            "Both the in-process urllib download and pip have failed.\n"
            "Possible causes: no internet access, or pip SSL unavailable.\n\n"
            "Install manually from the OSGeo4W Shell (Windows):\n\n"
            f"    python -m pip install protobuf=={_PROTOBUF_VERSION} "
            f"gtfs-realtime-bindings=={_GTFSRT_BINDINGS_VERSION}\n\n"
            "Then restart QGIS.",
        )
    except subprocess.TimeoutExpired:
        return False, "pip timed out after 120 s."
    except OSError as exc:
        return False, f"Could not launch pip: {exc}"
