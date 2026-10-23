"""Знак Шёпота: светлая волна голоса на чёрном переходит в текстовый курсор.

Один рисунок на всё: иконка приложения (tools/make_icon.py), значок в трее
(там курсор окрашен по состоянию: ждёт, пишет, обрабатывает) и шапка
настроек. Координаты — в квадрате 100×100, как в макете.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QRadialGradient

INK = QColor(245, 245, 247)
CARET = QColor(76, 155, 255)
# (x, y, высота) столбиков волны; ширина у всех 6,5, курсор — последним
BARS = ((14, 42, 16), (25, 35, 30), (36, 27, 46), (47, 34, 32), (58, 41, 18))
CURSOR = (75, 23, 54)
BAR_W = 6.5


def draw(painter: QPainter, rect: QRectF, caret: QColor = CARET, ground: bool = True) -> None:
    """Рисует знак в rect (квадрат). ground=False — без чёрной подложки."""
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    size = min(rect.width(), rect.height())
    k = size / 100.0
    ox, oy = rect.left() + (rect.width() - size) / 2, rect.top() + (rect.height() - size) / 2

    if ground:
        path = QPainterPath()
        path.addRoundedRect(QRectF(ox, oy, size, size), 23.5 * k, 23.5 * k)
        glow = QRadialGradient(QPointF(ox + 30 * k, oy), 110 * k)
        glow.setColorAt(0.0, QColor(38, 38, 44))
        glow.setColorAt(0.62, QColor(11, 11, 15))
        glow.setColorAt(1.0, QColor(11, 11, 15))
        painter.fillPath(path, glow)

    painter.setPen(Qt.NoPen)

    def bar(x: float, y: float, h: float, color: QColor) -> None:
        painter.setBrush(color)
        painter.drawRoundedRect(QRectF(ox + x * k, oy + y * k, BAR_W * k, h * k),
                                BAR_W / 2 * k, BAR_W / 2 * k)

    for x, y, h in BARS:
        bar(x, y, h, INK)
    bar(*CURSOR, caret)
    painter.restore()
