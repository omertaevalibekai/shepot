"""Точка входа: python -m shepot"""

from __future__ import annotations

import sys
import threading

import win32api
import win32event
import winerror
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from . import errlog, resources
from .app import APP_TITLE, Controller
from .config import Config
from .i18n import set_language, t

MUTEX_NAME = "Global\\ShepotSingleInstance"


def _log_uncaught() -> None:
    """Необработанные исключения (в том числе в слотах Qt и потоках) — в журнал ошибок."""
    previous = sys.excepthook

    def hook(kind, value, tb):
        errlog.record("uncaught", f"{kind.__name__}: {value}")
        previous(kind, value, tb)

    def thread_hook(args):
        errlog.record("uncaught", f"thread {args.thread.name if args.thread else '?'}: "
                                  f"{args.exc_type.__name__}: {args.exc_value}")

    sys.excepthook = hook
    threading.excepthook = thread_hook


def main() -> int:
    _log_uncaught()
    set_language(Config().get("ui_language"))
    mutex = win32event.CreateMutex(None, False, MUTEX_NAME)
    if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
        print(t("Шёпот уже запущен — смотрите значок в трее."))
        return 0

    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setQuitOnLastWindowClosed(False)
    icon = resources.app_icon()
    if icon is not None:
        app.setWindowIcon(QIcon(str(icon)))

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, t(APP_TITLE), t("Системный трей недоступен."))
        return 1

    controller = Controller(app)
    app.aboutToQuit.connect(controller.hotkeys.stop)
    code = app.exec()
    del mutex
    return code


if __name__ == "__main__":
    sys.exit(main())
