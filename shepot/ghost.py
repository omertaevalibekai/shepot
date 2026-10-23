"""Окно-призрак, часть в Шёпоте: запускает помощника с хуком мыши и рисует подсказки.

* шкала у курсора — пока крутишь колесо;
* ярлык над каждым прозрачным окном («Призрак 60 %»), а в режиме «насквозь»
  ещё и оранжевый пунктир вокруг окна, чтобы окно нельзя было потерять;
* капсула внизу экрана «2 окна-призрака · Вернуть все».

Сам хук и смена прозрачности — в ghost_hook.py (отдельный процесс).
Координаты от помощника физические, поэтому подсказки ставятся средствами
Windows (SetWindowPos), без пересчёта масштаба Qt.
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
from ctypes import wintypes
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from . import ghost_hook
from .island_place import PLACE

CREATE_NO_WINDOW = 0x08000000
SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_NOMOVE = 0x0001, 0x0004, 0x0010, 0x0002
SWP_SHOWWINDOW = 0x0040
HWND_TOP = 0
GW_HWNDPREV = 3
DWMWA_EXTENDED_FRAME_BOUNDS = 9

ORANGE = QColor(255, 159, 10)
GREEN = QColor(48, 209, 88)
INK = QColor(10, 10, 11)

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi
user32.GetWindow.restype = wintypes.HWND
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]


def helper_command(owner_pid: int) -> tuple[list[str], str | None]:
    """Команда запуска помощника: в сборке — тот же Shepot.exe с ключом."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--ghost-helper", str(owner_pid)], None
    root = str(Path(__file__).resolve().parent.parent)
    return [sys.executable, "-m", "shepot.ghost_hook", "--ghost-helper", str(owner_pid)], root


def visible_rect(hwnd: int) -> list[int]:
    """Видимая рамка окна, без невидимой полосы захвата Windows 11."""
    rect = wintypes.RECT()
    if dwmapi.DwmGetWindowAttribute(wintypes.HWND(hwnd), DWMWA_EXTENDED_FRAME_BOUNDS,
                                    ctypes.byref(rect), ctypes.sizeof(rect)) == 0:
        return [rect.left, rect.top, rect.right, rect.bottom]
    return ghost_hook.window_rect(hwnd)


def _native_size(widget: QWidget) -> tuple[int, int]:
    left, top, right, bottom = ghost_hook.window_rect(int(widget.winId()))
    return right - left, bottom - top


def _overlay_flags(top: bool = True) -> Qt.WindowType:
    flags = (Qt.FramelessWindowHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
             | Qt.WindowTransparentForInput)
    return flags | Qt.WindowStaysOnTopHint if top else flags


class _Overlay(QWidget):
    def __init__(self, top: bool = True) -> None:
        super().__init__(None, _overlay_flags(top))
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

    def place(self, x: int, y: int, above: int | None = None) -> None:
        """Поставить в физические координаты; above — окно, над которым держаться."""
        if not self.isVisible():
            self.show()
        hwnd = int(self.winId())
        flags = SWP_NOSIZE | SWP_NOACTIVATE
        if above:
            prev = user32.GetWindow(above, GW_HWNDPREV)
            if prev and int(prev) == hwnd:
                flags |= SWP_NOZORDER              # уже лежим прямо над окном
                prev = None
            insert = prev or HWND_TOP
        else:
            flags |= SWP_NOZORDER
            insert = None
        key = (x, y, flags, int(insert or 0))
        if key == getattr(self, "_last", None):
            return
        self._last = key
        user32.SetWindowPos(hwnd, insert, x, y, 0, 0, flags)


