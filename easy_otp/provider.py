"""Processing provider for easy-OTP."""

from qgis.core import QgsProcessingProvider

from .algorithms.count_from_surfaces import CountFromExistingSurfaces
from .algorithms.run_temporal_accessibility import RunTemporalAccessibility
from .algorithms.test_otp_server import TestOtpServer


class EasyOtpProvider(QgsProcessingProvider):
    def id(self) -> str:  # noqa: A003 — Qt API name
        return "easyotp"

    def name(self) -> str:
        return self.tr("easy-OTP")

    def longName(self) -> str:  # noqa: N802 — Qt API name
        return self.tr("easy-OTP — temporal accessibility via OpenTripPlanner")

    def loadAlgorithms(self) -> None:  # noqa: N802 — Qt API name
        self.addAlgorithm(RunTemporalAccessibility())
        self.addAlgorithm(CountFromExistingSurfaces())
        self.addAlgorithm(TestOtpServer())
