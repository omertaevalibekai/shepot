"""Остров сессий Claude Code: статусы, запросы разрешений, «готово».

Живёт там же, где остров диктовки, и уступает ему место: пока идёт запись,
сжатый статус прячется, а карточка разрешения поднимается над ним (у верхнего
края экрана — опускается под него). Его тоже можно перетащить левой кнопкой.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from . import hud
from .claude_bridge import Bridge, Permission
from .island_place import PLACE, Dragger
from .i18n import t

COLORS = {"think": QColor(217, 119, 87), "wait": QColor(255, 196, 92), "ask": QColor(165, 139, 255),
          "done": QColor(48, 209, 88), "idle": QColor(110, 114, 128)}
STATE_TEXT = {"think": "думает", "wait": "ждёт разрешения", "ask": "ждёт ответа",
              "done": "готово", "idle": "свободна"}
PAD = hud.PAD
MAX_W, MAX_H = 520.0, 260.0
WIDTH, HEIGHT = int(MAX_W) + PAD * 2, int(MAX_H) + PAD * 2
ROW_H = 46.0
DONE_S = 2.8
LIFT_OVER_HUD = 56                 # карточка поверх острова диктовки, если тот на экране

CARD_STYLE = """
QLabel#head { color: #ffffff; }
QLabel#tool { color: rgba(255,255,255,0.5); }
QLabel#code {
    color: #f3f3f5; background: #16161a; border: 1px solid #2a2a31; border-radius: 12px;
    padding: 9px 12px; font-family: Consolas;
}
QPushButton {
    color: #ffffff; background: #1f1f24; border: 1px solid #303038; border-radius: 15px;
    padding: 0 14px; min-height: 30px;
}
QPushButton:hover { border-color: #6b6b78; }
QPushButton#allow { background: #ffffff; color: #000000; border-color: #ffffff; }
"""


def _fmt(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}:{s % 60:02d}"


class ClaudeIsland(QWidget):
    def __init__(self, bridge: Bridge, dictation_hud: hud.Hud) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.resize(WIDTH, HEIGHT)
        self.bridge, self.hud = bridge, dictation_hud
        self.enabled = True

        self._font = QFont("Segoe UI"); self._font.setPointSizeF(10); self._font.setWeight(QFont.Medium)
        self._small = QFont("Segoe UI"); self._small.setPointSizeF(9)
        self._mono = QFont("Consolas"); self._mono.setPointSizeF(9.5)
        for f in (self._font, self._small, self._mono):
            QFontMetricsF(f).horizontalAdvance("Пет-проекты 0:00")      # прогрев, как в hud.py

        self.view = "hidden"               # hidden | compact | list | perm | done
        self._done = ("", "", 0.0)
        self._request: Permission | None = None
        self._w = self._h = hud.CAPSULE_H
        self._vw = self._vh = 0.0
        self._down = False                 # остров у верха экрана: растём вниз
        self.setMouseTracking(True)
        self.drag = Dragger(self, self._rect, self._place_center)
        self._build_card()

        bridge.changed.connect(self._refresh)
        bridge.finished.connect(self._on_finished)
        bridge.permissionAsked.connect(self._on_permission)
        bridge.permissionClosed.connect(self._on_permission_closed)

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.setInterval(hud.FRAME_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # ---------------------------------------------------------- карточка
    def _build_card(self) -> None:
        self.card = QWidget(self)
        self.card.setStyleSheet(CARD_STYLE)
        self._fade = QGraphicsOpacityEffect(self.card)
        self.card.setGraphicsEffect(self._fade)
        self.head = QLabel(objectName="head")
        head_font = QFont("Segoe UI"); head_font.setPointSizeF(10.5); head_font.setWeight(QFont.DemiBold)
        self.head.setFont(head_font)
        self.tool = QLabel(objectName="tool")
        self.code = QLabel(objectName="code")
        self.code.setWordWrap(True)
        self.code.setTextInteractionFlags(Qt.TextSelectableByMouse)
        mono = QFont("Consolas"); mono.setPointSizeF(10)
        self.code.setFont(mono)
        self.allow_btn = QPushButton(t("Разрешить") + "   Enter", objectName="allow")
        self.deny_btn = QPushButton(t("Отказать") + "   Esc")
        self.always_btn = QPushButton(t("Всегда так"))
        for b in (self.allow_btn, self.deny_btn, self.always_btn):
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.NoFocus)
        self.allow_btn.clicked.connect(lambda: self._decide(True))
        self.deny_btn.clicked.connect(lambda: self._decide(False))
        self.always_btn.clicked.connect(lambda: self._decide(True, always=True))

        top = QHBoxLayout(); top.setContentsMargins(20, 0, 0, 0)
        top.addWidget(self.head, 1); top.addWidget(self.tool)
        buttons = QHBoxLayout(); buttons.setSpacing(8)
        for b in (self.allow_btn, self.deny_btn, self.always_btn):
            buttons.addWidget(b)
        buttons.addStretch(1)
        column = QVBoxLayout(self.card)
        column.setContentsMargins(18, 14, 18, 14)
        column.setSpacing(10)
        column.addLayout(top); column.addWidget(self.code); column.addLayout(buttons)
        self.card.hide()

    def _on_permission(self, request: Permission) -> None:
        if self._request is not None and not self._request.done.is_set():
            return                          # покажем следующую, когда решат текущую
        self._request = request
        self.head.setText(request.session_name + "  " + t("хочет выполнить"))
        self.tool.setText(request.tool)
        summary = request.summary or request.tool
        self.code.setText(summary if len(summary) <= 280 else summary[:277] + "…")
        self.always_btn.setVisible(bool(request.suggestions))
        self._go("perm")

    def _on_permission_closed(self, request: Permission) -> None:
        if request is self._request:
            self._next_request()

    def _decide(self, allow: bool, always: bool = False) -> None:
        if self._request is None:
            return
        self.bridge.decide(self._request, allow, always)
        self._next_request()

    def _next_request(self) -> None:
        self._request = None
        waiting = [r for r in self.bridge.pending if not r.done.is_set()]
        if waiting:
            self._on_permission(waiting[0])
        else:
            self.card.hide()
            self._go(self._resting_view())

    # -------------------------------------------------------------- виды
    def _on_finished(self, name: str, line: str) -> None:
        if self.view == "perm":
            return
        self._done = (name, line, time.monotonic())
        self._go("done")

    def _refresh(self) -> None:
        if self.view in ("hidden", "compact"):
            self._go(self._resting_view())

    def _resting_view(self) -> str:
        busy = [s for s in self.bridge.snapshot() if s.state in ("think", "wait", "ask")]
        if not busy:
            return "hidden"
        return "list" if self.view == "list" else "compact"

    def _go(self, view: str) -> None:
        self.view = view
        if view == "perm":
            self._fade.setOpacity(0.0)
            top = PAD if self._down else PAD + MAX_H - self._target()[1]
            self.card.setGeometry(int((WIDTH - MAX_W) / 2), int(top), int(MAX_W), int(self._target()[1]))
            self.card.show()
        else:
            self.card.hide()

    def _target(self) -> tuple[float, float]:
        if self.view == "perm":
            lines = max(1, min(4, len(self.code.text()) // 52 + 1))
            return MAX_W, 120.0 + lines * 18.0
        if self.view == "list":
            count = len([s for s in self.bridge.snapshot() if s.state != "idle"]) or 1
            return 420.0, min(MAX_H, 16.0 + count * ROW_H)
        if self.view == "done":
            return min(MAX_W, max(240.0, QFontMetricsF(self._font).horizontalAdvance(self._done_text()) + 70.0)), hud.CAPSULE_H
        if self.view == "compact":
            return 340.0, hud.CAPSULE_H
        return hud.DOT_W, hud.CAPSULE_H

    def _done_text(self) -> str:
        name, line, _ = self._done
        return f"{name} · " + t("готово")

    # ------------------------------------------------------------- кадр
    def _tick(self) -> None:
        if not self.enabled:
            if self.isVisible():
                self.hide()
            return
        if self.view == "done" and time.monotonic() - self._done[2] > DONE_S:
            self._go(self._resting_view() if self._resting_view() != "list" else "compact")
        dictating = self.hud.isVisible()
        wanted = self.view != "hidden" and not (dictating and self.view in ("compact", "list", "done"))
        if wanted and not self.isVisible():
            self._w, self._h = hud.DOT_W, hud.CAPSULE_H
            self._place(self.hud.capsule_anchor(), dictating)
            self.show()
        elif not wanted and self.isVisible():
            self.hide()
            return
        if not self.isVisible():
            return
        dt = hud.FRAME_MS / 1000.0
        self.drag.tick(dt)
        if self.view == "perm" and not self.drag.busy:
            self._place(self.hud.capsule_anchor(), dictating)
        tw, th = self._target()
        for attr, vattr, target in (("_w", "_vw", tw), ("_h", "_vh", th)):
            v = getattr(self, vattr) + (hud.STIFFNESS * (target - getattr(self, attr))
                                        - hud.DAMPING * getattr(self, vattr)) * dt
            setattr(self, vattr, v)
            setattr(self, attr, getattr(self, attr) + v * dt)
        if self.view == "perm":
            grown = (self._h - hud.CAPSULE_H) / max(1.0, th - hud.CAPSULE_H)
            self._fade.setOpacity(max(0.0, min(1.0, (grown - 0.6) / 0.4)))
        self.update()

    def _place(self, anchor: QPoint, lift: bool) -> None:
        """anchor — середина нижнего края капсулы диктовки; lift — уступить ей место."""
        center = QPointF(anchor.x(), anchor.y() - hud.CAPSULE_H / 2)
        if lift:
            up = PLACE.grows_up(center)
            center.setY(center.y() + (-LIFT_OVER_HUD if up else LIFT_OVER_HUD))
        self._place_center(center)

    def _place_center(self, center: QPointF) -> None:
        """Поставить окно так, чтобы центр капсулы (её «корня») был в center."""
        self._down = not PLACE.grows_up(center)
        if self._down:
            top = center.y() - hud.CAPSULE_H / 2 - PAD
        else:
            top = center.y() + hud.CAPSULE_H / 2 - PAD - MAX_H
        self.move(int(round(center.x() - WIDTH / 2)), int(round(top)))
        if self.view == "perm" and self.card.isVisible():
            top = PAD if self._down else PAD + MAX_H - self._target()[1]
            self.card.move(self.card.x(), int(top))

    def _rect(self) -> QRectF:
        w = max(hud.DOT_W, min(MAX_W, self._w))
        h = max(hud.CAPSULE_H, min(MAX_H + 12, self._h))
        return QRectF((WIDTH - w) / 2, PAD if self._down else PAD + MAX_H - h, w, h)

    # ---------------------------------------------------------- рисование
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = self._rect()
        radius = min(22.0, rect.height() / 2)
        p.setPen(Qt.NoPen)
        for step in range(6, 0, -1):
            shadow = QPainterPath()
            shadow.addRoundedRect(rect.adjusted(-step, -step + 3, step, step + 3), radius + step, radius + step)
            p.fillPath(shadow, QColor(0, 0, 0, 9))
        shape = QPainterPath(); shape.addRoundedRect(rect, radius, radius)
        p.fillPath(shape, QColor(0, 0, 0))
        p.setPen(QPen(QColor(255, 255, 255, 22), 1)); p.drawPath(shape)
        p.setClipPath(shape)
        tw, th = self._target()
        ready = min(1.0, max(0.0, (rect.width() - 60) / max(1.0, tw - 60)))
        if ready < 0.3:
            return
        p.setOpacity(ready)
        if self.view == "compact":
            self._paint_compact(p, rect)
        elif self.view == "list":
            self._paint_list(p, rect)
        elif self.view == "done":
            self._paint_done(p, rect)
        elif self.view == "perm":
            self._mark(p, QRectF(rect.left() + 14, rect.top() + 14, 16, 22).center(), "wait")

    def _mark(self, p: QPainter, c, state: str) -> None:
        color = COLORS[state]
        if state == "think":
            pen = QPen(QColor(color.red(), color.green(), color.blue(), 70), 2.2); p.setPen(pen)
            p.setBrush(Qt.NoBrush); p.drawEllipse(c, 6, 6)
            pen.setColor(color); pen.setCapStyle(Qt.RoundCap); p.setPen(pen)
            start = int((time.monotonic() * 2400) % 5760)
            p.drawArc(QRectF(c.x() - 6, c.y() - 6, 12, 12), -start, -1500)
        else:
            p.setPen(Qt.NoPen); p.setBrush(color); p.drawEllipse(c, 4.6, 4.6)

    def _paint_compact(self, p: QPainter, rect: QRectF) -> None:
        sessions = [s for s in self.bridge.snapshot() if s.state != "idle"]
        if not sessions:
            return
        lead = next((s for s in sessions if s.state in ("wait", "ask")), None) or \
            next((s for s in sessions if s.state == "think"), sessions[0])
        cy = rect.center().y()
        self._mark(p, rect.topLeft() + QRectF(0, 0, 40, rect.height()).center(), lead.state)
        waiting = lead.state in ("wait", "ask")
        text = f"{lead.name} " + (t("ждёт тебя") if waiting else t(STATE_TEXT[lead.state]))
        p.setFont(self._font); p.setPen(QColor(255, 255, 255))
        fm = QFontMetricsF(self._font)
        text = fm.elidedText(text, Qt.ElideRight, rect.width() - 150)
        p.drawText(QRectF(rect.left() + 38, rect.top(), 240, rect.height()), Qt.AlignVCenter | Qt.AlignLeft, text)
        if lead.state == "think":
            p.setFont(self._mono); p.setPen(QColor(255, 255, 255, 170))
            p.drawText(QRectF(rect.left() + 42 + fm.horizontalAdvance(text), rect.top(), 60, rect.height()),
                       Qt.AlignVCenter | Qt.AlignLeft, _fmt(time.monotonic() - lead.started))
        x = rect.right() - 18
        for s in reversed(sessions[:6]):
            p.setPen(Qt.NoPen); p.setBrush(COLORS[s.state]); p.drawEllipse(QRectF(x - 8, cy - 4, 8, 8))
            x -= 12

    def _paint_list(self, p: QPainter, rect: QRectF) -> None:
        sessions = [s for s in self.bridge.snapshot() if s.state != "idle"]
        y = rect.top() + 8
        for s in sessions:
            row = QRectF(rect.left() + 8, y, rect.width() - 16, ROW_H)
            self._mark(p, QRectF(row.left() + 6, row.top(), 16, ROW_H).center(), s.state)
            p.setFont(self._font); p.setPen(QColor(255, 255, 255))
            p.drawText(QRectF(row.left() + 30, row.top() + 5, row.width() - 100, 20), Qt.AlignLeft | Qt.AlignVCenter, s.name)
            p.setFont(self._small); p.setPen(QColor(255, 255, 255, 128))
            state = t(STATE_TEXT[s.state])
            detail = f"{state} · {s.action}" if s.action and s.action != state else state
            detail = QFontMetricsF(self._small).elidedText(detail, Qt.ElideRight, row.width() - 100)
            p.drawText(QRectF(row.left() + 30, row.top() + 24, row.width() - 100, 18), Qt.AlignLeft | Qt.AlignVCenter, detail)
            if s.state == "think":
                p.setFont(self._mono); p.setPen(QColor(255, 255, 255, 170))
                p.drawText(QRectF(row.right() - 60, row.top(), 52, ROW_H), Qt.AlignRight | Qt.AlignVCenter,
                           _fmt(time.monotonic() - s.started))
            y += ROW_H

    def _paint_done(self, p: QPainter, rect: QRectF) -> None:
        c = QRectF(rect.left(), rect.top(), 44, rect.height()).center()
        pen = QPen(COLORS["done"], 2.2); pen.setCapStyle(Qt.RoundCap); pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        path = QPainterPath(c + QPointF(-5, 0)); path.lineTo(c.x() - 1.5, c.y() + 3.8); path.lineTo(c.x() + 5.5, c.y() - 3.8)
        p.drawPath(path)
        p.setFont(self._font); p.setPen(QColor(255, 255, 255))
        p.drawText(QRectF(rect.left() + 40, rect.top(), rect.width() - 54, rect.height()),
                   Qt.AlignVCenter | Qt.AlignLeft, self._done_text())

    # ------------------------------------------------------------ ввод
    def mousePressEvent(self, event) -> None:
        self.drag.press(event)

    def mouseMoveEvent(self, event) -> None:
        self.drag.move(event)

    def mouseReleaseEvent(self, event) -> None:
        if self.drag.release(event) or not self._rect().contains(event.position()):
            return                         # перетащили — это не щелчок
        if self.view == "compact":
            self._go("list")
        elif self.view == "list":
            self._go("compact")

    def mouseDoubleClickEvent(self, event) -> None:
        self.drag.double_click(event)

    def keyPressEvent(self, event) -> None:
        if self.view == "perm" and event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._decide(True)
        elif self.view == "perm" and event.key() == Qt.Key_Escape:
            self._decide(False)
        elif self.view == "list" and event.key() == Qt.Key_Escape:
            self._go("compact")
        else:
            super().keyPressEvent(event)
