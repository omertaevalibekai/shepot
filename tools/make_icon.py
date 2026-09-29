"""Генерирует assets/shepot.ico и assets/shepot.png для ярлыков и .exe.

Иконка приложения отличается от значка в трее: у неё есть подложка, иначе
в панели задач и в меню «Пуск» тонкий микрофон теряется.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPen,
)

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
SIZES = [16, 24, 32, 48, 64, 128, 256]


def render(size: int) -> QImage:
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)

    k = size / 256.0

    gradient = QLinearGradient(0, 0, 0, size)
    gradient.setColorAt(0.0, QColor(46, 124, 255))
    gradient.setColorAt(1.0, QColor(24, 60, 140))
    painter.setPen(Qt.NoPen)
    painter.setBrush(gradient)
    painter.drawRoundedRect(QRectF(0, 0, size, size), 56 * k, 56 * k)

    white = QColor(255, 255, 255)
    painter.setBrush(white)
    painter.drawRoundedRect(QRectF(106 * k, 54 * k, 44 * k, 96 * k), 22 * k, 22 * k)

    pen = QPen(white, 14 * k)
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawArc(QRectF(80 * k, 84 * k, 96 * k, 96 * k), 180 * 16, 180 * 16)
    painter.drawLine(QPointF(128 * k, 180 * k), QPointF(128 * k, 208 * k))
    painter.end()
    return image


def main() -> int:
    QGuiApplication(sys.argv)
    ASSETS.mkdir(exist_ok=True)

    png = ASSETS / "shepot.png"
    render(256).save(str(png))

    from PIL import Image

    Image.open(png).convert("RGBA").save(
        str(ASSETS / "shepot.ico"), format="ICO", sizes=[(s, s) for s in SIZES]
    )
    print("готово:", ASSETS / "shepot.ico", "и", png)
    return 0


if __name__ == "__main__":
    sys.exit(main())