class GhostScale(_Overlay):
    """Вертикальная шкала у курсора; штриховка снизу — зона «насквозь»."""

    W, H = 46, 176

    def __init__(self) -> None:
        super().__init__()
        self.resize(self.W + 120, self.H + 20)
        self._pct, self._thru = 100, False
        self._mono = QFont("Consolas")
        self._mono.setPointSizeF(8.5)
        self._mono.setWeight(QFont.DemiBold)
        self._font = QFont("Segoe UI")
        self._font.setPointSizeF(8.5)
        for f in (self._mono, self._font):
            QFontMetricsF(f).horizontalAdvance("насквозь 100")
        self._hide = QTimer(self, singleShot=True)
        self._hide.timeout.connect(self.hide)

    def show_at(self, x: int, y: int, pct: int, thru: bool) -> None:
        self._pct, self._thru = pct, thru
        self.update()
        self.place(x + 22, y - self.H // 2)
        self._hide.start(900)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        body = QRectF(10, 10, self.W, self.H)
        p.setPen(Qt.NoPen)
        for step in range(5, 0, -1):
            shadow = QPainterPath()
            shadow.addRoundedRect(body.adjusted(-step, -step + 2, step, step + 2), 23 + step, 23 + step)
            p.fillPath(shadow, QColor(0, 0, 0, 12))
        p.setBrush(INK)
        p.drawRoundedRect(body, 23, 23)

        track = QRectF(body.center().x() - 4, body.top() + 16, 8, body.height() - 56)
        p.setBrush(QColor(255, 255, 255, 30))
        p.drawRoundedRect(track, 4, 4)
        zone_h = track.height() * (ghost_hook.THRU_PCT - ghost_hook.MIN_PCT) / (100 - ghost_hook.MIN_PCT)
        zone = QRectF(track.left(), track.bottom() - zone_h, track.width(), zone_h)
        hatch = QBrush(QColor(255, 159, 10, 120), Qt.BDiagPattern)
        p.setBrush(hatch)
        p.drawRoundedRect(zone, 4, 4)
        level = track.height() * (self._pct - ghost_hook.MIN_PCT) / (100 - ghost_hook.MIN_PCT)
        p.setBrush(ORANGE if self._thru else QColor(255, 255, 255))
        p.drawRoundedRect(QRectF(track.left(), track.bottom() - level, track.width(), level), 4, 4)

        p.setFont(self._mono)
        p.setPen(QColor(255, 255, 255))
        p.drawText(QRectF(body.left(), body.bottom() - 34, body.width(), 22), Qt.AlignCenter, str(self._pct))
        if self._thru:
            label = QRectF(body.right() + 6, body.bottom() - 42, 70, 20)
            p.setPen(Qt.NoPen)
            p.setBrush(INK)
            p.drawRoundedRect(label, 6, 6)
            p.setFont(self._font)
            p.setPen(QColor(255, 255, 255))
            p.drawText(label, Qt.AlignCenter, "насквозь")


class GhostTab(_Overlay):
    """Чёрный ярлык над верхним правым углом прозрачного окна."""

    H = 24

    def __init__(self) -> None:
        super().__init__(top=False)
        self._font = QFont("Segoe UI")
        self._font.setPointSizeF(8.5)
        self._font.setWeight(QFont.Medium)
        QFontMetricsF(self._font).horizontalAdvance("Призрак 60%")
        self._text, self._hint, self._thru = "", "", False

    def set_state(self, pct: int, thru: bool, peek: bool) -> None:
        self._thru = thru
        self._text = "Подглядываю" if peek else (f"Насквозь {pct}%" if thru else f"Призрак {pct}%")
        self._hint = "Alt+средняя вернёт 100%"
        m = QFontMetricsF(self._font)
        self.resize(int(m.horizontalAdvance(self._text) + m.horizontalAdvance(self._hint) + 44), self.H)
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(0, 0, self.width(), self.height() + 8)            # низ уходит за край — «язычок»
        p.setPen(Qt.NoPen)
        p.setBrush(INK)
        p.drawRoundedRect(r, 7, 7)
        p.setBrush(ORANGE)
        p.drawEllipse(QRectF(10, self.height() / 2 - 3.5, 7, 7))
        p.setFont(self._font)
        p.setPen(QColor(255, 255, 255))
        m = QFontMetricsF(self._font)
        p.drawText(QRectF(23, 0, self.width(), self.height()), Qt.AlignVCenter | Qt.AlignLeft, self._text)
        p.setPen(QColor(255, 255, 255, 130))
        p.drawText(QRectF(31 + m.horizontalAdvance(self._text), 0, self.width(), self.height()),
                   Qt.AlignVCenter | Qt.AlignLeft, self._hint)


class GhostFrame(_Overlay):
    """Оранжевый пунктир вокруг окна в режиме «насквозь»."""

    PAD = 5

    def __init__(self) -> None:
        super().__init__(top=False)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(ORANGE, 2, Qt.DashLine)
        pen.setDashPattern([4, 3])
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(1, 1, self.width() - 2, self.height() - 2), 10, 10)


