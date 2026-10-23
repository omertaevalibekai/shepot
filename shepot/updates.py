"""Проверка обновлений: есть ли на GitHub релиз новее запущенной версии.

Раз в сутки (и через полминуты после запуска) спрашиваем GitHub API о последнем
релизе. Отправляется только обычный GET без данных о человеке. Нет сети или
GitHub ответил ошибкой — молчим и пробуем в следующий раз.
"""

from __future__ import annotations

import json
import re
import threading
import urllib.request

from PySide6.QtCore import QObject, QTimer, Signal

from . import __version__

REPO = "omertaevalibekai/shepot"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{REPO}/releases/latest"
FIRST_CHECK_MS = 30_000
INTERVAL_MS = 24 * 60 * 60 * 1000
TIMEOUT_S = 10


def parse_version(text: str) -> tuple[int, ...] | None:
    """'v1.3.0' / '1.3' / 'Шёпот 1.3.0' → (1, 3, 0). None, если номера нет."""
    match = re.search(r"(\d+(?:\.\d+)*)", text or "")
    if not match:
        return None
    parts = [int(p) for p in match.group(1).split(".")]
    while len(parts) > 1 and parts[-1] == 0:      # 1.3.0 == 1.3
        parts.pop()
    return tuple(parts)


def is_newer(latest: str, current: str = __version__) -> bool:
    new, old = parse_version(latest), parse_version(current)
    return bool(new and old and new > old)


def fetch_latest() -> dict | None:
    """{'version': '1.3.0', 'url': ...} последнего релиза или None."""
    request = urllib.request.Request(API_URL, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"Shepot/{__version__}",
    })
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None
    tag = str(data.get("tag_name") or "")
    if data.get("draft") or data.get("prerelease") or parse_version(tag) is None:
        return None
    return {"version": tag.lstrip("vV"), "url": data.get("html_url") or RELEASES_URL}


class Checker(QObject):
    """Фоновая проверка; found(version, url) — только если релиз новее текущего."""

    found = Signal(str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.check_now)
        self._running = False

    def set_enabled(self, enabled: bool) -> None:
        if enabled and not self._timer.isActive():
            self._timer.start(INTERVAL_MS)
            QTimer.singleShot(FIRST_CHECK_MS, self.check_now)
        elif not enabled:
            self._timer.stop()

    def check_now(self) -> None:
        if self._running or not self._timer.isActive():
            return
        self._running = True

        def work() -> None:
            try:
                latest = fetch_latest()
                if latest and is_newer(latest["version"]):
                    self.found.emit(latest["version"], latest["url"])
            finally:
                self._running = False

        threading.Thread(target=work, daemon=True, name="shepot-updates").start()
