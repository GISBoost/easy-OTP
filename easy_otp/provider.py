"""Processing provider for easy-OTP."""

from qgis.core import QgsProcessingProvider

from .algorithms.run_temporal_accessibility import RunTemporalAccessibility


class EasyOtpProvider(QgsProcessingProvider):
    def id(self) -> str:  # noqa: A003 — Qt API name
        return "easyotp"

    def name(self) -> str:
        return self.tr("easy-OTP")

    def longName(self) -> str:  # noqa: N802 — Qt API name
        return self.tr("easy-OTP — temporal accessibility via OpenTripPlanner")

    def loadAlgorithms(self) -> None:  # noqa: N802 — Qt API name
        self.addAlgorithm(RunTemporalAccessibility())
