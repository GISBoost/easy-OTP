"""Processing provider for easy-OTP."""

from qgis.core import QgsProcessingProvider

from .algorithms.compare_temporal_accessibility import CompareTemporalAccessibility
from .algorithms.count_from_surfaces import CountFromExistingSurfaces
from .algorithms.download_jre import DownloadJre
from .algorithms.download_transit_data import DownloadTransitData
from .algorithms.generate_hex_grid import GenerateHexGrid
from .algorithms.generate_isochrones import GenerateIsochrones
from .algorithms.generate_isochrones_over_time import GenerateIsochronesOverTime
from .algorithms.population_overlay import PopulationOverlay
from .algorithms.prepare_student_layer import PrepareStudentLayer
from .algorithms.build_realized_gtfs import BuildRealizedGtfs
from .algorithms.record_gtfsrt import RecordGtfsRt
from .algorithms.route_via_points import RouteViaPoints
from .algorithms.run_origin_destination_times import RunOriginDestinationTimes
from .algorithms.run_realtime_accessibility import RunRealtimeAccessibility
from .algorithms.run_travel_time_matrix import RunTravelTimeMatrix
from .algorithms.run_service_coverage import RunServiceCoverage
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
        self.addAlgorithm(RunRealtimeAccessibility())
        self.addAlgorithm(RecordGtfsRt())
        self.addAlgorithm(BuildRealizedGtfs())
        self.addAlgorithm(CompareTemporalAccessibility())
        self.addAlgorithm(CountFromExistingSurfaces())
        self.addAlgorithm(GenerateHexGrid())
        self.addAlgorithm(GenerateIsochrones())
        self.addAlgorithm(GenerateIsochronesOverTime())
        self.addAlgorithm(RouteViaPoints())
        self.addAlgorithm(RunOriginDestinationTimes())
        self.addAlgorithm(RunTravelTimeMatrix())
        self.addAlgorithm(RunServiceCoverage())
        self.addAlgorithm(PopulationOverlay())
        self.addAlgorithm(PrepareStudentLayer())
        self.addAlgorithm(TestOtpServer())
        self.addAlgorithm(DownloadJre())
        self.addAlgorithm(DownloadTransitData())
