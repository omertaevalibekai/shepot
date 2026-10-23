"""Статистика: сколько надиктовано и сколько времени это сэкономило.

Хранится отдельно от истории (та помнит только последние записи): счётчики по
дням и по приложениям, без самого текста. При первом запуске счётчики
заполняются из истории, чтобы статистика не начиналась с нуля.

«Сэкономлено» — оценка: время набора тех же слов со скоростью TYPING_WPM минус
время, потраченное на диктовку и обработку. 40 слов в минуту — наше допущение
(средняя скорость набора на клавиатуре), его видно в окне статистики.
"""

from __future__ import annotations

import json
import threading
from datetime import date, datetime, timedelta

from .config import CONFIG_DIR
from .i18n import t

STATS_PATH = CONFIG_DIR / "stats.json"
TYPING_WPM = 40
KEEP_DAYS = 400


def count_words(text: str) -> int:
    return len((text or "").split())


def _empty_day() -> dict:
    return {"words": 0, "dictations": 0, "edits": 0, "seconds": 0.0,
            "wait": 0.0, "waits": 0}


class Stats:
    def __init__(self, path=STATS_PATH, history_items: list[dict] | None = None) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._data = {"days": {}, "apps": {}}
        if path.exists():
            self._load()
        elif history_items:
            self._seed(history_items)
            self._save()

    # ----------------------------------------------------------- хранение
    def _load(self) -> None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(data, dict):
            self._data["days"] = dict(data.get("days") or {})
            self._data["apps"] = dict(data.get("apps") or {})

    def _save(self) -> None:
        cutoff = (date.today() - timedelta(days=KEEP_DAYS)).isoformat()
        self._data["days"] = {d: v for d, v in self._data["days"].items() if d >= cutoff}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._data, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
        except OSError:
            pass

    def _seed(self, items: list[dict]) -> None:
        for item in items:
            try:
                day = datetime.fromisoformat(item.get("at", "")).date().isoformat()
            except ValueError:
                continue
            entry = self._data["days"].setdefault(day, _empty_day())
            entry["words"] += count_words(item.get("text", ""))
            if item.get("mode") == "edit":
                entry["edits"] += 1
            else:
                entry["dictations"] += 1
                entry["seconds"] += float(item.get("seconds") or 0)

    # ------------------------------------------------------------- запись
    def record(self, words: int, seconds: float = 0.0, wait: float | None = None,
               app: str = "", edit: bool = False, when: date | None = None) -> None:
        """Одна доставленная диктовка (или правка). wait — от отпускания клавиши до вставки."""
        if words <= 0:
            return
        day = (when or date.today()).isoformat()
        with self._lock:
            entry = self._data["days"].setdefault(day, _empty_day())
            entry["words"] += words
            entry["edits" if edit else "dictations"] += 1
            entry["seconds"] += round(seconds, 1)
            if wait is not None and not edit:
                entry["wait"] += round(wait, 2)
                entry["waits"] += 1
            if app:
                self._data["apps"][app] = self._data["apps"].get(app, 0) + words
            self._save()

    # ------------------------------------------------------------- сводка
    def summary(self, today: date | None = None, days: int = 30) -> dict:
        today = today or date.today()
        with self._lock:
            all_days = {d: dict(v) for d, v in self._data["days"].items()}
            apps = dict(self._data["apps"])

        def total(key: str, since: date | None = None) -> float:
            return sum(v.get(key, 0) for d, v in all_days.items()
                       if since is None or d >= since.isoformat())

        words = int(total("words"))
        spoken = total("seconds")
        waits, wait_sum = total("waits"), total("wait")
        week_start = today - timedelta(days=6)

        series = []
        for offset in range(days - 1, -1, -1):
            day = today - timedelta(days=offset)
            series.append((day, int(all_days.get(day.isoformat(), {}).get("words", 0))))

        streak, day = 0, today
        if not all_days.get(day.isoformat(), {}).get("words"):
            day -= timedelta(days=1)                   # сегодня ещё не диктовал — серия не прервана
        while all_days.get(day.isoformat(), {}).get("words"):
            streak += 1
            day -= timedelta(days=1)

        top_apps = sorted(apps.items(), key=lambda p: -p[1])[:5]
        return {
            "words": words,
            "words_today": int(all_days.get(today.isoformat(), {}).get("words", 0)),
            "words_week": int(total("words", week_start)),
            "dictations": int(total("dictations")),
            "edits": int(total("edits")),
            "saved_seconds": max(0.0, saved_seconds(words, spoken + wait_sum)),
            "avg_wait": wait_sum / waits if waits else None,
            "streak": streak,
            "series": series,
            "apps": top_apps,
        }


def saved_seconds(words: int, spent: float) -> float:
    """Время набора этих слов минус время на диктовку и ожидание."""
    return words / TYPING_WPM * 60 - spent


def format_duration(seconds: float) -> str:
    minutes = int(round(seconds / 60))
    if minutes < 60:
        return t("{m} мин", m=minutes)
    hours, minutes = divmod(minutes, 60)
    return t("{h} ч {m:02d} мин", h=hours, m=minutes) if minutes else t("{h} ч", h=hours)
