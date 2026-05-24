"""easy-OTP — QGIS plugin for temporal accessibility analysis via OpenTripPlanner."""


def classFactory(iface):  # noqa: N802 — required QGIS entry point name
    from .easy_otp_plugin import EasyOtpPlugin
    return EasyOtpPlugin(iface)
