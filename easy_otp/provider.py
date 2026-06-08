"""Processing provider for easy-OTP."""

from qgis.core import QgsProcessingProvider

from .algorithms.compare_temporal_accessibility import CompareTemporalAccessibility
from .algorithms.count_from_surfaces import CountFromExistingSurfaces
from .algorithms.download_jre import DownloadJre
from .algorithms.download_transit_data import DownloadTransitData
from .algorithms.generate_hex_grid import GenerateHexGrid
from .algorithms.population_overlay import PopulationOverlay
from .algorithms.prepare_student_layer import PrepareStudentLayer
from .algorithms.run_temporal_accessibility import RunTemporalAccessibility
from .algorithms.test_otp_server import TestOtpServer


class EasyOtpProvider(QgsProcessingProvider):
    def id(self) -> str:  # noqa: A003 — Qt API name
        return "easyotp"

    def name(self) -> str:
        return self.tr("Easy-OTP")

    def longName(self) -> str:  # noqa: N802 — Qt API name
        return self.tr("Easy-OTP — temporal accessibility via OpenTripPlanner")

    def loadAlgorithms(self) -> None:  # noqa: N802 — Qt API name
        self.addAlgorithm(RunTemporalAccessibility())
        self.addAlgorithm(CompareTemporalAccessibility())
        self.addAlgorithm(CountFromExistingSurfaces())
        self.addAlgorithm(GenerateHexGrid())
        self.addAlgorithm(PopulationOverlay())
        self.addAlgorithm(PrepareStudentLayer())
        self.addAlgorithm(TestOtpServer())
        self.addAlgorithm(DownloadJre())
        self.addAlgorithm(DownloadTransitData())
