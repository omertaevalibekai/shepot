"""Хранение API-ключей.

Ключи лежат в %APPDATA%\\Shepot\\secrets.dat, зашифрованные DPAPI — штатным
механизмом Windows. Расшифровать их может только та же учётная запись на той
же машине, так что файл бесполезно копировать куда-то ещё. В открытом виде
ключ нигде на диск не попадает.

Если ключ не задан в приложении, берётся одноимённая переменная среды —
так продолжают работать те, у кого ключ прописан через setx.
"""

from __future__ import annotations

import base64
import json
import os
import threading

import win32crypt

from .config import CONFIG_DIR

SECRETS_PATH = CONFIG_DIR / "secrets.dat"
DESCRIPTION = "Shepot API keys"

OPENAI = "OPENAI_API_KEY"
ANTHROPIC = "ANTHROPIC_API_KEY"

_lock = threading.RLock()
_cache: dict[str, str] | None = None


def _encrypt(value: str) -> str:
    blob = win32crypt.CryptProtectData(
        value.encode("utf-8"), DESCRIPTION, None, None, None, 0
    )
    return base64.b64encode(blob).decode("ascii")


def _decrypt(payload: str) -> str | None:
    try:
        blob = base64.b64decode(payload.encode("ascii"))
        _description, data = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
        return data.decode("utf-8")
    except Exception:
        # чужой профиль, другая машина или повреждённый файл
        return None


def _load() -> dict[str, str]:
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
        _cache = {}
        if SECRETS_PATH.exists():
            try:
                stored = json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                stored = {}
            for name, payload in stored.items():
                value = _decrypt(payload) if isinstance(payload, str) else None
                if value:
                    _cache[name] = value
        return _cache


def get(name: str) -> str:
    """Ключ из приложения, иначе из переменной среды, иначе пустая строка."""
    stored = _load().get(name, "").strip()
    return stored or os.environ.get(name, "").strip()


def stored(name: str) -> str:
    """Только ключ, сохранённый в приложении (без переменных среды)."""
    return _load().get(name, "")


def is_from_environment(name: str) -> bool:
    """True, если ключ берётся из переменной среды, а не сохранён в приложении."""
    return not _load().get(name, "").strip() and bool(os.environ.get(name, "").strip())


def set_key(name: str, value: str) -> None:
    """Сохраняет ключ; пустая строка удаляет сохранённый."""
    value = (value or "").strip()
    with _lock:
        cache = _load()
        if value:
            cache[name] = value
        else:
            cache.pop(name, None)
        _write(cache)


def _write(cache: dict[str, str]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    payload = {name: _encrypt(value) for name, value in cache.items()}
    tmp = SECRETS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    tmp.replace(SECRETS_PATH)


def masked(name: str) -> str:
    """Ключ для показа в интерфейсе: sk-...abcd."""
    value = get(name)
    if not value:
        return ""
    if len(value) <= 10:
        return "•" * len(value)
    return f"{value[:5]}…{value[-4:]}"


def invalidate() -> None:
    """Сбрасывает кэш — нужно после правки файла извне."""
    global _cache
    with _lock:
        _cache = None
