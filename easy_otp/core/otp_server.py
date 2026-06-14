"""OpenTripPlanner 1.5.0 process lifecycle: graph build, server start, cleanup.

Implements PR section 6: SHA-256-keyed graph cache, separate --build and
--server phases, --analyst --pointSets serve mode, health-check via
OtpClient.get_router_info(), and guaranteed teardown via the OtpServer
context manager.

Subprocess output for both build and serve is redirected to logfiles inside
WORK_DIR so the OS pipe can never fill (the algorithm is single-threaded;
streaming logs to QGIS feedback is a milestone-7 hardening item).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from .otp_client import OtpClient, OtpClientError, probe_otp

ROUTER_ID_LEN = 8
_LOG_TAIL_BYTES = 2048
_HASH_CHUNK = 1 << 20  # 1 MiB

_OTP_EXIT_HINT = (
    "Common causes: wrong Java version (OTP 1.5.0 needs Java 8 — run "
    "TestOtpServer to verify), port already in use (pick a different "
    "OTP_PORT), corrupt/wrong-version jar (re-download "
    "otp-1.5.0-shaded.jar from Maven Central), invalid or empty GTFS feed "
    "(check that .zip files are valid GTFS archives), or insufficient heap "
    "memory (increase -Xmx, e.g. set OTP_BUILD_XMX=8g or OTP_SERVE_XMX=8g)."
)

_JAVA_VERSION_RE = re.compile(r'(?:java|openjdk)\s+version\s+"([^"]+)"', re.IGNORECASE)


def check_java_version(java_path: Path) -> "tuple[bool, str, str]":
    """Run ``java -version`` and return ``(is_java8, version_str, error_msg)``.

    ``is_java8`` is True when the binary reports Java 1.8.x or 8.x.
    On failure ``version_str`` is empty and ``error_msg`` explains what went
    wrong in user-readable language.

    Called from both ``TestOtpServer`` and ``RunTemporalAccessibility``
    so that a wrong Java binary is diagnosed *before* OTP is launched.
    """
    if not java_path.is_file():
        return (
            False,
            "",
            f"Java binary not found: {java_path}. "
            "Download portable Eclipse Temurin 8 (https://adoptium.net/temurin/releases/?version=8), "
            "unzip it, and point the 'Java 8 binary' parameter at bin/java "
            "(or bin\\java.exe on Windows).",
        )
    try:
        proc = subprocess.run(
            [str(java_path), "-version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return False, "", f"Timed out running '{java_path} -version'."
    except OSError as e:
        return False, "", f"Could not invoke '{java_path} -version': {e}"

    banner = (proc.stderr or proc.stdout or "").strip()
    first_line = banner.splitlines()[0] if banner else ""
    match = _JAVA_VERSION_RE.search(first_line)
    if not match:
        return (
            False,
            "",
            f"Could not parse Java version from output: {banner[:200]!r}",
        )
    version = match.group(1)
    if version.startswith("1.8.") or version.startswith("8."):
        return True, version, ""
    return (
        False,
        version,
        f"OTP 1.5.0 requires Java 8; detected version '{version}'. "
        "Download portable Eclipse Temurin 8 "
        "(https://adoptium.net/temurin/releases/?version=8), "
        "unzip it, and point the 'Java 8 binary' parameter at bin/java "
        "(or bin\\java.exe on Windows).",
    )


# ---------- pure helpers ----------

def compute_router_id(osm_pbf: Path, gtfs_files: Iterable[Path]) -> str:
    """Deterministic ID from the byte contents of the input files."""
    h = hashlib.sha256()
    for path in [osm_pbf, *sorted(gtfs_files)]:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(_HASH_CHUNK), b""):
                h.update(chunk)
    return h.hexdigest()[:ROUTER_ID_LEN]


def discover_gtfs_files(gtfs_dir: Path) -> list[Path]:
    if not gtfs_dir.is_dir():
        raise FileNotFoundError(f"GTFS folder does not exist: {gtfs_dir}")
    files = sorted(gtfs_dir.glob("*.zip"))
    if not files:
        raise FileNotFoundError(
            f"No GTFS .zip feeds found in {gtfs_dir}. "
            f"Place one or more GTFS archives there."
        )
    return files


def ensure_router_dir(
    work_dir: Path, router_id: str, osm_pbf: Path, gtfs_files: Iterable[Path]
) -> Path:
    router_dir = work_dir / "graphs" / router_id
    router_dir.mkdir(parents=True, exist_ok=True)
    _copy_if_missing(osm_pbf, router_dir / osm_pbf.name)
    for gtfs in gtfs_files:
        _copy_if_missing(gtfs, router_dir / gtfs.name)
    return router_dir


def ensure_pointsets_dir(work_dir: Path) -> Path:
    pointsets = work_dir / "pointsets"
    pointsets.mkdir(parents=True, exist_ok=True)
    return pointsets


# Embedded fallback router config — matches the Marcus Young OTP-1.5.0 tutorial
# defaults. Without a router-config.json present in the router dir, OTP's
# built-in defaults leave the analyst SPT collapsed to a handful of vertices
# (confirmed by user log comparison 2026-05-25).
DEFAULT_ROUTER_CONFIG = {
    "routingDefaults": {
        "walkSpeed": 1.3,
        "walkReluctance": 5.0,
        "waitReluctance": 4.0,
        "stairsReluctance": 4.0,
        "carDropoffTime": 240,
    }
}


def ensure_router_config(
    router_dir: Path,
    source_dir: Optional[Path],
    feedback,
    config_file: Optional[Path] = None,
) -> None:
    """Ensure router-config.json (and build-config.json if user-supplied) exist.

    OTP reads router-config at server start; without it, analyst surface
    routing degenerates (SPT does not expand). Priority:

    1. ``config_file`` — explicit path supplied by the user (ROUTER_CONFIG_PATH).
    2. ``source_dir/router-config.json`` — GTFS-folder convention.
    3. Embedded ``DEFAULT_ROUTER_CONFIG`` — written if none of the above exists.
    4. Existing file in ``router_dir`` — left untouched if already present.

    build-config.json is only copied from source_dir if present — never
    auto-generated, since OTP's defaults produce a working graph and a wrong
    embedded build-config could break things at build time.
    """
    router_cfg = router_dir / "router-config.json"
    build_cfg = router_dir / "build-config.json"

    if config_file is not None and config_file.is_file():
        src_router = config_file
    else:
        src_router = (source_dir / "router-config.json") if source_dir else None
    src_build = (source_dir / "build-config.json") if source_dir else None

    if src_router is not None and src_router.is_file():
        shutil.copy2(src_router, router_cfg)
        feedback.pushInfo(f"router-config.json copied from {src_router}")
    elif not router_cfg.is_file():
        router_cfg.write_text(json.dumps(DEFAULT_ROUTER_CONFIG, indent=2))
        feedback.pushInfo(
            "router-config.json generated with built-in defaults "
            "(walkSpeed=1.3, walkReluctance=5.0, waitReluctance=4.0, "
            "stairsReluctance=4.0, carDropoffTime=240)"
        )
    else:
        feedback.pushInfo(f"Using existing router-config.json at {router_cfg}")

    if src_build is not None and src_build.is_file():
        shutil.copy2(src_build, build_cfg)
        feedback.pushInfo(f"build-config.json copied from {src_build}")


def graph_obj_exists(work_dir: Path, router_id: str) -> bool:
    return (work_dir / "graphs" / router_id / "Graph.obj").is_file()


def graph_build_complete(work_dir: Path, router_id: str) -> bool:
    """True only when a build finished cleanly.

    Sentinel-pair invariant: Graph.obj AND easy_otp_meta.json both present
    ⇔ build completed. write_meta() is called only after a successful
    build, and build_graph() defensively wipes both files at start, so a
    cancelled/crashed build can never produce a state that looks complete.
    """
    router_dir = work_dir / "graphs" / router_id
    return (router_dir / "Graph.obj").is_file() and (router_dir / "easy_otp_meta.json").is_file()


def write_meta(router_dir: Path, jar_path: Path, inputs: list[Path]) -> None:
    meta = {
        "router_id": router_dir.name,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "otp_jar": str(jar_path),
        "inputs": [p.name for p in inputs],
    }
    (router_dir / "easy_otp_meta.json").write_text(json.dumps(meta, indent=2))


def port_is_listening(port: int) -> bool:
    """True if some process accepts TCP connections on 127.0.0.1:port.

    More reliable than bind-based detection on Windows, where bind to
    127.0.0.1:port can succeed even when another process holds 0.0.0.0:port.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except (ConnectionRefusedError, socket.timeout, OSError):
        return False
    finally:
        s.close()


