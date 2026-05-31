"""DownloadJre: auto-download Eclipse Temurin 8 JRE from Adoptium (R3)."""

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
        return self.tr("Download Java Runtime Environment")

    def group(self) -> str:
        return self.tr("Setup")

    def groupId(self) -> str:  # noqa: N802
        return "setup"

    def shortHelpString(self) -> str:  # noqa: N802
        return self.tr(
            "Downloads a portable Eclipse Temurin 8 JRE (x64) from the public "
            "Adoptium API, verifies its SHA-256 checksum, unpacks it into the "
            "chosen folder, and optionally saves the Java binary path to "
            "QSettings so other easy-OTP algorithms pick it up automatically.\n\n"
            "Supported platforms: Windows x64, Linux x64, macOS x64 (Intel). "
            "Apple Silicon / ARM Linux are not supported in v0.2 — download a "
            "native build manually from https://adoptium.net/temurin/releases/?version=8\n\n"
            "Running the algorithm a second time on the same folder detects the "
            "existing JRE and exits in seconds (cache hit)."
        )

    def createInstance(self):  # noqa: N802
        return DownloadJre()

    def initAlgorithm(self, config=None):  # noqa: N802
        self.addParameter(
            QgsProcessingParameterFile(
                self.JRE_DEST_DIR,
                self.tr("Destination folder for JRE"),
                behavior=QgsProcessingParameterFile.Folder,
            )
        )

        platform_param = QgsProcessingParameterEnum(
            self.PLATFORM,
            self.tr("Platform override"),
            options=self._PLATFORM_OPTIONS,
            defaultValue=0,
        )
        platform_param.setFlags(
            platform_param.flags() | QgsProcessingParameterDefinition.FlagAdvanced
        )
        self.addParameter(platform_param)

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.SET_AS_DEFAULT,
                self.tr("Save Java binary path to QSettings (easy_otp/java_path)"),
                defaultValue=True,
            )
        )

        self.addOutput(QgsProcessingOutputString(self.JAVA_PATH, self.tr("Java binary path")))
        self.addOutput(QgsProcessingOutputString(self.JAVA_VERSION, self.tr("Java version")))

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        dest = Path(self.parameterAsFile(parameters, self.JRE_DEST_DIR, context))
        platform_idx = self.parameterAsEnum(parameters, self.PLATFORM, context)
        set_as_default = self.parameterAsBool(parameters, self.SET_AS_DEFAULT, context)

        # Step 0 — Pre-flight
        self._check_arch()
        self._check_writable(dest)
        self._check_disk(dest)

        # Step 2 — Platform detection (before cache hit so _find_binary gets
        # the correct binary name even when PLATFORM override is active)
        os_name = self._resolve_os(platform_idx)
        feedback.pushInfo(self.tr(f"Target platform: {os_name} x64"))

        # Step 1 — Cache hit
        cached = self._find_binary(dest, os_name)
        if cached:
            is_ok, version, err = check_java_version(cached)
            if is_ok:
                feedback.pushInfo(self.tr(
                    f"Existing Java 8 found at {cached}, skipping download."
                ))
                if set_as_default:
                    self._save_qsettings(cached, feedback)
                return {self.JAVA_PATH: str(cached), self.JAVA_VERSION: version}
            # Found a JRE but wrong version — remove it before downloading replacement
            self._remove_old_jre(cached, dest, feedback)

        # Step 3 — Adoptium API
        feedback.pushInfo(self.tr("Querying Adoptium API for latest Temurin 8 JRE …"))
        pkg_link, checksum, pkg_name, release_name = self._query_adoptium(os_name)
        feedback.pushInfo(self.tr(f"Found release: {release_name}  ({pkg_name})"))

        # Step 4 — Download
        archive = dest / pkg_name
        tmp = dest / (pkg_name + ".tmp")
        feedback.pushInfo(self.tr(f"Downloading {pkg_link} …"))
        self._download(pkg_link, tmp, archive, feedback)
        if feedback.isCanceled():
            return {}

        # Step 5 — SHA256
        feedback.pushInfo(self.tr("Verifying SHA-256 …"))
        self._verify_sha256(archive, checksum)

        # Step 6 — Extract
        feedback.setProgress(85)
        feedback.pushInfo(self.tr("Extracting archive …"))
        self._extract(archive, dest, os_name)
        try:
            os.remove(archive)
        except OSError as exc:
            feedback.pushWarning(self.tr(
                f"Could not delete downloaded archive '{archive}': {exc}. "
                "You may remove it manually."
            ))

        # Step 7 — Find binary
        feedback.setProgress(90)
        binary = self._find_binary(dest, os_name)
        if binary is None:
            raise QgsProcessingException(self.tr(
                f"Cannot find 'bin/java[.exe]' inside the unpacked archive at '{dest}'. "
                "Archive structure may have changed — please report this at "
                "https://github.com/GISBoost/easy-OTP/issues"
            ))
        if os_name != "windows":
            os.chmod(binary, 0o755)

        # Step 8 — Validate version
        feedback.setProgress(95)
        is_ok, version, err_msg = check_java_version(binary)
        if not is_ok:
            raise QgsProcessingException(self.tr(
                f"Unpacked JRE reports version '{version}', expected '1.8.x'. "
                "Adoptium API may have returned the wrong asset — please open an issue."
            ))

        # Step 9 — Save and return
        feedback.setProgress(100)
        feedback.pushInfo(self.tr(f"Java 8 OK: version {version}  ({binary})"))
        if set_as_default:
            self._save_qsettings(binary, feedback)

        return {self.JAVA_PATH: str(binary), self.JAVA_VERSION: version}

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
            with urllib_request.urlopen(req, timeout=30) as resp:
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
        feedback,
    ) -> None:
        req = urllib_request.Request(url, headers={"User-Agent": _USER_AGENT})
        try:
            with urllib_request.urlopen(req, timeout=60) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                downloaded = 0
                with open(tmp, "wb") as fh:
                    while True:
                        if feedback.isCanceled():
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
                            feedback.setProgress(int(downloaded / total * 80))
        except URLError as exc:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise QgsProcessingException(self.tr(
                f"Download failed ({url}): {exc}"
            )) from exc

        # Clean up .tmp if cancel arrived after the last chunk was read
        if feedback.isCanceled():
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

    def _extract(self, archive: Path, dest: Path, os_name: str) -> None:
        if os_name == "windows":
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(dest)
        else:
            with tarfile.open(archive) as tf:
                # filter="data" available from Python 3.12; safe for data archives
                if sys.version_info >= (3, 12):
                    tf.extractall(dest, filter="data")
                else:
                    tf.extractall(dest)  # noqa: S202

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
