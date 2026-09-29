"""Контроллер приложения: горячая клавиша → запись → текст → вставка."""

from __future__ import annotations

import threading
import winsound

from PySide6.QtCore import QObject, Qt, QRectF, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import audio, inserter, polish, providers, stt
from .config import Config
from .history import History
from .history_ui import HistoryDialog
from .hotkeys import HotkeyManager
from .hud import Hud
from .settings_ui import MODE_LABELS, TONE_LABELS, SettingsDialog

APP_TITLE = "Шёпот"

IDLE_COLOR = QColor(210, 214, 224)
ACTIVE_COLOR = QColor(96, 205, 255)
BUSY_COLOR = QColor(255, 196, 92)


def make_icon(color: QColor) -> QIcon:
    """Рисует иконку микрофона, чтобы не тащить бинарные ресурсы."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    painter.setPen(Qt.NoPen)
    painter.setBrush(color)
    painter.drawRoundedRect(QRectF(24, 9, 16, 29), 8, 8)

    pen = QPen(color, 5)
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawArc(QRectF(16, 18, 32, 30), 180 * 16, 180 * 16)
    painter.drawLine(32, 48, 32, 56)
    painter.end()
    return QIcon(pixmap)


def _beep(frequency: int, duration: int) -> None:
    threading.Thread(
        target=lambda: _safe_beep(frequency, duration), daemon=True
    ).start()


def _safe_beep(frequency: int, duration: int) -> None:
    try:
        winsound.Beep(frequency, duration)
    except RuntimeError:
        pass


class Controller(QObject):
    # сигналы приходят из потоков keyboard/записи — Qt сам ставит их в очередь
    requestStart = Signal()
    requestFinish = Signal()
    requestCancel = Signal()
    levelChanged = Signal(float)
    resultReady = Signal(str, float)
    inserted = Signal(bool)
    failed = Signal(str)

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self.app = app
        self.cfg = Config()
        self.history = History(int(self.cfg.get("history_limit")))
        self.hud = Hud()
        self.recorder = audio.Recorder(
            on_level=self.levelChanged.emit,
            on_auto_stop=self.requestFinish.emit,
        )
        self.hotkeys = HotkeyManager(
            on_press=self.requestStart.emit,
            on_release=self.requestFinish.emit,
            on_cancel=self.requestCancel.emit,
        )
        self._busy = False
        self._settings_dialog: SettingsDialog | None = None
        self._history_dialog: HistoryDialog | None = None

        self.requestStart.connect(self.start_dictation)
        self.requestFinish.connect(self.finish_dictation)
        self.requestCancel.connect(self.cancel_dictation)
        self.levelChanged.connect(self.hud.set_level)
        self.resultReady.connect(self._deliver)
        self.inserted.connect(self._report_insertion)
        self.failed.connect(self._show_error)

        self.tray = QSystemTrayIcon(make_icon(IDLE_COLOR))
        self.tray.activated.connect(self._tray_activated)
        self._build_menu()
        self.tray.show()

        self._apply_hotkeys(announce=False)
        self._warm_up()
        # первый запуск: без ключа диктовать нечем — сразу открываем настройки
        QTimer.singleShot(500, self._prompt_for_key_if_needed)

    def _prompt_for_key_if_needed(self) -> None:
        if providers.missing_key(self.cfg) is None:
            return
        self.tray.showMessage(
            APP_TITLE,
            "Вставьте ключ API, и можно диктовать.",
            QSystemTrayIcon.Information,
            5000,
        )
        self.open_settings(focus_keys=True)

    # ------------------------------------------------------------------ трей
    def _build_menu(self) -> None:
        menu = QMenu()

        self.dictate_action = QAction("Начать диктовку", menu)
        self.dictate_action.triggered.connect(self.toggle_dictation)
        menu.addAction(self.dictate_action)
        menu.addSeparator()

        mode_menu = menu.addMenu("Режим")
        self._mode_group = QActionGroup(menu)
        self._mode_group.setExclusive(True)
        for value, label in MODE_LABELS:
            action = QAction(label.split(" — ")[0], menu, checkable=True)
            action.setData(value)
            action.setChecked(self.cfg.get("mode") == value)
            action.triggered.connect(lambda _c, v=value: self._set("mode", v))
            self._mode_group.addAction(action)
            mode_menu.addAction(action)

        tone_menu = menu.addMenu("Тон")
        self._tone_group = QActionGroup(menu)
        self._tone_group.setExclusive(True)
        for value, label in TONE_LABELS:
            action = QAction(label, menu, checkable=True)
            action.setData(value)
            action.setChecked(self.cfg.get("tone") == value)
            action.triggered.connect(lambda _c, v=value: self._set("tone", v))
            self._tone_group.addAction(action)
            tone_menu.addAction(action)

        menu.addSeparator()
        history_action = QAction("История…", menu)
        history_action.triggered.connect(self.open_history)
        menu.addAction(history_action)

        settings_action = QAction("Настройки…", menu)
        settings_action.triggered.connect(lambda: self.open_settings())
        menu.addAction(settings_action)

        menu.addSeparator()
        quit_action = QAction("Выход", menu)
        quit_action.triggered.connect(self.quit)
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)
        self._menu = menu
        self._update_tooltip()

    def _update_tooltip(self) -> None:
        mode = dict(MODE_LABELS).get(self.cfg.get("mode"), "")
        self.tray.setToolTip(
            f"{APP_TITLE}\n{self.cfg.get('hotkey').upper()} — диктовка\n{mode}"
        )

    def _tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.Trigger:
            self.toggle_dictation()

    def _set(self, key: str, value) -> None:
        self.cfg.set(key, value)
        self.cfg.save()
        self._update_tooltip()

    # ----------------------------------------------------------- горячие клавиши
    def _apply_hotkeys(self, announce: bool = True) -> bool:
        try:
            self.hotkeys.apply(
                self.cfg.get("hotkey"),
                self.cfg.get("hotkey_mode"),
                self.cfg.get("cancel_key"),
            )
        except Exception as exc:
            if announce:
                QMessageBox.warning(
                    None, APP_TITLE, f"Не удалось назначить горячую клавишу: {exc}"
                )
            return False
        self._update_tooltip()
        return True

    # ------------------------------------------------------------------ поток
    def toggle_dictation(self) -> None:
        if self.recorder.is_recording:
            self.finish_dictation()
        else:
            self.start_dictation()

    def start_dictation(self) -> None:
        if self._busy or self.recorder.is_recording:
            return
        if providers.missing_key(self.cfg) is not None:
            self._show_error("Не указан ключ API — откройте настройки")
            self._prompt_for_key_if_needed()
            return
        started = self.recorder.start(
            device_index=self.cfg.get("input_device"),
            max_seconds=float(self.cfg.get("max_seconds")),
        )
        if not started:
            return
        self.tray.setIcon(make_icon(ACTIVE_COLOR))
        if self.cfg.get("play_sounds"):
            _beep(880, 55)
        if self.cfg.get("hud"):
            self.hud.show_state("listening")

    def finish_dictation(self) -> None:
        if not self.recorder.is_recording:
            return
        wav = self.recorder.stop()
        self.hotkeys.notify_stopped()
        self.tray.setIcon(make_icon(BUSY_COLOR))
        if self.cfg.get("play_sounds"):
            _beep(660, 55)

        error = self.recorder.error
        if error:
            self._show_error(f"Микрофон недоступен: {error}")
            return

        seconds = _wav_seconds(wav)
        if seconds < float(self.cfg.get("min_seconds")):
            self.tray.setIcon(make_icon(IDLE_COLOR))
            self.hud.hide_now()
            return

        self._busy = True
        if self.cfg.get("hud"):
            self.hud.show_state("processing")
        threading.Thread(
            target=self._process, args=(wav, seconds), daemon=True,
            name="shepot-pipeline",
        ).start()

    def cancel_dictation(self) -> None:
        if not self.recorder.is_recording:
            return
        self.recorder.cancel()
        self.hotkeys.notify_stopped()
        self.tray.setIcon(make_icon(IDLE_COLOR))
        self.hud.hide_now()

    def _process(self, wav: bytes, seconds: float) -> None:
        snapshot = _Snapshot(self.cfg.as_dict())
        try:
            transcript = stt.transcribe(wav, snapshot)
            text = polish.polish(transcript, snapshot)
        except (stt.SttError, polish.PolishError) as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:                      # не роняем трей из-за сети
            self.failed.emit(f"Непредвиденная ошибка: {exc}")
            return
        self.resultReady.emit(text, seconds)

    # ------------------------------------------------------------- результат
    def _deliver(self, text: str, seconds: float) -> None:
        self._busy = False
        self.tray.setIcon(make_icon(IDLE_COLOR))

        if not text.strip():
            if self.cfg.get("hud"):
                self.hud.show_state("done", "Ничего не распознано", hide_after=1.6)
            return

        self.history.add(text, self.cfg.get("mode"), seconds)

        # вставка спит между нажатиями — уводим её с потока интерфейса,
        # иначе индикатор замирает на время доставки
        method = self.cfg.get("insert_method")
        restore = bool(self.cfg.get("restore_clipboard"))
        delay = float(self.cfg.get("restore_delay"))

        def deliver() -> None:
            try:
                ok = inserter.insert(text, method=method,
                                     restore_clipboard=restore, restore_delay=delay)
            except Exception:
                ok = False
            self.inserted.emit(ok)

        threading.Thread(target=deliver, daemon=True, name="shepot-insert").start()

    def _report_insertion(self, delivered: bool) -> None:
        if self.cfg.get("hud"):
            message = "Вставлено" if delivered else "Скопировано в буфер"
            self.hud.show_state("done", message, hide_after=1.2)

    def _show_error(self, message: str) -> None:
        self._busy = False
        self.tray.setIcon(make_icon(IDLE_COLOR))
        if self.cfg.get("play_sounds"):
            _beep(320, 160)
        if self.cfg.get("hud"):
            self.hud.show_state("error", message, hide_after=4.0)
        else:
            self.tray.showMessage(APP_TITLE, message, QSystemTrayIcon.Warning, 4000)

    # ----------------------------------------------------------------- окна
    def open_settings(self, focus_keys: bool = False) -> None:
        if self._settings_dialog is not None and self._settings_dialog.isVisible():
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
            return
        self.cancel_dictation()
        self.hotkeys.stop()                    # чтобы поле записи клавиш не срабатывало
        dialog = SettingsDialog(self.cfg, focus_keys=focus_keys)
        self._settings_dialog = dialog
        accepted = dialog.exec()
        self._settings_dialog = None
        self._apply_hotkeys()
        if accepted:
            self._sync_menu()
            self._warm_up()

    def _warm_up(self) -> None:
        """Заранее открывает соединения и грузит локальную модель."""

        def task() -> None:
            snapshot = _Snapshot(self.cfg.as_dict())
            providers.warm(snapshot)
            stt.preload(snapshot)

        threading.Thread(target=task, daemon=True, name="shepot-warmup").start()

    def _sync_menu(self) -> None:
        for action in self._mode_group.actions():
            action.setChecked(action.data() == self.cfg.get("mode"))
        for action in self._tone_group.actions():
            action.setChecked(action.data() == self.cfg.get("tone"))
        self._update_tooltip()

    def open_history(self) -> None:
        if self._history_dialog is not None and self._history_dialog.isVisible():
            self._history_dialog.raise_()
            self._history_dialog.activateWindow()
            return
        dialog = HistoryDialog(self.history)
        self._history_dialog = dialog
        dialog.exec()
        self._history_dialog = None

    def quit(self) -> None:
        self.hotkeys.stop()
        self.recorder.close()
        self.hud.hide_now()
        self.tray.hide()
        self.app.quit()


class _Snapshot:
    """Неизменяемый срез настроек на время одной диктовки."""

    def __init__(self, data: dict) -> None:
        self._data = data

    def get(self, key: str, default=None):
        return self._data.get(key, default)


def _wav_seconds(wav: bytes | None) -> float:
    if not wav or len(wav) <= 44:
        return 0.0
    return (len(wav) - 44) / (audio.SAMPLE_WIDTH * audio.RATE)