class GhostChip(QWidget):
    """Капсула внизу экрана: сколько окон-призраков и кнопка «Вернуть все»."""

    restoreAll = Signal()
    H = 36

    def __init__(self) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setCursor(Qt.PointingHandCursor)
        self._font = QFont("Segoe UI")
        self._font.setPointSizeF(9.5)
        self._font.setWeight(QFont.Medium)
        QFontMetricsF(self._font).horizontalAdvance("2 окна-призрака Вернуть все")
        self._text = ""
        self._hover = False
        self.setMouseTracking(True)

    def set_count(self, count: int) -> None:
        forms = ("окно-призрак", "окна-призрака", "окон-призраков")
        n = count % 100
        form = forms[2] if 11 <= n <= 14 else forms[0] if n % 10 == 1 else forms[1] if 2 <= n % 10 <= 4 else forms[2]
        self._text = f"{count} {form}"
        m = QFontMetricsF(self._font)
        self.resize(int(m.horizontalAdvance(self._text) + m.horizontalAdvance("Вернуть все") + 98), self.H + 16)
        self.update()

    def place(self, lift: int) -> None:
        """На месте острова; если там остров Claude — рядом, со стороны раскрытия."""
        center = PLACE.center()
        if lift:
            center.setY(center.y() + (-lift if PLACE.grows_up(center) else lift))
        self.move(int(center.x() - self.width() / 2), int(center.y() - self.H / 2 - 8))

    def _button(self) -> QRectF:
        m = QFontMetricsF(self._font)
        w = m.horizontalAdvance("Вернуть все") + 22
        return QRectF(self.width() - 8 - 5 - w, 8 + 5, w, self.H - 10)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        body = QRectF(8, 8, self.width() - 16, self.H)
        p.setPen(Qt.NoPen)
        for step in range(5, 0, -1):
            shadow = QPainterPath()
            shadow.addRoundedRect(body.adjusted(-step, -step + 2, step, step + 2), self.H / 2 + step, self.H / 2 + step)
            p.fillPath(shadow, QColor(0, 0, 0, 10))
        p.setBrush(INK)
        p.drawRoundedRect(body, self.H / 2, self.H / 2)
        p.setBrush(ORANGE)
        p.drawEllipse(QRectF(body.left() + 14, body.center().y() - 4, 8, 8))
        p.setFont(self._font)
        p.setPen(QColor(255, 255, 255))
        p.drawText(body.adjusted(30, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, self._text)
        button = self._button()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 52 if self._hover else 30))
        p.drawRoundedRect(button, button.height() / 2, button.height() / 2)
        p.setPen(QColor(255, 255, 255))
        p.drawText(button, Qt.AlignCenter, "Вернуть все")

    def mouseMoveEvent(self, event) -> None:
        hover = self._button().contains(event.position())
        if hover != self._hover:
            self._hover = hover
            self.update()

    def mousePressEvent(self, event) -> None:
        if self._button().contains(event.position()):
            self.restoreAll.emit()


