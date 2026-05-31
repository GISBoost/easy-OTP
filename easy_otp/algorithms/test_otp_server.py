"""TestOtpServer: static diagnostics for Java + OTP jar + port (PR section 6.1)."""

from __future__ import annotations

from pathlib import Path

from qgis.PyQt.QtCore import QCoreApplication, QSettings
from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterFile,
    QgsProcessingParameterNumber,
)

from ..core.otp_server import check_java_version, port_is_listening, probe_otp


class TestOtpServer(QgsProcessingAlgorithm):
    USE_SAVED_JAVA = "USE_SAVED_JAVA"
    JAVA_PATH = "JAVA_PATH"
    OTP_JAR_PATH = "OTP_JAR_PATH"
    OTP_PORT = "OTP_PORT"

    def tr(self, string: str) -> str:
        return QCoreApplication.translate("Processing", string)

    def name(self) -> str:
        return "testotpserver"

    def displayName(self) -> str:  # noqa: N802
        return self.tr("Test OTP server")

    def group(self) -> str:
        return self.tr("Diagnostics")

    def groupId(self) -> str:  # noqa: N802
        return "diagnostics"

    def createInstance(self):  # noqa: N802
        return TestOtpServer()

    def shortHelpString(self) -> str:  # noqa: N802
        return self.tr(
            "Diagnostic checks for an OpenTripPlanner 1.5.0 setup: verifies "
            "the Java 8 binary, the OTP jar, and the port state (free / held "
            "by a foreign process / already serving OTP). All checks run "
            "independently and report through the algorithm log."
        )

    def initAlgorithm(self, config=None):  # noqa: N802
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.USE_SAVED_JAVA,
                self.tr(
                    "Use Java path saved by 'Download Java Runtime Environment' (QSettings)"
                ),
                defaultValue=True,
            )
        )
        java_param = QgsProcessingParameterFile(
            self.JAVA_PATH,
            self.tr("Java 8 binary"),
            behavior=QgsProcessingParameterFile.File,
        )
        java_param.setFlags(
            java_param.flags() | QgsProcessingParameterDefinition.FlagOptional
        )
        self.addParameter(java_param)
        self.addParameter(
            QgsProcessingParameterFile(
                self.OTP_JAR_PATH,
                self.tr("OpenTripPlanner 1.5.0 jar (otp-1.5.0-shaded.jar)"),
                behavior=QgsProcessingParameterFile.File,
                extension="jar",
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.OTP_PORT,
                self.tr("OTP server port"),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=8801,
                minValue=1,
                maxValue=65535,
            )
        )

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        use_saved = self.parameterAsBool(parameters, self.USE_SAVED_JAVA, context)
        java = Path(self.parameterAsFile(parameters, self.JAVA_PATH, context) or "")
        jar = Path(self.parameterAsFile(parameters, self.OTP_JAR_PATH, context) or "")
        port = self.parameterAsInt(parameters, self.OTP_PORT, context)

        ok = True
        ok &= self._check_java(java, use_saved, feedback)
        ok &= self._check_jar(jar, feedback)
        ok &= self._check_port(port, feedback)

        if ok:
            feedback.pushInfo(self.tr("All checks passed."))
        else:
            feedback.reportError(self.tr(
                "One or more checks failed. Fix the items reported above before "
                "running 'Run temporal accessibility'."
            ))
        return {}

    # --- individual checks ---

    def _check_java(self, java: Path, use_saved: bool, feedback) -> bool:
        if use_saved:
            saved = QSettings().value("easy_otp/java_path", "")
            if not saved:
                feedback.reportError(self.tr(
                    "No Java path saved in QSettings. Run 'Download Java Runtime "
                    "Environment' first, or uncheck 'Use saved Java path' and "
                    "supply the path manually."
                ))
                return False
            java = Path(saved)
            feedback.pushInfo(self.tr(f"Using Java path from QSettings: {java}"))
        elif not java.name:
            feedback.reportError(self.tr(
                "JAVA_PATH is empty. Either check 'Use saved Java path' or "
                "provide the path to the Java 8 binary."
            ))
            return False

        is_ok, version, err_msg = check_java_version(java)
        if is_ok:
            feedback.pushInfo(self.tr(f"Java OK: version {version}"))
            return True
        feedback.reportError(self.tr(err_msg))
        return False

    def _check_jar(self, jar: Path, feedback) -> bool:
        if not jar.name:
            feedback.reportError(self.tr("OTP_JAR_PATH is empty."))
            return False
        if not jar.is_file():
            feedback.reportError(self.tr(
                f"OTP jar not found: {jar}. Download "
                f"otp-1.5.0-shaded.jar from Maven Central "
                f"(org.opentripplanner:otp:1.5.0, classifier 'shaded')."
            ))
            return False
        if jar.suffix.lower() != ".jar":
            feedback.reportError(self.tr(f"File is not a .jar: {jar}"))
            return False
        size_mb = jar.stat().st_size / (1024 * 1024)
        feedback.pushInfo(self.tr(f"OTP jar OK: {jar} ({size_mb:.1f} MB)"))
        return True

    def _check_port(self, port: int, feedback) -> bool:
        info = probe_otp(port)
        if info is not None:
            version = info.get("serverVersion", {})
            ver_str = version.get("version") if isinstance(version, dict) else str(version)
            feedback.pushInfo(self.tr(
                f"Port {port}: OTP already serving here (version {ver_str}). "
                f"RunTemporalAccessibility will reuse this server."
            ))
            return True
        if port_is_listening(port):
            feedback.reportError(self.tr(
                f"Port {port} is held by a non-OTP process (responds to TCP "
                f"but not as an OTP /otp endpoint). Pick a different OTP_PORT "
                f"or stop the conflicting service."
            ))
            return False
        feedback.pushInfo(self.tr(f"Port {port}: free."))
        return True
