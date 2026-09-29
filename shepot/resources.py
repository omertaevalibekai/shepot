"""Поиск файлов из assets/ — одинаково для запуска из исходников и из .exe."""

from __future__ import annotations

import sys
from pathlib import Path


def base_dir() -> Path:
    if getattr(sys, "frozen", False):
        # PyInstaller распаковывает данные во временную папку _MEIPASS
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def asset(name: str) -> Path | None:
    path = base_dir() / "assets" / name
    return path if path.exists() else None


def app_icon() -> Path | None:
    return asset("shepot.ico") or asset("shepot.png")
