"""Плавающий «остров» внизу экрана: состояние диктовки и уровень звука.

Чёрная капсула вырастает из точки на пружине, пока идёт запись — слева лампа
и таймер, справа волна; при обработке и готовности сжимается под надпись.
Капсулу можно перетащить левой кнопкой (island_place.py), фокус она не берёт.
"""

from __future__ import annotations

import ctypes
import time
from collections import deque

from PySide6.QtCore import Qt, QTimer, QRectF, QPoint, QPointF
from PySide6.QtGui import (
    QColor, QFont, QFontMetricsF, QPainter, QPainterPath,
    QPen, QRadialGradient,
)
from PySide6.QtWidgets import QWidget

from .island_place import PLACE, Dragger, no_activate

BARS = 16
CAPSULE_H = 40.0
DOT_W = CAPSULE_H              # с этой ширины остров вырастает и в неё же уходит
MAX_W = 440.0
PAD = 14                       # поле под тень вокруг капсулы
WIDTH = int(MAX_W) + PAD * 2
HEIGHT = int(CAPSULE_H) + PAD * 2
BOTTOM_GAP = 28                # от нижнего края рабочей области (над панелью задач)

FRAME_MS = 16
SAMPLE_S = 0.045               # шаг истории уровня, как у прежнего индикатора
STIFFNESS, DAMPING = 380.0, 26.0   # чуть недодемпфированная пружина: лёгкий перелёт
FADE_IN_S, FADE_OUT_S = 0.09, 0.14

COLORS = {
    "listening": QColor(255, 159, 10),
    "processing": QColor(255, 255, 255),
    "done": QColor(48, 209, 88),
    "error": QColor(255, 69, 58),
}
LABELS = {
    "processing": "Обрабатываю",
    "done": "Готово",
    "error": "Ошибка",
}
LISTENING_W = 250.0
REPLY_COLOR = QColor(165, 139, 255)   # как «ждёт ответа» в острове Claude Code
RECORDING = ("listening",)


def _animations_enabled() -> bool:
    """Системная настройка «Показывать анимацию в Windows» (SPI_GETCLIENTAREAANIMATION)."""
    try:
        value = ctypes.c_int(1)
        ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(value), 0)
        return bool(value.value)
    except Exception:
        return True


