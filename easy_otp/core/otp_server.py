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
    "OTP_PORT), or corrupt/wrong-version jar (re-download "
    "otp-1.5.0-shaded.jar from Maven Central)."
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
) -> None:
    """Ensure router-config.json (and build-config.json if user-supplied) exist.

    OTP reads router-config at server start; without it, analyst surface
    routing degenerates (SPT does not expand). Logic:

    - If ``source_dir/router-config.json`` exists → copy it (user override).
    - Else, if router_dir has no router-config.json yet → write the embedded
      default.
    - Else, leave the existing one untouched (user may have manually
      customised it inside the router dir).

    build-config.json is only copied from source_dir if present — never
    auto-generated, since OTP's defaults produce a working graph and a wrong
    embedded build-config could break things at build time.
    """
    router_cfg = router_dir / "router-config.json"
    build_cfg = router_dir / "build-config.json"

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

    When show_console=True on Windows, the Java process is spawned in its own
    console window with live stdout/stderr visible (no logfile in that mode).
    Useful for diagnosing routing problems where OTP only reports the failure
    via its own logging.
    """
    log_path = work_dir / f"otp_server_{router_id}.log"
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
    if show_console and sys.platform == "win32":
        feedback.pushInfo("Server output will appear in a separate console window (SHOW_OTP_CONSOLE=True).")
        proc = subprocess.Popen(
            cmd,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
    else:
        feedback.pushInfo(f"Server log: {log_path}")
        log_fh = open(log_path, "wb")
        proc = subprocess.Popen(
            cmd,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            **_popen_kwargs(),
        )
        # log_fh stays open; closed implicitly by OS when proc exits / we kill it.
    return proc, log_path


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
    except Exception:
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
        except Exception:
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
        if exc_type is not None or not self.keep_alive:
            self.feedback.pushInfo("Stopping OTP server…")
            stop_server(self.proc, feedback=self.feedback)
        else:
            self.feedback.pushInfo(
                f"Leaving OTP server running on port {self.port} (KEEP_SERVER_ALIVE=True)."
            )


__all__ = [
    "OtpServer",
    "OtpClient",
    "OtpClientError",
    "build_graph",
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
