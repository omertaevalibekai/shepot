"""Плавающая подсказка поверх всех окон: состояние диктовки и уровень звука."""

from __future__ import annotations

from collections import deque

from PySide6.QtCore import Qt, QTimer, QRectF, QPointF
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QCursor
from PySide6.QtWidgets import QWidget

BARS = 26
WIDTH = 260
HEIGHT = 58

COLORS = {
    "listening": QColor(96, 205, 255),
    "processing": QColor(255, 196, 92),
    "done": QColor(126, 227, 143),
    "error": QColor(255, 118, 118),
}

LABELS = {
    "listening": "Слушаю",
    "processing": "Обрабатываю",
    "done": "Готово",
    "error": "Ошибка",
}


class Hud(QWidget):
    """Окно-«пилюля», которое не забирает фокус у активного приложения."""

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.NoFocus)
        self.resize(WIDTH, HEIGHT)

        self._state = "listening"
        self._message = ""
        self._levels = deque([0.0] * BARS, maxlen=BARS)
        self._level = 0.0
        self._phase = 0

        self._anim = QTimer(self)
        self._anim.setInterval(45)
        self._anim.timeout.connect(self._tick)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    # ------------------------------------------------------------- состояние
    def show_state(self, state: str, message: str = "", hide_after: float = 0.0) -> None:
        self._state = state
        self._message = message
        if state == "listening":
            self._levels = deque([0.0] * BARS, maxlen=BARS)
        self._hide_timer.stop()
        self._reposition()
        if not self.isVisible():
            self.show()
        if not self._anim.isActive():
            self._anim.start()
        if hide_after > 0:
            self._hide_timer.start(int(hide_after * 1000))
        self.update()

    def set_level(self, level: float) -> None:
        self._level = max(0.0, min(1.0, float(level)))

    def hide_now(self) -> None:
        self._hide_timer.stop()
        self._anim.stop()
        self.hide()

    # ------------------------------------------------------------ внутреннее
    def _tick(self) -> None:
        if self._state == "listening":
            self._levels.append(self._level)
        self._phase = (self._phase + 1) % 1000
        self.update()

    def _reposition(self) -> None:
        screen = self.screen()
        cursor = QCursor.pos()
        from PySide6.QtGui import QGuiApplication

        at_cursor = QGuiApplication.screenAt(cursor)
        if at_cursor is not None:
            screen = at_cursor
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(
            area.x() + (area.width() - WIDTH) // 2,
            area.y() + area.height() - HEIGHT - 90,
        )

    # -------------------------------------------------------------- отрисовка
    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, WIDTH, HEIGHT), HEIGHT / 2, HEIGHT / 2)
        painter.fillPath(path, QColor(22, 24, 30, 235))
        painter.setPen(QColor(255, 255, 255, 28))
        painter.drawPath(path)

        accent = COLORS.get(self._state, COLORS["listening"])
        painter.setPen(Qt.NoPen)
        painter.setBrush(accent)
        painter.drawEllipse(QPointF(26, HEIGHT / 2), 5.5, 5.5)

        if self._state == "listening":
            self._draw_wave(painter, accent)
        else:
            self._draw_text(painter)

    def _draw_wave(self, painter: QPainter, accent: QColor) -> None:
        painter.setBrush(accent)
        left, right = 46.0, WIDTH - 24.0
        step = (right - left) / BARS
        center = HEIGHT / 2
        for index, level in enumerate(self._levels):
            height = 4.0 + level * 30.0
            x = left + index * step
            painter.setOpacity(0.35 + 0.65 * (index / BARS))
            painter.drawRoundedRect(
                QRectF(x, center - height / 2, max(2.0, step - 2.5), height), 1.6, 1.6
            )
        painter.setOpacity(1.0)

    def _draw_text(self, painter: QPainter) -> None:
        label = self._message or LABELS.get(self._state, "")
        if self._state == "processing":
            label = LABELS["processing"] + "." * (1 + (self._phase // 6) % 3)
        font = QFont()
        font.setPointSizeF(10.5)
        painter.setFont(font)
        painter.setPen(QColor(236, 238, 245))
        painter.drawText(
            QRectF(44, 0, WIDTH - 60, HEIGHT),
            Qt.AlignVCenter | Qt.AlignLeft,
            _elide(label, 30),
        )


def _elide(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
