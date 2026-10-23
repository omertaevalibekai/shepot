"""Остров, развёрнутый в панель правки: как переписать выделенный текст.

Появляется на месте острова записи и вырастает из него на той же пружине.
Три готовых стиля (кнопки и клавиши 1–3) и поле для своей просьбы. Выбор
сворачивает панель обратно в капсулу, дальше остров показывает «Переписываю».
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from . import hud
from .island_place import PLACE

STYLES = (
    ("Деловой", "Перепиши в деловом стиле: вежливо, чётко и по делу, без разговорных слов."),
    ("Дружелюбный", "Перепиши тепло и дружелюбно, но по делу и без лишних слов."),
    ("Короче", "Сократи текст, сохранив смысл, все факты, цифры и имена."),
)

PANEL_W, PANEL_H = 430.0, 140.0
PAD = hud.PAD
WIDTH, HEIGHT = int(PANEL_W) + PAD * 2, int(PANEL_H) + PAD * 2
RADIUS = 20.0
VIOLET = QColor(165, 139, 255)

STYLE_SHEET = """
QLabel#title { color: #ffffff; }
QLabel#hint { color: rgba(255,255,255,0.45); }
QPushButton {
    color: #ffffff; background: #1f1f23; border: 1px solid #2e2e34; border-radius: 15px;
    padding: 0 14px; min-height: 30px;
}
QPushButton:hover { background: #2b2933; border-color: #a58bff; }
QPushButton:pressed { background: #36304a; }
QLineEdit {
    color: #ffffff; background: #1f1f23; border: 1px solid #2e2e34; border-radius: 15px;
    padding: 0 14px; min-height: 30px; selection-background-color: #6d4cdb;
}
QLineEdit:focus { border-color: #a58bff; }
"""


class EditPanel(QWidget):
    chosen = Signal(str, float)    # просьба для модели, ширина капсулы при сворачивании
    cancelled = Signal()

    def __init__(self) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(WIDTH, HEIGHT)
        self.setStyleSheet(STYLE_SHEET)

        font = QFont("Segoe UI")
        font.setPointSizeF(10)
        self.setFont(font)

        self._body = QWidget(self)
        self._body.setGeometry(PAD + 18, PAD + 12, int(PANEL_W) - 34, int(PANEL_H) - 24)
        self._fade = QGraphicsOpacityEffect(self._body)
        self._body.setGraphicsEffect(self._fade)

        self.title = QLabel(objectName="title")
        title_font = QFont("Segoe UI")
        title_font.setPointSizeF(10.5)
        title_font.setWeight(QFont.DemiBold)
        self.title.setFont(title_font)
        hint = QLabel("1–3 или свой текст · Esc", objectName="hint")
        top = QHBoxLayout()
        top.setContentsMargins(20, 0, 0, 0)             # место под лампу
        top.addWidget(self.title, 1)
        top.addWidget(hint)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        for index, (label, prompt) in enumerate(STYLES, start=1):
            button = QPushButton(label)
            button.setFocusPolicy(Qt.NoFocus)
            button.setCursor(Qt.PointingHandCursor)
            button.setToolTip(f"Клавиша {index}")
            button.clicked.connect(lambda _=False, p=prompt: self.choose(p))
            buttons.addWidget(button)
        buttons.addStretch(1)

        self.field = QLineEdit()
        self.field.setPlaceholderText("Или напишите, как изменить, и нажмите Enter")
        self.field.returnPressed.connect(self._choose_typed)

        column = QVBoxLayout(self._body)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(10)
        column.addLayout(top)
        column.addLayout(buttons)
        column.addWidget(self.field)

        self._w, self._h, self._vw, self._vh = PANEL_W, PANEL_H, 0.0, 0.0
        self._target = (PANEL_W, PANEL_H)
        self._closing: str | None = None     # None | "chosen" | "cancelled"
        self._prompt = ""
        self._was_active = False
        self._down = False

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.setInterval(hud.FRAME_MS)
        self._timer.timeout.connect(self._tick)

    # -------------------------------------------------------------- снаружи
    def open(self, text: str, anchor: QPoint, start_width: float) -> None:
        """Развернуться из капсулы шириной start_width; anchor — середина её нижнего края."""
        from .selection import count_words
        self.title.setText("Переписать · " + count_words(text))
        self.field.clear()
        # остров у верхнего края — панель раскрывается вниз
        self._down = not PLACE.grows_up(QPointF(anchor.x(), anchor.y() - hud.CAPSULE_H / 2))
        top = anchor.y() - int(hud.CAPSULE_H) - PAD if self._down else anchor.y() + PAD - HEIGHT
        self.move(anchor.x() - WIDTH // 2, top)
        motion = hud._animations_enabled()
        self._w, self._h = (start_width, hud.CAPSULE_H) if motion else (PANEL_W, PANEL_H)
        self._vw = self._vh = 0.0
        self._target = (PANEL_W, PANEL_H)
        self._closing = None
        self._was_active = False
        self._motion = motion
        self._fade.setOpacity(0.0 if motion else 1.0)
        self.show()
        self.raise_()
        self.activateWindow()          # чтобы 1–3, Esc и набор работали без щелчка
        self.setFocus()
        self._timer.start()

    def choose(self, prompt: str) -> None:
        if self._closing:
            return
        self._prompt = prompt
        self._collapse("chosen", (min(self._w, 190.0), hud.CAPSULE_H))

    def cancel(self) -> None:
        if self.isVisible() and not self._closing:
            self._collapse("cancelled", (hud.DOT_W, hud.CAPSULE_H))

    # ------------------------------------------------------------ внутреннее
    def _choose_typed(self) -> None:
        text = self.field.text().strip()
        if text:
            self.choose(text)

    def _collapse(self, how: str, target: tuple[float, float]) -> None:
        self._closing = how
        self._target = target
        self._fade.setOpacity(0.0)
        if not self._motion:
            self._finish()

    def _finish(self) -> None:
        self._timer.stop()
        self.hide()
        if self._closing == "chosen":
            self.chosen.emit(self._prompt, self._w)
        else:
            self.cancelled.emit()

    def _tick(self) -> None:
        dt = hud.FRAME_MS / 1000.0
        tw, th = self._target
        if self._motion:
            for attr, vattr, target in (("_w", "_vw", tw), ("_h", "_vh", th)):
                v = getattr(self, vattr) + (hud.STIFFNESS * (target - getattr(self, attr))
                                            - hud.DAMPING * getattr(self, vattr)) * dt
                setattr(self, vattr, v)
                setattr(self, attr, getattr(self, attr) + v * dt)
        else:
            self._w, self._h = tw, th
        if self._closing:
            if abs(self._h - th) < 3 and abs(self._w - tw) < 6:
                self._finish()
                return
        else:
            grown = (self._h - hud.CAPSULE_H) / (PANEL_H - hud.CAPSULE_H)
            self._fade.setOpacity(max(0.0, min(1.0, (grown - 0.6) / 0.4)))
        self.update()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key_Escape:
            self.cancel()
            return
        if not self.field.hasFocus():
            if Qt.Key_1 <= key <= Qt.Key_1 + len(STYLES) - 1:
                self.choose(STYLES[key - Qt.Key_1][1])
                return
            if event.text() and event.text().isprintable():
                # любая другая буква — начало своей просьбы
                self.field.setFocus()
                self.field.insert(event.text())
                return
        super().keyPressEvent(event)

    def changeEvent(self, event) -> None:
        if event.type() == event.Type.ActivationChange:
            if self.isActiveWindow():
                self._was_active = True
            elif self._was_active and not self._closing:
                self.cancel()          # щёлкнули в другое окно — передумали
        super().changeEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w = max(hud.DOT_W, min(PANEL_W, self._w))
        h = max(hud.CAPSULE_H, min(PANEL_H + 12, self._h))
        # растёт от края, к которому прижат остров
        rect = QRectF((WIDTH - w) / 2, PAD if self._down else PAD + PANEL_H - h, w, h)
        radius = min(RADIUS, h / 2)

        painter.setPen(Qt.NoPen)
        for step in range(6, 0, -1):
            shadow = QPainterPath()
            shadow.addRoundedRect(rect.adjusted(-step, -step + 3, step, step + 3),
                                  radius + step, radius + step)
            painter.fillPath(shadow, QColor(0, 0, 0, 9))
        shape = QPainterPath()
        shape.addRoundedRect(rect, radius, radius)
        painter.fillPath(shape, QColor(0, 0, 0))
        painter.setPen(QPen(QColor(255, 255, 255, 22), 1))
        painter.drawPath(shape)

        opacity = self._fade.opacity()
        if opacity > 0:
            painter.setOpacity(opacity)
            lamp = QRectF(PAD + 18, PAD + 12, 14, 22).center()
            glow = QRadialGradient(lamp, 11)
            glow.setColorAt(0, QColor(165, 139, 255, 140))
            glow.setColorAt(1, QColor(165, 139, 255, 0))
            painter.setPen(Qt.NoPen)
            painter.setBrush(glow)
            painter.drawEllipse(lamp, 11, 11)
            painter.setBrush(VIOLET)
            painter.drawEllipse(lamp, 4.4, 4.4)
