"""Сборка автономного Shepot.exe — на целевой машине Python не нужен.

    python tools\\build_exe.py

Результат: dist\\Shepot\\Shepot.exe. Папку можно целиком перенести на другой
компьютер. Локальное распознавание в сборку не попадает: faster-whisper тянет
за собой несколько гигабайт моделей и CUDA — если он нужен, ставьте его и
запускайте из исходников.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ARGS = [
    "--noconfirm",
    "--clean",
    "--windowed",                       # без окна консоли
    "--name", "Shepot",
    "--icon", "assets/shepot.ico",
    "--add-data", "assets;assets",
    # pywin32 подтягивается не по прямому импорту
    "--hidden-import", "win32clipboard",
    "--hidden-import", "win32crypt",
    "--hidden-import", "win32timezone",
    "run.pyw",
]

# PyInstaller идёт по цепочке импортов и без этого списка утаскивает в сборку
# половину глобальных site-packages (torch, cv2, polars — 1,4 ГБ и ~35 секунд
# на запуск). Приложение ничего из перечисленного не импортирует: проверяется
# тестом ниже, который поднимает shepot и смотрит sys.modules.
UNUSED = [
    # машинное обучение
    "torch", "torchvision", "torchaudio", "torchgen", "transformers", "tokenizers",
    "safetensors", "accelerate", "datasets", "huggingface_hub", "bitsandbytes",
    "onnxruntime", "faiss", "faiss_cpu", "nltk", "sklearn", "numba", "sympy",
    "faster_whisper", "ctranslate2",
    # данные и графика
    "polars", "pandas", "cv2", "scipy", "matplotlib", "PIL", "networkx", "joblib",
    # веб и ноутбуки
    "gradio", "fastapi", "uvicorn", "starlette", "watchfiles", "fsspec",
    "IPython", "notebook", "jupyter", "jupyterlab", "zmq",
    # прочее ненужное
    "tkinter", "pydoc_data",
    # части Qt, которых нет в интерфейсе
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQuick",
    "PySide6.QtQml", "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtPdf",
]

for _module in UNUSED:
    ARGS[-1:-1] = ["--exclude-module", _module]

VERIFY = """
import sys
sys.path.insert(0, {root!r})
import shepot.__main__, shepot.app, shepot.stt, shepot.polish, shepot.inserter
# именно точное имя: PySide6 импортирован, а PySide6.QtQuick — нет
used = [m for m in {names!r} if m in sys.modules]
print('|'.join(used))
"""


def folder_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def main() -> int:
    if shutil.which("python") is None:
        print("Не найден python в PATH")
        return 1

    print("Проверяю список исключений…")
    probe = subprocess.run(
        [sys.executable, "-c", VERIFY.format(root=str(ROOT), names=UNUSED)],
        cwd=ROOT, capture_output=True, text=True,
    )
    if probe.returncode != 0:
        print("Не удалось импортировать приложение:\n", probe.stderr[-800:])
        return 1
    used = [name for name in probe.stdout.strip().split("|") if name]
    if used:
        print("Эти модули приложению нужны, убираю их из исключений:", ", ".join(used))
        for name in used:
            index = ARGS.index(name)
            del ARGS[index - 1 : index + 1]

    print("Рисую иконку…")
    subprocess.run([sys.executable, str(ROOT / "tools" / "make_icon.py")],
                   cwd=ROOT, check=True)

    print("Собираю…  (первый раз это несколько минут)")
    result = subprocess.run([sys.executable, "-m", "PyInstaller", *ARGS], cwd=ROOT)
    if result.returncode != 0:
        print("PyInstaller завершился с ошибкой")
        return result.returncode

    exe = ROOT / "dist" / "Shepot" / "Shepot.exe"
    if not exe.exists():
        print("Сборка не создала", exe)
        return 1

    size = folder_size(exe.parent) / (1024 * 1024)
    print(f"\nГотово: {exe}  (папка ~{size:.0f} МБ)")
    print("Ярлыки на него:  python tools\\create_shortcuts.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
