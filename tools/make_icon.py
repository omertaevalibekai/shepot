"""Генерирует assets/shepot.ico и assets/shepot.png для ярлыков и .exe.

Рисунок общий с треем и настройками: shepot/logo.py (волна голоса и курсор на чёрном).
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shepot import logo  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
SIZES = [16, 24, 32, 48, 64, 128, 256]


def render(size: int) -> QImage:
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    logo.draw(painter, QRectF(0, 0, size, size))
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