def port_is_free(port: int) -> bool:
    """Inverse of port_is_listening — kept for readability at call sites."""
    return not port_is_listening(port)


# ---------- subprocess ----------

def _popen_kwargs() -> dict:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {}


def _log_tail(log_path: Path, n: int = _LOG_TAIL_BYTES) -> str:
    try:
        with open(log_path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - n))
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return "(log unavailable)"


def build_graph(
    java_path: Path,
    jar_path: Path,
    xmx: str,
    work_dir: Path,
    router_id: str,
    feedback,
) -> None:
    """Run OTP --build for the router. Blocks the algorithm thread."""
    router_dir = work_dir / "graphs" / router_id
    # Defensive: wipe any stale completion markers so a cancelled/crashed
    # build cannot leave the dir in a state that graph_build_complete()
    # treats as cached.
    for stale in (router_dir / "Graph.obj", router_dir / "easy_otp_meta.json"):
        try:
            stale.unlink()
        except FileNotFoundError:
            pass
    log_path = work_dir / f"otp_build_{router_id}.log"
    cmd = [
        str(java_path),
        f"-Xmx{xmx}",
        "-jar",
        str(jar_path),
        "--build",
        str(router_dir),
    ]
    feedback.pushInfo(f"$ {' '.join(cmd)}")
    feedback.pushInfo(f"Build log: {log_path}")
    with open(log_path, "wb") as log_fh:
        proc = subprocess.Popen(
            cmd,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            **_popen_kwargs(),
        )
        start = time.monotonic()
        try:
            while proc.poll() is None:
                if feedback.isCanceled():
                    _terminate(proc, feedback)
                    raise RuntimeError("OTP build cancelled by user.")
                elapsed = int(time.monotonic() - start)
                feedback.pushInfo(f"…building graph ({elapsed}s elapsed)")
                time.sleep(2.0)
        except BaseException:
            _terminate(proc, feedback)
            raise
    if proc.returncode != 0:
        raise RuntimeError(
            f"OTP --build failed (exit {proc.returncode}). {_OTP_EXIT_HINT}\n"
            f"Last log:\n{_log_tail(log_path)}"
        )
    feedback.pushInfo(f"Graph build finished in {int(time.monotonic() - start)}s.")