class Hud(QWidget):
    """Окно-остров: не забирает фокус; капсулу можно перетащить, мимо неё клики проходят."""

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
        self.setMouseTracking(True)
        self.resize(WIDTH, HEIGHT)
        self.drag = Dragger(self, self._capsule_rect, self._place_center)

        self._font = QFont("Segoe UI")
        self._font.setPointSizeF(10.5)
        self._font.setWeight(QFont.Medium)
        self._mono = QFont("Consolas")   # есть в любой Windows; поиск отсутствующего шрифта стоит ~0,3 с
        self._mono.setPointSizeF(10)
        self._mono.setWeight(QFont.DemiBold)
        self._hint_font = QFont("Segoe UI")
        self._hint_font.setPointSizeF(9.5)
        self._hint_font.setWeight(QFont.DemiBold)
        # подбор шрифта ленивый и случается на первой отрисовке — делаем его сейчас,
        # иначе первый кадр записи замирает
        for font in (self._font, self._mono, self._hint_font):
            QFontMetricsF(font).horizontalAdvance("0:00 Обрабатываю")

        self._state = "listening"
        self._message = ""
        self._hint = ""
        self._levels = deque([0.0] * BARS, maxlen=BARS)
        self._level = 0.0
        self._started = 0.0          # начало записи — для таймера
        self._shown_at = 0.0         # для проявления
        self._leaving_at = 0.0       # 0 — остров не уходит
        self._width = DOT_W
        self._velocity = 0.0
        self._last_tick = 0.0
        self._sample_acc = 0.0
        self._motion = _animations_enabled()

        self._anim = QTimer(self)
        self._anim.setTimerType(Qt.PreciseTimer)
        self._anim.setInterval(FRAME_MS)
        self._anim.timeout.connect(self._tick)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._leave)

    # ------------------------------------------------------------- состояние
    def show_state(self, state: str, message: str = "", hide_after: float = 0.0,
                   hint: str = "", from_width: float | None = None) -> None:
        """from_width — остров «принимает эстафету» у панели правки: начинает с её ширины."""
        now = time.monotonic()
        appearing = not self.isVisible() or self._leaving_at
        self._state = state
        self._message = message
        self._hint = hint
        self._leaving_at = 0.0
        if state == "listening":
            self._levels = deque([0.0] * BARS, maxlen=BARS)
            self._level = 0.0
            self._started = now
        self._hide_timer.stop()
        if appearing:
            self._width, self._velocity = (from_width or DOT_W), 0.0
            self._shown_at = now - (FADE_IN_S if from_width else 0.0)
            if not self.drag.busy:
                self._reposition()
            self._motion = _animations_enabled()
        if not self.isVisible():
            self.show()
        if not self._anim.isActive():
            self._last_tick = now
            self._anim.start()
        if hide_after > 0:
            self._hide_timer.start(int(hide_after * 1000))
        self.update()

    def capsule_anchor(self) -> QPoint:
        """Середина нижнего края капсулы на экране — сюда же встаёт панель правки."""
        if not self.isVisible():
            self._reposition()
        return QPoint(self.x() + WIDTH // 2, self.y() + PAD + int(CAPSULE_H))

    def hand_over(self) -> float:
        """Спрятаться мгновенно, отдав место панели; вернуть текущую ширину капсулы."""
        width = max(DOT_W, self._width) if self.isVisible() else DOT_W
        self._hide_timer.stop()
        self._finish_hide()
        return width

    def set_level(self, level: float) -> None:
        self._level = max(0.0, min(1.0, float(level)))

    def hide_now(self) -> None:
        """Убрать остров: он сжимается в точку и гаснет за доли секунды."""
        self._hide_timer.stop()
        if self.isVisible():
            self._leave()

    # ------------------------------------------------------------ внутреннее
    def _leave(self) -> None:
        if self.drag.dragging:
            self._hide_timer.start(400)            # держат в руке — уберём, когда отпустят
            return
        if not self._motion:
            self._finish_hide()
            return
        if not self._leaving_at:
            self._leaving_at = time.monotonic()

    def _finish_hide(self) -> None:
        self._anim.stop()
        self._leaving_at = 0.0
        self.hide()

    def _target_width(self) -> float:
        if self._leaving_at:
            return DOT_W
        if self._state in RECORDING:
            if self._message:                          # «→ сессия»: ответ Claude Code голосом
                text = QFontMetricsF(self._font).horizontalAdvance(self._message)
                return min(MAX_W, LISTENING_W + text + 16.0)
            return LISTENING_W
        width = QFontMetricsF(self._font).horizontalAdvance(self._label()) + 62.0
        if self._hint:
            width += QFontMetricsF(self._hint_font).horizontalAdvance(self._hint) + 10.0
        return min(MAX_W, max(150.0, width))

    def _label(self) -> str:
        return self._message or LABELS.get(self._state, "")

    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(0.05, max(0.0, now - self._last_tick))
        self._last_tick = now

        self.drag.tick(dt)
        target = self._target_width()
        if self._motion:
            accel = STIFFNESS * (target - self._width) - DAMPING * self._velocity
            self._velocity += accel * dt
            self._width += self._velocity * dt
        else:
            self._width, self._velocity = target, 0.0

        if self._state in RECORDING and not self._leaving_at:
            self._sample_acc += dt
            while self._sample_acc >= SAMPLE_S:
                self._sample_acc -= SAMPLE_S
                self._levels.append(self._level)

        if self._leaving_at and now - self._leaving_at >= FADE_OUT_S:
            self._finish_hide()
            return
        self.update()

    def _reposition(self) -> None:
        self._place_center(PLACE.center())

    def _place_center(self, center: QPointF) -> None:
        self.move(int(round(center.x() - WIDTH / 2)), int(round(center.y() - CAPSULE_H / 2 - PAD)))

    def _capsule_rect(self) -> QRectF:
        width = max(DOT_W, min(MAX_W, self._width))
        return QRectF((WIDTH - width) / 2, PAD, width, CAPSULE_H)

    # ------------------------------------------------------------ мышь
    def showEvent(self, event) -> None:
        super().showEvent(event)
        no_activate(self)

    def mousePressEvent(self, event) -> None:
        self.drag.press(event)

    def mouseMoveEvent(self, event) -> None:
        self.drag.move(event)

    def mouseReleaseEvent(self, event) -> None:
        self.drag.release(event)

    def mouseDoubleClickEvent(self, event) -> None:
        self.drag.double_click(event)

    def _opacity(self, now: float) -> float:
        if not self._motion:
            return 1.0
        if self._leaving_at:
            return max(0.0, 1.0 - (now - self._leaving_at) / FADE_OUT_S)
        return min(1.0, (now - self._shown_at) / FADE_IN_S)

    # -------------------------------------------------------------- отрисовка
    def paintEvent(self, _event) -> None:
        now = time.monotonic()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setOpacity(self._opacity(now))

        rect = self._capsule_rect()
        width = rect.width()
        radius = CAPSULE_H / 2

        # мягкая тень: несколько расширяющихся контуров с малой прозрачностью;
        # в руке — тень глубже, остров «приподнят»
        painter.setPen(Qt.NoPen)
        for step in range(9 if self.drag.dragging else 6, 0, -1):
            shadow = QPainterPath()
            shadow.addRoundedRect(rect.adjusted(-step, -step + 3, step, step + 3),
                                  radius + step, radius + step)
            painter.fillPath(shadow, QColor(0, 0, 0, 9))

        capsule = QPainterPath()
        capsule.addRoundedRect(rect, radius, radius)
        painter.fillPath(capsule, QColor(0, 0, 0))
        painter.setPen(QPen(QColor(255, 255, 255, 22), 1))
        painter.drawPath(capsule)

        # содержимое проявляется, когда капсула почти доросла
        target = self._target_width()
        if self._leaving_at or target <= DOT_W:
            return
        content = (width - 60.0) / max(1.0, target - 60.0)
        if content < 0.3:
            return
        painter.setOpacity(painter.opacity() * min(1.0, content))
        painter.setClipPath(capsule)

        if self._state in RECORDING:
            self._draw_listening(painter, rect, now)
        else:
            self._draw_status(painter, rect, now)

    def _draw_listening(self, painter: QPainter, rect: QRectF, now: float) -> None:
        accent = COLORS[self._state]
        lamp = QPointF(rect.left() + 21, rect.center().y())

        glow = QRadialGradient(lamp, 11)
        glow.setColorAt(0, QColor(accent.red(), accent.green(), accent.blue(), 140))
        glow.setColorAt(1, QColor(accent.red(), accent.green(), accent.blue(), 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(lamp, 11, 11)
        painter.setBrush(accent)
        painter.drawEllipse(lamp, 4.4, 4.4)

        seconds = int(now - self._started)
        painter.setFont(self._mono)
        painter.setPen(accent)
        painter.drawText(QRectF(rect.left() + 34, rect.top(), 60, rect.height()),
                         Qt.AlignVCenter | Qt.AlignLeft, f"{seconds // 60}:{seconds % 60:02d}")

        if self._message:
            room = rect.width() - (LISTENING_W - 10)
            painter.setFont(self._font)
            painter.setPen(REPLY_COLOR)
            text = QFontMetricsF(self._font).elidedText(self._message, Qt.ElideRight, room)
            painter.drawText(QRectF(rect.left() + 82, rect.top(), room, rect.height()),
                             Qt.AlignVCenter | Qt.AlignLeft, text)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255))
        step, bar_w = 5.0, 2.4
        center = rect.center().y()
        right = rect.right() - 20
        for index, level in enumerate(self._levels):
            height = 2.5 + level * 21.0
            x = right - (BARS - 1 - index) * step
            painter.drawRoundedRect(QRectF(x, center - height / 2, bar_w, height), 1.2, 1.2)

    def _draw_status(self, painter: QPainter, rect: QRectF, now: float) -> None:
        color = COLORS.get(self._state, COLORS["processing"])
        mark = QPointF(rect.left() + 23, rect.center().y())
        pen = QPen(color, 2.2)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        if self._state == "processing":
            start = (now * 2400) % 5760 if self._motion else 0   # 1/16 градуса
            painter.drawArc(QRectF(mark.x() - 7, mark.y() - 7, 14, 14), -int(start), -4000)
        elif self._state == "done":
            path = QPainterPath(QPointF(mark.x() - 5.5, mark.y()))
            path.lineTo(mark.x() - 1.6, mark.y() + 3.9)
            path.lineTo(mark.x() + 5.5, mark.y() - 3.9)
            painter.drawPath(path)
        else:
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(mark, 8, 8)
            painter.setPen(_bang_pen())
            painter.drawLine(QPointF(mark.x(), mark.y() - 3.8), QPointF(mark.x(), mark.y() + 0.6))
            painter.drawPoint(QPointF(mark.x(), mark.y() + 3.6))

        painter.setFont(self._font)
        painter.setPen(QColor(255, 255, 255))
        text_rect = QRectF(rect.left() + 40, rect.top(), rect.width() - 54, rect.height())
        metrics = QFontMetricsF(self._font)
        label = metrics.elidedText(" ".join(self._label().split()), Qt.ElideRight, text_rect.width())
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, label)
        if self._hint:
            painter.setFont(self._hint_font)
            painter.setPen(QColor(255, 255, 255, 128))
            hint_rect = text_rect.adjusted(metrics.horizontalAdvance(label) + 10, 0, 0, 0)
            painter.drawText(hint_rect, Qt.AlignVCenter | Qt.AlignLeft, self._hint)


def _bang_pen() -> QPen:
    pen = QPen(QColor(0, 0, 0), 2.2)
    pen.setCapStyle(Qt.RoundCap)
    return pen
