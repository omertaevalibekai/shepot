"""Скриншот из буфера обмена → файл на диске → путь в буфере.

Терминалы не умеют принимать картинку из буфера: Ctrl+V в консоли вставляет
текст и ничего больше. Поэтому вотчер ловит момент, когда в буфере оказалось
изображение (Win+Shift+S, PrtScr, «копировать картинку», копирование файла
картинки в проводнике), сохраняет его в PNG и кладёт в буфер путь к файлу.
Дальше обычный Ctrl+V вставляет строку, которую ассистент в терминале может
открыть.

Слушаем сигнал буфера обмена, а не опрашиваем его в цикле — картинка
превращается в путь мгновенно. Опрос оставлен запасным путём: Windows иногда
не доставляет уведомление, если в этот момент буфер держало другое приложение,
а молча пропавший скриншот выглядит как сломанная функция.
"""

from __future__ import annotations

import ctypes
import os
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

# буфер только что записало чужое приложение — даём ему дописать до конца
READ_DELAY_MS = 120
# запасной опрос на случай, если уведомление не дошло
POLL_MS = 1200

_u32 = ctypes.windll.user32


def default_dir() -> Path:
    return Path(os.environ.get("TEMP") or Path.home()) / "claude-shots"


def _sequence() -> int:
    """Счётчик изменений буфера — дешевле, чем открывать сам буфер."""
    return int(_u32.GetClipboardSequenceNumber())


def prune(folder: Path, keep_days: int) -> None:
    """Убирает старые снимки, чтобы папка не росла бесконечно."""
    if keep_days <= 0 or not folder.is_dir():
        return
    deadline = time.time() - keep_days * 86400
    for item in folder.glob("shot-*.png"):
        try:
            if item.stat().st_mtime < deadline:
                item.unlink()
        except OSError:
            pass


class Watcher(QObject):
    """Следит за буфером обмена, пока включён.

    Живёт на потоке интерфейса: QClipboard можно трогать только оттуда.
    Работы на сигнал приходится немного — сохранение PNG на 4K-экран занимает
    единицы миллисекунд, диктовку это не задержит.
    """

    captured = Signal(str)      # путь, уже положенный в буфер

    def __init__(self, cfg, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self._enabled = False
        self._seen = _sequence()

        self._clipboard = QApplication.clipboard()

        # читаем не сразу по сигналу: см. READ_DELAY_MS
        self._read_timer = QTimer(self)
        self._read_timer.setSingleShot(True)
        self._read_timer.setInterval(READ_DELAY_MS)
        self._read_timer.timeout.connect(self._check)

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(POLL_MS)
        self._poll_timer.timeout.connect(self._poll)

    # ------------------------------------------------------------ управление
    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._enabled:
            return
        self._enabled = enabled
        if enabled:
            # то, что лежало в буфере до включения, не трогаем
            self._seen = _sequence()
            self._clipboard.dataChanged.connect(self._on_change)
            self._poll_timer.start()
            prune(self._folder(), int(self.cfg.get("clipshot_keep_days") or 0))
        else:
            try:
                self._clipboard.dataChanged.disconnect(self._on_change)
            except (RuntimeError, TypeError):
                pass
            self._poll_timer.stop()
            self._read_timer.stop()

    # -------------------------------------------------------------- проверка
    def _on_change(self) -> None:
        self._read_timer.start()

    def _poll(self) -> None:
        if _sequence() != self._seen:
            self._check()

    def _check(self) -> None:
        if not self._enabled:
            return
        self._seen = _sequence()
        try:
            path = self._grab()
        except Exception:
            # буфер держит другое приложение: запасной опрос вернётся сюда сам
            return
        if path is None:
            return
        text = self._publish(path)
        self._seen = _sequence()      # своя же запись — не повод срабатывать
        self.captured.emit(text)

    def _grab(self) -> Path | None:
        data = self._clipboard.mimeData()
        if data is None:
            return None

        if data.hasImage():
            image = self._clipboard.image()
            if not image.isNull():
                folder = self._folder()
                folder.mkdir(parents=True, exist_ok=True)
                stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
                path = folder / f"shot-{stamp}.png"
                if image.save(str(path), "PNG"):
                    return path
                return None

        if data.hasUrls():
            for url in data.urls():
                local = url.toLocalFile()
                if local and Path(local).suffix.lower() in IMAGE_SUFFIXES:
                    return Path(local)
        return None

    def _publish(self, path: Path) -> str:
        text = str(path)
        # кавычки только когда без них не обойтись — иначе путь грязнее
        if any(ch.isspace() for ch in text):
            text = f'"{text}"'
        self._clipboard.setText(text)
        return text

    def _folder(self) -> Path:
        custom = (self.cfg.get("clipshot_dir") or "").strip()
        return Path(custom) if custom else default_dir()
