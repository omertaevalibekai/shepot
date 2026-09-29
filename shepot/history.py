"""История диктовок — чтобы вернуть текст, который не вставился."""

from __future__ import annotations

import json
import threading
from datetime import datetime

from .config import CONFIG_DIR, HISTORY_PATH


class History:
    def __init__(self, limit: int = 200) -> None:
        self._lock = threading.Lock()
        self._limit = limit
        self._items: list[dict] = []
        self._load()

    def _load(self) -> None:
        if not HISTORY_PATH.exists():
            return
        try:
            data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        if isinstance(data, list):
            self._items = [i for i in data if isinstance(i, dict)]

    def _save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        try:
            HISTORY_PATH.write_text(
                json.dumps(self._items, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
        except OSError:
            pass

    def add(self, text: str, mode: str, seconds: float) -> None:
        if not text.strip():
            return
        with self._lock:
            self._items.insert(
                0,
                {
                    "text": text,
                    "mode": mode,
                    "seconds": round(seconds, 1),
                    "at": datetime.now().isoformat(timespec="seconds"),
                },
            )
            del self._items[self._limit :]
            self._save()

    def items(self) -> list[dict]:
        with self._lock:
            return list(self._items)

    def clear(self) -> None:
        with self._lock:
            self._items = []
            self._save()
