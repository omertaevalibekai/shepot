"""Хранение настроек приложения в %APPDATA%\\Shepot\\config.json."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

APP_NAME = "Shepot"

CONFIG_DIR = Path(os.environ.get("APPDATA") or Path.home()) / APP_NAME
CONFIG_PATH = CONFIG_DIR / "config.json"
HISTORY_PATH = CONFIG_DIR / "history.json"

MODES = ("raw", "clean", "translate")
TONES = ("preserve", "neutral", "casual", "formal")

DEFAULTS: dict = {
    # --- горячие клавиши ---
    "hotkey": "ctrl+space",
    "hotkey_mode": "toggle",       # toggle | ptt (push-to-talk) | pair (старт и стоп разными)
    "hotkey_stop": "",             # клавиша остановки для режима pair
    "cancel_key": "esc",
    # --- обработка ---
    "mode": "clean",               # raw | clean | translate
    "tone": "preserve",            # preserve | neutral | casual | formal
    "source_language": "auto",     # auto | ru | en | kk | ...
    "target_language": "en",       # куда переводить в режиме translate
    "vocabulary": [],              # имена, бренды, термины
    "latin_terms": True,           # английские слова в русской речи — латиницей
    "learn_corrections": True,     # исправил термин после вставки → он попадает в словарь
    "live_transcribe": True,       # длинная диктовка распознаётся кусками, пока человек говорит
    "window_context": False,       # имена и текст из окна — подсказка распознаванию и чистке; текст уходит в API, поэтому только по желанию
    "replacements": {},            # выученное «как распознал → как надо» (learn.py)
    "custom_instruction": "",      # своя добавка к промпту
    # --- распознавание речи ---
    "stt_backend": "openai",       # openai | local
    "stt_model": "gpt-4o-transcribe",
    "local_model": "small",        # tiny | base | small | medium | large-v3
    "local_device": "auto",        # auto | cuda | cpu
    # --- постобработка текстом ---
    "llm_provider": "openai",      # openai | anthropic | none
    "llm_model": "gpt-4o-mini",
    "anthropic_model": "claude-opus-5",
    # --- запись ---
    "input_device": None,          # индекс устройства PyAudio, None = по умолчанию
    "max_seconds": 120,
    "min_seconds": 0.35,
    # --- вставка ---
    "insert_method": "paste",      # paste (Ctrl+V) | type (SendInput) | clipboard
    "restore_clipboard": True,
    "restore_delay": 0.6,          # через сколько вернуть прежний буфер
    # --- голосовая правка ---
    "voice_edit": True,            # есть выделение → речь считается командой правки
    # --- Claude Code ---
    "claude_island": True,         # показывать сессии Claude Code в острове (нужны хуки)
    "claude_reply": True,          # своя клавиша — голосовой ответ сессии Claude Code
    "claude_reply_hotkey": "ctrl+alt+space",
    # --- окна и проекты ---
    "ghost": True,                 # Alt + колесо над окном — прозрачность
    "jump": True,                  # горячая клавиша — поиск проекта и его рабочее место
    "jump_hotkey": "win+j",
    "jump_root": "",               # пусто = Рабочий стол\AI Projects
    "jump_recent": {},             # путь проекта → когда открывали прыжком
    "jump_pinned": [],             # закреплённые проекты — всегда сверху
    "jump_options": {},            # путь проекта → что открывать (галочки)
    # --- скриншоты в терминал ---
    "clipshot": False,             # картинка в буфере → PNG на диске + путь в буфере
    "clipshot_dir": "",            # пусто = %TEMP%\claude-shots
    "clipshot_keep_days": 7,       # через сколько дней убирать старые снимки
    # --- интерфейс ---
    "hud": True,
    "island_place": {},            # куда перетащили остров: монитор → доли рабочей области
    "play_sounds": True,
    "history_limit": 200,
    "autostart": False,
    "ui_language": "auto",         # язык интерфейса: auto (как в Windows) | ru | en
    "check_updates": True,         # раз в сутки спрашивать GitHub о новой версии
    "update_notified": "",         # о какой версии уже сказали всплывашкой
}


class Config:
    """Потокобезопасная обёртка над JSON-файлом настроек."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._data = dict(DEFAULTS)
        self.load()

    # ------------------------------------------------------------------ io
    def load(self) -> None:
        with self._lock:
            if CONFIG_PATH.exists():
                try:
                    stored = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    stored = {}
                # неизвестные ключи отбрасываем, недостающие берём из DEFAULTS
                for key in DEFAULTS:
                    if key in stored:
                        self._data[key] = stored[key]

    def save(self) -> None:
        with self._lock:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            tmp = CONFIG_PATH.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(CONFIG_PATH)

    # --------------------------------------------------------------- access
    def get(self, key: str, default=None):
        with self._lock:
            return self._data.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value) -> None:
        with self._lock:
            self._data[key] = value

    def update(self, values: dict) -> None:
        with self._lock:
            self._data.update(values)

    def as_dict(self) -> dict:
        with self._lock:
            return dict(self._data)

    def __getitem__(self, key: str):
        return self.get(key)