class Ghost(QObject):
    """Включает и выключает помощника; события от него превращает в подсказки."""

    event = Signal(dict)

    def __init__(self, parent=None, dictation_hud=None, claude_island=None) -> None:
        super().__init__(parent)
        self.scale = GhostScale()
        self.chip = GhostChip()
        self.chip.restoreAll.connect(self.restore_all)
        self.hud = dictation_hud
        self.claude_island = claude_island
        self.error = ""
        self._proc: subprocess.Popen | None = None
        self._ghosts: dict[int, int] = {}          # hwnd → проценты
        self._peek = 0
        self._tabs: dict[int, GhostTab] = {}
        self._frames: dict[int, GhostFrame] = {}
        self._stopping = False
        self.event.connect(self._on_event)
        self._follow = QTimer(self)
        self._follow.setInterval(120)               # ярлыки едут за окнами
        self._follow.timeout.connect(self._sync_overlays)

    @property
    def enabled(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def set_enabled(self, on: bool) -> None:
        if on and not self.enabled:
            self._start()
        elif not on and self._proc is not None:
            self.stop()

    def _start(self) -> None:
        cmd, cwd = helper_command(os.getpid())
        self._stopping = False
        try:
            self._proc = subprocess.Popen(
                cmd, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW,
            )
        except OSError as exc:
            self.error = str(exc)
            self._proc = None
            return
        threading.Thread(target=self._read, args=(self._proc,), name="ghost-read", daemon=True).start()

    def _read(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            try:
                self.event.emit(json.loads(line))
            except ValueError:
                continue
        # помощник ушёл: если не мы его остановили — возвращаем окна сами
        if not self._stopping:
            self.event.emit({"died": True})

    def _send(self, command: str) -> None:
        if self._proc is None:
            return
        try:
            self._proc.stdin.write(command.encode() + b"\n")
            self._proc.stdin.flush()
        except OSError:
            pass

    def restore_all(self) -> None:
        self._send("restore")

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        self._stopping = True
        try:
            proc.stdin.write(b"quit\n")
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(2.0)                     # помощник сам вернёт окна
        except subprocess.TimeoutExpired:
            proc.kill()
            self._rescue()
        else:
            if proc.returncode:
                self._rescue()
        self._set_ghosts({})
        self.scale.hide()

    def _rescue(self) -> None:
        for hwnd in self._ghosts:
            try:
                ghost_hook.force_opaque(hwnd)
            except Exception:
                pass

    # ------------------------------------------------------------ события
    def _on_event(self, event: dict) -> None:
        if event.get("died"):
            self._rescue()
            self._set_ghosts({})
            self._proc = None
            return
        if event.get("fatal"):
            self.error = event["fatal"]
            return
        if "ghosts" in event:
            self._set_ghosts({int(h): int(p) for h, p in event["ghosts"].items()})
        if "peek" in event:
            self._peek = event["hwnd"] if event["peek"] else 0
            self._sync_overlays()
            return
        error = event.get("error")
        if error and event.get("rect"):
            tab = self._tab(event["hwnd"])
            tab._text = ("Окно администратора — Windows его не даёт" if error == "denied"
                         else "Это окно рисует прозрачность само")
            tab._hint = ""
            tab.resize(int(QFontMetricsF(tab._font).horizontalAdvance(tab._text) + 34), tab.H)
            left, top, right, _ = event["rect"]
            tab.place(right - _native_size(tab)[0] - 12, max(top - tab.H, 0))
            QTimer.singleShot(1600, lambda h=event["hwnd"]: self._drop(h) if h not in self._ghosts else None)
            return
        if "pct" in event and event.get("x") is not None and (event.get("x") or event.get("y")):
            self.scale.show_at(event["x"], event["y"], int(event["pct"]), bool(event.get("thru")))

    def _set_ghosts(self, ghosts: dict[int, int]) -> None:
        for hwnd in list(self._tabs):
            if hwnd not in ghosts:
                self._drop(hwnd)
        self._ghosts = ghosts
        if self._peek not in ghosts:
            self._peek = 0
        if ghosts and not self._follow.isActive():
            self._follow.start()
        elif not ghosts:
            self._follow.stop()
        self._sync_overlays()

    def _tab(self, hwnd: int) -> GhostTab:
        if hwnd not in self._tabs:
            self._tabs[hwnd] = GhostTab()
        return self._tabs[hwnd]

    def _drop(self, hwnd: int) -> None:
        for store in (self._tabs, self._frames):
            widget = store.pop(hwnd, None)
            if widget is not None:
                widget.hide()
                widget.deleteLater()

    def _sync_overlays(self) -> None:
        for hwnd, pct in list(self._ghosts.items()):
            if not user32.IsWindow(hwnd):
                self._ghosts.pop(hwnd)
                self._drop(hwnd)
                continue
            shown = user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd)
            thru = pct <= ghost_hook.THRU_PCT and self._peek != hwnd
            tab = self._tab(hwnd)
            if not shown:
                tab.hide()
                if hwnd in self._frames:
                    self._frames[hwnd].hide()
                continue
            left, top, right, bottom = visible_rect(hwnd)
            tab.set_state(pct, thru, self._peek == hwnd)
            tab_w, tab_h = _native_size(tab)
            # над окном, если есть место; у развёрнутого — внутри, под верхним краем
            y = top - tab_h if top - tab_h >= 0 else top
            tab.place(right - tab_w - 12, y, above=hwnd)
            if thru:
                frame = self._frames.get(hwnd)
                if frame is None:
                    frame = self._frames[hwnd] = GhostFrame()
                pad = GhostFrame.PAD
                ratio = frame.devicePixelRatioF() or 1.0
                frame.resize(int((right - left + pad * 2) / ratio), int((bottom - top + pad * 2) / ratio))
                frame.place(left - pad, top - pad, above=hwnd)
            elif hwnd in self._frames:
                self._frames.pop(hwnd).deleteLater()
        self._sync_chip()

    def _sync_chip(self) -> None:
        count = len(self._ghosts)
        dictating = self.hud is not None and self.hud.isVisible()
        if not count or dictating:
            self.chip.hide()
            return
        lift = 56 if self.claude_island is not None and self.claude_island.isVisible() else 0
        self.chip.set_count(count)
        self.chip.place(lift)
        if not self.chip.isVisible():
            self.chip.show()
