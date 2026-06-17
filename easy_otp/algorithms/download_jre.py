"""DownloadJre: auto-download Eclipse Temurin 8 JRE and OTP 1.5.0 jar (R3)."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import sys
import tarfile
import zipfile
from pathlib import Path
from urllib import request as urllib_request
from urllib.error import URLError

from qgis.PyQt.QtCore import QCoreApplication, QSettings
from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingMultiStepFeedback,
    QgsProcessingOutputString,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFile,
)

from ..core.otp_server import check_java_version

_ADOPTIUM_URL = (
    "https://api.adoptium.net/v3/assets/latest/8/hotspot"
    "?architecture=x64&image_type=jre&os={os_name}&vendor=eclipse"
)
_OTP_JAR_URL = (
    "https://github.com/opentripplanner/OpenTripPlanner/releases/"
    "download/v1.5.0/otp-1.5.0-shaded.jar"
)
_OTP_JAR_FILENAME = "otp-1.5.0-shaded.jar"
_OTP_JAR_MIN_BYTES = 50 * 1024 * 1024   # 50 MB sanity floor
_OTP_JAR_MAX_BYTES = 80 * 1024 * 1024   # 80 MB sanity ceiling
_OTP_JAR_MIN_FREE_MB = 90
_USER_AGENT = "easy-OTP/0.2"
_CHUNK_SIZE = 64 * 1024        # 64 KB download blocks
_HASH_CHUNK = 1 * 1024 * 1024  # 1 MB SHA256 blocks
_MIN_FREE_MB = 260


class DownloadJre(QgsProcessingAlgorithm):
    JRE_DEST_DIR = "JRE_DEST_DIR"
    PLATFORM = "PLATFORM"
    SET_AS_DEFAULT = "SET_AS_DEFAULT"

    JAVA_PATH = "JAVA_PATH"
    JAVA_VERSION = "JAVA_VERSION"
    OTP_JAR_PATH = "OTP_JAR_PATH"

    _PLATFORM_OPTIONS = [
        "Auto-detect (current system)",
        "Windows x64",
        "Linux x64",
        "macOS x64 (Intel)",
    ]
    # Indexed by platform_idx - 1 (index 0 is auto-detect, handled separately)
    _OS_NAMES = ["windows", "linux", "mac"]

    def tr(self, string: str) -> str:
        return QCoreApplication.translate("Processing", string)

    def name(self) -> str:
        return "downloadjre"

    def displayName(self) -> str:  # noqa: N802
        return self.tr("Download Java 8 JRE and OpenTripPlanner Jar")

    def group(self) -> str:
        return self.tr("1 · Setup")

    def groupId(self) -> str:  # noqa: N802
        return "setup"

    def shortHelpString(self) -> str:  # noqa: N802
        return self.tr(
            "Downloads a portable Eclipse Temurin 8 JRE (x64) from the public "
            "Adoptium API AND otp-1.5.0-shaded.jar from the GitHub Releases "
            "page, verifies both files, and saves their paths to QSettings so "
            "other easy-OTP algorithms pick them up automatically.\n\n"
            "Supported platforms: Windows x64, Linux x64, macOS x64 (Intel). "
            "Apple Silicon / ARM Linux are not supported in v0.2 — download a "
            "native build manually from https://adoptium.net/temurin/releases/?version=8\n\n"
            "Running the algorithm a second time on the same folder detects "
            "existing files and exits in seconds (cache hit for each independently)."
        )

    def createInstance(self):  # noqa: N802
        return DownloadJre()

    def initAlgorithm(self, config=None):  # noqa: N802
        self.addParameter(
            QgsProcessingParameterFile(
                self.JRE_DEST_DIR,
                self.tr("Destination folder for JRE and OTP jar"),
                behavior=QgsProcessingParameterFile.Folder,
            )
        )

        platform_param = QgsProcessingParameterEnum(
            self.PLATFORM,
            self.tr("Platform override"),
            options=[self.tr(s) for s in self._PLATFORM_OPTIONS],
            defaultValue=0,
        )
        platform_param.setFlags(
            platform_param.flags() | QgsProcessingParameterDefinition.FlagAdvanced
        )
        self.addParameter(platform_param)

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.SET_AS_DEFAULT,
                self.tr(
                    "Save paths to QSettings "
                    "(easy_otp/java_path and easy_otp/otp_jar_path)"
                ),
                defaultValue=True,
            )
        )

        self.addOutput(QgsProcessingOutputString(self.JAVA_PATH, self.tr("Java binary path")))
        self.addOutput(QgsProcessingOutputString(self.JAVA_VERSION, self.tr("Java version")))
        self.addOutput(QgsProcessingOutputString(self.OTP_JAR_PATH, self.tr("OTP jar path")))

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        dest = Path(self.parameterAsFile(parameters, self.JRE_DEST_DIR, context))
        platform_idx = self.parameterAsEnum(parameters, self.PLATFORM, context)
        set_as_default = self.parameterAsBool(parameters, self.SET_AS_DEFAULT, context)

        # 10 virtual steps: 0-6 = JRE phase (70%), 7-9 = OTP jar phase (30%)
        multi = QgsProcessingMultiStepFeedback(10, feedback)

        # ── Step 0: pre-flight ────────────────────────────────────────────────
        multi.setCurrentStep(0)
        self._check_arch()
        self._check_writable(dest)

        # ── Step 1: platform detection + JRE cache hit check ─────────────────
        multi.setCurrentStep(1)
        os_name = self._resolve_os(platform_idx)
        multi.pushInfo(self.tr(f"Target platform: {os_name} x64"))

        binary: "Path | None" = None
        version: str = ""
        jre_cached = False

        cached = self._find_binary(dest, os_name)
        if cached:
            is_ok, ver, _err = check_java_version(cached)
            if is_ok:
                multi.pushInfo(self.tr(
                    f"Existing Java 8 found at {cached}, skipping download."
                ))
                binary = cached
                version = ver
                jre_cached = True
            else:
                self._remove_old_jre(cached, dest, multi)

        # ── Steps 2-6: JRE download (skipped on cache hit) ───────────────────
        if not jre_cached:
            self._check_disk(dest)

            # Step 2: Adoptium API query
            multi.setCurrentStep(2)
            multi.pushInfo(self.tr("Querying Adoptium API for latest Temurin 8 JRE …"))
            pkg_link, checksum, pkg_name, release_name = self._query_adoptium(os_name)
            multi.pushInfo(self.tr(f"Found release: {release_name}  ({pkg_name})"))

            # Steps 3-5: archive download (3 steps = 30% of total)
            archive = dest / pkg_name
            tmp = dest / (pkg_name + ".tmp")
            multi.pushInfo(self.tr(f"Downloading {pkg_link} …"))
            self._download(pkg_link, tmp, archive, multi, step_start=3, step_count=3)
            if multi.isCanceled():
                return {}

            # Step 6: SHA256 + extract + binary find + version check
            multi.setCurrentStep(6)
            multi.pushInfo(self.tr("Verifying SHA-256 …"))
            self._verify_sha256(archive, checksum)

            multi.pushInfo(self.tr("Extracting archive …"))
            self._extract(archive, dest, os_name)
            try:
                os.remove(archive)
            except OSError as exc:
                multi.pushWarning(self.tr(
                    f"Could not delete downloaded archive '{archive}': {exc}. "
                    "You may remove it manually."
                ))

            binary = self._find_binary(dest, os_name)
            if binary is None:
                raise QgsProcessingException(self.tr(
                    f"Cannot find 'bin/java[.exe]' inside the unpacked archive at '{dest}'. "
                    "Archive structure may have changed — please report this at "
                    "https://github.com/GISBoost/easy-OTP/issues"
                ))
            if os_name != "windows":
                os.chmod(binary, 0o755)  # nosec B103 — executable bit required for JRE binary

            is_ok, version, err_msg = check_java_version(binary)
            if not is_ok:
                raise QgsProcessingException(self.tr(
                    f"Unpacked JRE reports version '{version}', expected '1.8.x'. "
                    "Adoptium API may have returned the wrong asset — please open an issue."
                ))

            multi.pushInfo(self.tr(f"Java 8 OK: version {version}  ({binary})"))

        if set_as_default:
            self._save_qsettings(binary, multi)

        # ── Cancellation check between JRE and OTP jar phases ─────────────────
        if multi.isCanceled():
            multi.pushWarning(self.tr(
                "Cancelled before OTP jar download. "
                "Java path was already saved to QSettings — "
                "run the algorithm again to download the OTP jar."
            ))
            return {}

        # ── Steps 7-9: OTP jar phase ──────────────────────────────────────────
        jar_path = self._download_otp_jar(dest, multi)
        if multi.isCanceled():
            return {}

        if set_as_default and jar_path is not None:
            QSettings().setValue("easy_otp/otp_jar_path", str(jar_path))
            multi.pushInfo(self.tr(
                f"OTP jar path saved to QSettings (easy_otp/otp_jar_path): {jar_path}"
            ))

        multi.setCurrentStep(9)
        multi.setProgress(100)

        return {
            self.JAVA_PATH: str(binary),
            self.JAVA_VERSION: version,
            self.OTP_JAR_PATH: str(jar_path) if jar_path is not None else "",
        }

    # ------------------------------------------------------------------ helpers

    def _check_arch(self) -> None:
        machine = platform.machine().lower()
        if machine in ("arm64", "aarch64"):
            raise QgsProcessingException(self.tr(
                f"Automatic JRE download in v0.2 supports x64 only. "
                f"Detected architecture: {machine}. "
                "Please download Temurin 8 manually from "
                "https://adoptium.net/temurin/releases/?version=8 "
                "(native build for your architecture, or x64 build for use "
                "under Rosetta 2 on macOS)."
            ))

    def _check_writable(self, dest: Path) -> None:
        if not dest.is_dir():
            raise QgsProcessingException(self.tr(
                f"Destination folder '{dest}' does not exist. "
                "Create it first or choose an existing folder."
            ))
        if not os.access(dest, os.W_OK):
            raise QgsProcessingException(self.tr(
                f"Destination folder '{dest}' is not writable. "
                "Check permissions or choose another folder."
            ))

    def _check_disk(self, dest: Path) -> None:
        free_mb = shutil.disk_usage(dest).free / (1024 * 1024)
        if free_mb < _MIN_FREE_MB:
            raise QgsProcessingException(self.tr(
                f"Not enough disk space in '{dest}'. "
                f"Need ~{_MIN_FREE_MB} MB, have {free_mb:.0f} MB."
            ))

    def _resolve_os(self, platform_idx: int) -> str:
        if platform_idx == 0:
            mapping = {"win32": "windows", "linux": "linux", "darwin": "mac"}
            os_name = mapping.get(sys.platform)
            if os_name is None:
                raise QgsProcessingException(self.tr(
                    f"Unsupported platform '{sys.platform}'. "
                    "Use the 'Platform override' parameter to select manually."
                ))
            return os_name
        return self._OS_NAMES[platform_idx - 1]

    def _query_adoptium(self, os_name: str) -> "tuple[str, str, str, str]":
        url = _ADOPTIUM_URL.format(os_name=os_name)
        req = urllib_request.Request(url, headers={"User-Agent": _USER_AGENT})
        try:
            with urllib_request.urlopen(req, timeout=30) as resp:  # nosec B310 — QGIS stdlib only (no requests); HTTPS URL from hardcoded endpoint or trusted API
                data = json.loads(resp.read().decode())
        except URLError as exc:
            raise QgsProcessingException(self.tr(
                f"Cannot reach Adoptium API at https://api.adoptium.net. "
                f"Check your network connection. ({exc})"
            )) from exc

        if not data:
            raise QgsProcessingException(self.tr(
                f"No JRE 8 x64 build available for '{os_name}' on Adoptium. "
                "Supported combinations: see "
                "https://adoptium.net/temurin/releases/?version=8"
            ))

        asset = data[0]
        pkg = asset["binary"]["package"]
        return (
            pkg["link"],
            pkg["checksum"],
            pkg["name"],
            asset.get("release_name", ""),
        )

    def _download(
        self,
        url: str,
        tmp: Path,
        archive: Path,
        multi_feedback,
        step_start: int,
        step_count: int,
    ) -> None:
        req = urllib_request.Request(url, headers={"User-Agent": _USER_AGENT})
        try:
            with urllib_request.urlopen(req, timeout=60) as resp:  # nosec B310 — QGIS stdlib only (no requests); HTTPS URL from hardcoded endpoint or trusted API
                total = int(resp.headers.get("Content-Length") or 0)
                downloaded = 0
                with open(tmp, "wb") as fh:
                    while True:
                        if multi_feedback.isCanceled():
                            try:
                                os.remove(tmp)
                            except OSError:
                                pass
                            return
                        chunk = resp.read(_CHUNK_SIZE)
                        if not chunk:
                            break
                        fh.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            frac = downloaded / total
                            cur_step = step_start + int(frac * step_count)
                            cur_step = min(cur_step, step_start + step_count - 1)
                            multi_feedback.setCurrentStep(cur_step)
                            within = (frac * step_count) % 1.0
                            multi_feedback.setProgress(int(within * 100))
        except URLError as exc:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise QgsProcessingException(self.tr(
                f"Download failed ({url}): {exc}"
            )) from exc

        # Clean up .tmp if cancel arrived after the last chunk was read
        if multi_feedback.isCanceled():
            try:
                os.remove(tmp)
            except OSError:
                pass
            return

        os.rename(tmp, archive)

    def _verify_sha256(self, archive: Path, expected: str) -> None:
        h = hashlib.sha256()
        with open(archive, "rb") as fh:
            for block in iter(lambda: fh.read(_HASH_CHUNK), b""):
                h.update(block)
        got = h.hexdigest()
        if got.lower() != expected.lower():
            os.remove(archive)
            raise QgsProcessingException(self.tr(
                "Downloaded archive checksum does not match Adoptium API. "
                "Likely network corruption — please retry. "
                f"Expected {expected}, got {got}."
            ))

    def _safe_zipextract(self, zf: "zipfile.ZipFile", dest: Path) -> None:
        """Extract zip safely, skipping members with path traversal (zip slip)."""
        dest_root = dest.resolve()
        prefix = str(dest_root) + os.sep
        for member in zf.infolist():
            target = (dest_root / member.filename).resolve()
            if str(target) != str(dest_root) and not str(target).startswith(prefix):
                continue  # skip zip-slip attempt
            zf.extract(member, dest)

    def _extract(self, archive: Path, dest: Path, os_name: str) -> None:
        if os_name == "windows":
            with zipfile.ZipFile(archive) as zf:
                self._safe_zipextract(zf, dest)
        else:
            with tarfile.open(archive) as tf:
                # filter="data" available from Python 3.12; safe for data archives
                if sys.version_info >= (3, 12):
                    tf.extractall(dest, filter="data")
                else:
                    tf.extractall(dest)  # nosec B202 — SHA-256 verified before extraction

    def _find_binary(self, dest: Path, os_name: str) -> "Path | None":
        binary_name = "java.exe" if os_name == "windows" else "java"
        for root, dirs, files in os.walk(dest):
            depth = len(Path(root).relative_to(dest).parts)
            if depth >= 3:
                dirs.clear()
                continue
            if Path(root).name == "bin" and binary_name in files:
                return Path(root) / binary_name
        return None

    def _remove_old_jre(self, cached: Path, dest: Path, feedback) -> None:
        """Remove a non-Java-8 JRE found in dest before downloading a replacement."""
        old_root = cached.parent.parent  # bin/java(.exe) -> bin -> jre-root
        if old_root == dest:
            # JRE was extracted flat into dest with no top-level subfolder —
            # cannot safely remove dest itself; tell user to clean up manually.
            raise QgsProcessingException(self.tr(
                f"Found non-Java-8 JRE directly inside '{dest}' (no top-level "
                "subfolder). Please manually remove the existing JRE contents "
                "and retry."
            ))
        feedback.pushWarning(self.tr(
            f"Found existing JRE at '{old_root}' but it is not Java 8. "
            "Removing it before downloading a replacement."
        ))
        try:
            shutil.rmtree(old_root)
        except OSError as exc:
            raise QgsProcessingException(self.tr(
                f"Could not remove old JRE at '{old_root}': {exc}. "
                "Please delete the folder manually and retry."
            )) from exc

    def _save_qsettings(self, binary: Path, feedback) -> None:
        QSettings().setValue("easy_otp/java_path", str(binary))
        feedback.pushInfo(self.tr(
            f"Java path saved to QSettings (easy_otp/java_path): {binary}"
        ))

    def _download_otp_jar(self, dest: Path, multi_feedback) -> "Path | None":
        """Download otp-1.5.0-shaded.jar to dest (steps 7–8 of 10)."""
        jar_path = dest / _OTP_JAR_FILENAME
        tmp = dest / (_OTP_JAR_FILENAME + ".tmp")

        # Step 7: cache check + disk check
        multi_feedback.setCurrentStep(7)
        if jar_path.exists() and self._sanity_check_jar(jar_path):
            multi_feedback.pushInfo(self.tr(
                f"Existing OTP jar found at {jar_path}, skipping download."
            ))
            return jar_path

        free_mb = shutil.disk_usage(dest).free / (1024 * 1024)
        if free_mb < _OTP_JAR_MIN_FREE_MB:
            raise QgsProcessingException(self.tr(
                f"Not enough disk space for OTP jar in '{dest}'. "
                f"Need ~{_OTP_JAR_MIN_FREE_MB} MB, have {free_mb:.0f} MB."
            ))

        # Step 8: download + sanity check
        multi_feedback.setCurrentStep(8)
        multi_feedback.pushInfo(self.tr(f"Downloading OTP jar from {_OTP_JAR_URL} …"))
        self._download(_OTP_JAR_URL, tmp, jar_path, multi_feedback, step_start=8, step_count=1)
        if multi_feedback.isCanceled():
            return None

        if not self._sanity_check_jar(jar_path):
            try:
                os.remove(jar_path)
            except OSError:
                pass
            raise QgsProcessingException(self.tr(
                "Downloaded OTP jar failed sanity check: must be a valid ZIP file "
                f"between {_OTP_JAR_MIN_BYTES // (1024 * 1024)} MB and "
                f"{_OTP_JAR_MAX_BYTES // (1024 * 1024)} MB. "
                "The file may be corrupted — please retry."
            ))

        multi_feedback.pushInfo(self.tr(f"OTP jar OK: {jar_path}"))
        return jar_path

    def _sanity_check_jar(self, path: Path) -> bool:
        try:
            size = path.stat().st_size
            if not (_OTP_JAR_MIN_BYTES <= size <= _OTP_JAR_MAX_BYTES):
                return False
            return zipfile.is_zipfile(path)
        except OSError:
            return False
