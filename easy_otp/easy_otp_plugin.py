"""Main plugin class: registers and unregisters the easy-OTP Processing provider."""

import os

from qgis.core import QgsApplication
from qgis.PyQt.QtCore import QCoreApplication, QSettings, QTranslator
from qgis.PyQt.QtWidgets import QMessageBox

from .core.dependencies import (
    ensure_openpyxl,
    install_openpyxl,
)
from .provider import EasyOtpProvider


class EasyOtpPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self._translator = None

        locale = QSettings().value("locale/userLocale", "en_US")[:2]
        qm = os.path.join(os.path.dirname(__file__), "i18n", f"easy_otp_{locale}.qm")
        if os.path.exists(qm):
            self._translator = QTranslator()
            self._translator.load(qm)
            QCoreApplication.installTranslator(self._translator)

    def tr(self, message: str) -> str:  # noqa: N802 — QGIS i18n convention
        return QCoreApplication.translate(self.__class__.__name__, message)

    def initGui(self):  # noqa: N802 — required QGIS plugin hook
        if not ensure_openpyxl():
            self._prompt_install_openpyxl()

        self.provider = EasyOtpProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def _prompt_install_openpyxl(self) -> None:
        reply = QMessageBox.question(
            None,
            self.tr("easy-OTP: missing dependency"),
            self.tr(
                "The openpyxl library is not installed in your QGIS Python "
                "environment. It is required by the Prepare Student Layer "
                "(R1a) algorithm to read GUS NSP 2021 Excel files.\n\n"
                "Install it now? (downloads wheel via urllib, falls back to pip; "
                "requires internet access)\n\n"
                "Choosing 'No' is safe — all other algorithms work "
                "without openpyxl, but R1a will raise an error when run."
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply == QMessageBox.Yes:
            success, msg = install_openpyxl()
            if success:
                QMessageBox.information(
                    None,
                    self.tr("easy-OTP"),
                    self.tr("openpyxl installed successfully."),
                )
            else:
                QMessageBox.warning(
                    None,
                    self.tr("easy-OTP: installation failed"),
                    self.tr(
                        "Could not install openpyxl automatically:\n\n"
                        "%1"
                        "\n\nYou can install it manually by running the "
                        "following command in the OSGeo4W Shell (Windows) "
                        "or a terminal with QGIS's Python active:\n\n"
                        "    python -m pip install openpyxl\n\n"
                        "Then restart QGIS."
                    ).replace("%1", msg),
                )

    def unload(self):
        if self._translator is not None:
            QCoreApplication.removeTranslator(self._translator)
            self._translator = None
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None