def start_server(
    java_path: Path,
    jar_path: Path,
    xmx: str,
    work_dir: Path,
    router_id: str,
    port: int,
    pointsets_dir: Path,
    feedback,
    show_console: bool = False,
) -> tuple[subprocess.Popen, Path]:
    """Spawn OTP --server --analyst --pointSets. Returns (process, log_path).

    OTP's output is ALWAYS redirected to ``otp_server_<router_id>_<timestamp>.log``
    so it survives a crash — the file is available for analysis even after the
    process (and any console window) is gone. The timestamp keeps each run's log
    distinct, so re-running after a crash does not wipe the log the user was told
    to inspect. When show_console=True on Windows, a separate window is opened that
    live-tails that same logfile; unlike OTP's own console it stays open after OTP
    exits, so the final error remains visible.
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = work_dir / f"otp_server_{router_id}_{stamp}.log"
    cmd = [
        str(java_path),
        f"-Xmx{xmx}",
        "-jar",
        str(jar_path),
        "--server",
        "--basePath",
        str(work_dir),
        "--router",
        router_id,
        "--port",
        str(port),
        "--analyst",
        "--pointSets",
        str(pointsets_dir),
    ]
    feedback.pushInfo(f"$ {' '.join(cmd)}")
    feedback.pushInfo(f"Server log: {log_path}")
    # Always log to file (crash-proof). log_fh stays open; closed by the OS when
    # proc exits / we kill it.
    log_fh = open(log_path, "wb")  # noqa: SIM115
    proc = subprocess.Popen(
        cmd,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        **_popen_kwargs(),
    )
    if show_console and sys.platform == "win32":
        _spawn_log_tail_console(log_path, feedback)
    return proc, log_path


def _spawn_log_tail_console(log_path: Path, feedback) -> None:
    """Open a separate Windows console that live-tails the OTP server logfile.

    Replaces attaching OTP directly to its own console (which vanished on crash,
    taking the visible logs with it). The tail reads the persistent logfile, so it
    keeps showing output — including the final error — after OTP exits. Best-effort:
    failure to open the window never breaks the run. The window must be closed
    manually; the full log is always at ``log_path`` regardless.
    """
    # QGIS's bundled environment often has a stripped PATH without System32, so the
    # bare name "powershell" raises FileNotFoundError under CreateProcess. Resolve
    # the full path first, then fall back to PATH lookup, then the bare name.
    ps_full = os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"),
        "System32", "WindowsPowerShell", "v1.0", "powershell.exe",
    )
    powershell = ps_full if os.path.isfile(ps_full) else (shutil.which("powershell") or "powershell")
    # Escape single quotes for the PowerShell single-quoted string literal: a path
    # like C:\Users\O'Brien\... would otherwise terminate the string early (lost
    # live view, minor injection). In PS, '' is the literal-quote escape.
    ps_literal = str(log_path).replace("'", "''")
    try:
        subprocess.Popen(
            [
                powershell,
                "-NoExit",
                "-Command",
                f"Get-Content -LiteralPath '{ps_literal}' -Wait -Tail 2000",
            ],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        feedback.pushInfo(
            "Live OTP server log opened in a separate window (SHOW_OTP_CONSOLE=True); "
            "it stays open after a crash so the error remains readable."
        )
    except Exception as e:  # noqa: BLE001 — the tail window is a convenience only
        feedback.pushWarning(
            f"Could not open live log window: {e!r}. Full log is still at {log_path}."
        )


def wait_until_ready(
    client: OtpClient,
    feedback,
    timeout_s: float = 300.0,
    poll_interval_s: float = 2.0,
    log_path: Optional[Path] = None,
    proc: Optional[subprocess.Popen] = None,
) -> None:
    """Poll the router endpoint until it answers 200 (= router loaded)."""
    deadline = time.monotonic() + timeout_s
    start = time.monotonic()
    while time.monotonic() < deadline:
        if feedback.isCanceled():
            raise RuntimeError("Wait-for-OTP cancelled by user.")
        if proc is not None and proc.poll() is not None:
            tail = _log_tail(log_path) if log_path else "(no log path)"
            raise RuntimeError(
                f"OTP server exited prematurely (code {proc.returncode}). "
                f"{_OTP_EXIT_HINT}\nLast log:\n{tail}"
            )
        try:
            client.get_router_info()
            elapsed = int(time.monotonic() - start)
            feedback.pushInfo(f"OTP router '{client.router}' ready after {elapsed}s.")
            return
        except OtpClientError:
            elapsed = int(time.monotonic() - start)
            feedback.pushInfo(f"…waiting for OTP router ({elapsed}s elapsed)")
            time.sleep(poll_interval_s)
    tail = _log_tail(log_path) if log_path else "(no log path)"
    raise RuntimeError(
        f"OTP router '{client.router}' not ready after {int(timeout_s)}s. Last log:\n{tail}"
    )


def stop_server(proc: subprocess.Popen, feedback=None) -> None:
    if proc.poll() is not None:
        return
    _terminate(proc, feedback)


def _log(feedback, msg: str) -> None:
    if feedback is None:
        return
    try:
        feedback.pushInfo(msg)
    except Exception:  # nosec B110
        pass


def _terminate(proc: subprocess.Popen, feedback=None) -> None:
    """Terminate proc; on Windows finishes with taskkill /T as a tree-kill fallback.

    CREATE_NEW_CONSOLE detaches the child from our process group enough
    that proc.terminate() can leave the conhost window — and any helper
    children Java spawned — alive after java.exe exits. taskkill /F /T
    /PID walks the whole tree, idempotent if terminate() already worked.
    """
    if proc.poll() is not None:
        return
    pid = proc.pid
    _log(feedback, f"Terminating OTP process (pid={pid})…")
    try:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _log(feedback, f"terminate() timed out for pid={pid}, escalating to taskkill…")
    except Exception as e:
        _log(feedback, f"terminate() raised {e!r} for pid={pid}, escalating to taskkill…")

    if proc.poll() is None and sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                timeout=10,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as e:
            _log(feedback, f"taskkill failed for pid={pid}: {e!r}")

    if proc.poll() is None:
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:  # nosec B110
            pass

    if proc.poll() is None:
        _log(feedback, f"WARNING: OTP pid={pid} still alive after full kill attempt — kill it manually.")
    else:
        _log(feedback, f"OTP process pid={pid} stopped.")


def _copy_if_missing(src: Path, dst: Path) -> None:
    if not dst.exists():
        shutil.copy2(src, dst)


# ---------- context manager ----------

class OtpServer:
    """Context manager owning an OTP --server subprocess we spawned ourselves.

    Honors keep_alive only on clean exit; always tears down on exception.
    Does NOT manage a server we merely detected via probe_otp() — that one
    belongs to whoever started it.
    """

    def __init__(
        self,
        java_path: Path,
        jar_path: Path,
        xmx: str,
        work_dir: Path,
        router_id: str,
        port: int,
        pointsets_dir: Path,
        keep_alive: bool,
        feedback,
        show_console: bool = False,
        rt_config_cleanup: bool = False,
    ):
        self.java_path = java_path
        self.jar_path = jar_path
        self.xmx = xmx
        self.work_dir = work_dir
        self.router_id = router_id
        self.port = port
        self.pointsets_dir = pointsets_dir
        self.keep_alive = keep_alive
        self.feedback = feedback
        self.show_console = show_console
        # When True, the per-run GTFS-RT router-config.json is deleted on teardown
        # so a stale RT updater cannot leak into a later fresh static graph build
        # (RunRealtimeAccessibility sets this; static analysis leaves it False).
        self.rt_config_cleanup = rt_config_cleanup
        self.proc: Optional[subprocess.Popen] = None
        self.log_path: Optional[Path] = None

    def __enter__(self) -> "OtpServer":
        self.proc, self.log_path = start_server(
            self.java_path,
            self.jar_path,
            self.xmx,
            self.work_dir,
            self.router_id,
            self.port,
            self.pointsets_dir,
            self.feedback,
            show_console=self.show_console,
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.proc is None:
            return
        server_left_running = exc_type is None and self.keep_alive
        try:
            if not server_left_running:
                self.feedback.pushInfo("Stopping OTP server…")
                stop_server(self.proc, feedback=self.feedback)
            else:
                self.feedback.pushInfo(
                    f"Leaving OTP server running on port {self.port} (KEEP_SERVER_ALIVE=True)."
                )
        finally:
            # Always remove the per-run RT config, even when the server is left
            # running. OTP reads router-config.json only at boot, so removal cannot
            # affect the live server — but a leftover RT updater on disk WOULD make a
            # later static run on the same router_id silently poll the live feed,
            # breaking the Analysis/Realtime separation (CLAUDE.md). Leak-prevention
            # outranks keeping the file around for inspection (its contents were
            # already logged at write time).
            if self.rt_config_cleanup:
                self._remove_rt_config()

    def _remove_rt_config(self) -> None:
        """Delete the per-run GTFS-RT router-config.json from the router dir.

        OTP reads router-config.json only at server start, so removing it after
        teardown is safe and prevents a stale RT updater from being reused by a
        later fresh static graph build (ensure_router_config keeps an existing
        file untouched).
        """
        cfg = self.work_dir / "graphs" / self.router_id / "router-config.json"
        try:
            cfg.unlink()
            _log(self.feedback, f"Removed GTFS-RT router-config.json: {cfg}")
        except FileNotFoundError:
            pass
        except OSError as e:
            _log(self.feedback, f"Could not remove {cfg}: {e!r}")


__all__ = [
    "OtpServer",
    "OtpClient",
    "OtpClientError",
    "build_graph",
    "check_java_version",
    "compute_router_id",
    "discover_gtfs_files",
    "ensure_pointsets_dir",
    "ensure_router_config",
    "ensure_router_dir",
    "graph_build_complete",
    "graph_obj_exists",
    "port_is_listening",
    "port_is_free",
    "probe_otp",
    "start_server",
    "stop_server",
    "wait_until_ready",
    "write_meta",
]
