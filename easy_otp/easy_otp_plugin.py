"""Main plugin class: registers and unregisters the easy-OTP Processing provider."""

from qgis.core import QgsApplication

from .provider import EasyOtpProvider


class EasyOtpPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None

    def initGui(self):  # noqa: N802 — required QGIS plugin hook
        self.provider = EasyOtpProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def unload(self):
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None
