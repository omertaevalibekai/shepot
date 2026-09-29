"""Создаёт ярлыки «Шёпот» на рабочем столе и в меню «Пуск».

Если рядом лежит собранный dist\\Shepot\\Shepot.exe — ярлык ведёт на него.
Иначе на pythonw.exe с run.pyw: приложение запускается без окна консоли.

    python tools\\create_shortcuts.py            создать
    python tools\\create_shortcuts.py --source   создать на исходники, даже если есть .exe
    python tools\\create_shortcuts.py --remove   удалить
"""

from __future__ import annotations

import sys
from pathlib import Path

import win32com.client

ROOT = Path(__file__).resolve().parent.parent
EXE = ROOT / "dist" / "Shepot" / "Shepot.exe"
LAUNCHER = ROOT / "run.pyw"
ICON = ROOT / "assets" / "shepot.ico"
NAME = "Шёпот.lnk"


def _target(prefer_source: bool = False) -> tuple[str, str, str]:
    """Возвращает (программа, аргументы, путь к иконке)."""
    if EXE.exists() and not prefer_source:
        return str(EXE), "", str(EXE)

    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    if not pythonw.exists():
        pythonw = exe
    icon = str(ICON) if ICON.exists() else str(pythonw)
    return str(pythonw), f'"{LAUNCHER}"', icon


def _locations() -> list[Path]:
    shell = win32com.client.Dispatch("WScript.Shell")
    desktop = Path(shell.SpecialFolders("Desktop"))
    programs = Path(shell.SpecialFolders("Programs"))
    return [desktop / NAME, programs / NAME]


def create(prefer_source: bool = False) -> None:
    program, arguments, icon = _target(prefer_source)
    shell = win32com.client.Dispatch("WScript.Shell")
    for path in _locations():
        path.parent.mkdir(parents=True, exist_ok=True)
        link = shell.CreateShortCut(str(path))
        link.TargetPath = program
        link.Arguments = arguments
        link.WorkingDirectory = str(ROOT)
        link.IconLocation = icon
        link.Description = "Голосовой ввод: Ctrl+Space — диктовка в любое окно"
        link.Save()
        print("создан:", path)
    print("\nЗапуск:", program, arguments)


def remove() -> None:
    for path in _locations():
        if path.exists():
            path.unlink()
            print("удалён:", path)


if __name__ == "__main__":
    if "--remove" in sys.argv:
        remove()
    else:
        create(prefer_source="--source" in sys.argv)
