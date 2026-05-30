"""Bootstrap helper: ensures openpyxl is available in the QGIS Python environment.

No QGIS or Qt imports — safe to call before QgsApplication is initialised.
Called by EasyOtpPlugin.initGui() before provider registration.
This is the sole permitted exception to the project's "zero pip install" rule.
"""

import importlib
import os
import subprocess
import sys


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


def install_openpyxl() -> tuple[bool, str]:
    """Install openpyxl via pip into the QGIS Python environment.

    Attempt 1: <python> -m pip install --user openpyxl
      (writes to user site-packages; no admin rights needed)
    Attempt 2: <python> -m pip install openpyxl
      (writes to QGIS site-packages; requires admin on Windows)

    After a successful pip exit, adds user site-packages to sys.path (if
    needed), invalidates the import cache, and re-checks importability.
    If the import still fails, the user is asked to restart QGIS.

    Returns (success: bool, message: str).
    """
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
        return False, f"pip failed (exit {result.returncode}):\n{stderr}"
    except subprocess.TimeoutExpired:
        return False, "pip timed out after 120 s."
    except OSError as exc:
        return False, f"Could not launch pip: {exc}"
