"""Точка входа: python -m shepot"""

from __future__ import annotations

import sys

import win32api
import win32event
import winerror
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from . import resources
from .app import APP_TITLE, Controller

MUTEX_NAME = "Global\\ShepotSingleInstance"


def main() -> int:
    mutex = win32event.CreateMutex(None, False, MUTEX_NAME)
    if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
        print("Шёпот уже запущен — смотрите значок в трее.")
        return 0

    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setQuitOnLastWindowClosed(False)
    icon = resources.app_icon()
    if icon is not None:
        app.setWindowIcon(QIcon(str(icon)))

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, APP_TITLE, "Системный трей недоступен.")
        return 1

    controller = Controller(app)
    app.aboutToQuit.connect(controller.hotkeys.stop)
    code = app.exec()
    del mutex
    return code


if __name__ == "__main__":
    sys.exit(main())
