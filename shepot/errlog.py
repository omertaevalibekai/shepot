"""Журнал ошибок: %APPDATA%/Shepot/errors.log.

На экране человек видит короткое понятное сообщение, а сюда пишется исходная
ошибка целиком: по ней можно разобрать жалобу «иногда не работает».
Продиктованный текст сюда не попадает, ключи API вырезаются. Файл не растёт
бесконечно: больше MAX_BYTES — уходит в errors.log.1, и пишется заново.
Журнал никогда не роняет приложение: любая ошибка записи молча глотается.
"""

from __future__ import annotations

import re
import threading
import time

from .config import CONFIG_DIR

LOG_PATH = CONFIG_DIR / "errors.log"
MAX_BYTES = 512 * 1024
_lock = threading.Lock()
# sk-..., sk-ant-..., sk-proj-...: оставляем начало, чтобы было видно, какой ключ
_KEY = re.compile(r"(sk-(?:ant-|proj-)?[A-Za-z0-9]{2})[A-Za-z0-9_\-*]{6,}")


def redact(text: str) -> str:
    return _KEY.sub(r"\1…", text)


def record(where: str, message: str) -> None:
    """Одна строка в журнал: время, где случилось, что случилось."""
    try:
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{where}] {redact(' '.join(str(message).split()))[:4000]}\n"
        with _lock:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            if LOG_PATH.exists() and LOG_PATH.stat().st_size > MAX_BYTES:
                LOG_PATH.replace(LOG_PATH.with_name(LOG_PATH.name + ".1"))
            with LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass


def exception(where: str, exc: BaseException) -> None:
    record(where, f"{type(exc).__name__}: {exc}")


def ensure_file():
    """Путь к журналу; файл создаётся, чтобы его можно было открыть из меню."""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_PATH.touch(exist_ok=True)
    except Exception:
        pass
    return LOG_PATH
