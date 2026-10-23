"""Статистика: python -m pytest tests/test_stats.py"""

from datetime import date, timedelta

from shepot import stats

TODAY = date(2026, 10, 23)


def test_record_and_summary(tmp_path):
    s = stats.Stats(tmp_path / "stats.json")
    s.record(120, seconds=40, wait=1.5, app="telegram.exe", when=TODAY)
    s.record(80, seconds=30, wait=0.5, app="chrome.exe", when=TODAY)
    s.record(20, app="telegram.exe", edit=True, when=TODAY)
    summary = s.summary(today=TODAY)
    assert summary["words"] == 220 and summary["words_today"] == 220
    assert summary["dictations"] == 2 and summary["edits"] == 1
    assert summary["avg_wait"] == 1.0
    assert summary["apps"][0] == ("telegram.exe", 140)
    # 220 слов при 40 в минуту = 330 с, минус 70 с диктовки и 2 с ожидания
    assert summary["saved_seconds"] == 330 - 72


def test_survives_restart(tmp_path):
    path = tmp_path / "stats.json"
    stats.Stats(path).record(10, when=TODAY)
    assert stats.Stats(path).summary(today=TODAY)["words"] == 10


def test_streak_counts_consecutive_days(tmp_path):
    s = stats.Stats(tmp_path / "stats.json")
    for back in (1, 2, 3, 5):
        s.record(5, when=TODAY - timedelta(days=back))
    assert s.summary(today=TODAY)["streak"] == 3          # сегодня ещё не диктовал — серия жива
    s.record(5, when=TODAY)
    assert s.summary(today=TODAY)["streak"] == 4


def test_series_has_thirty_days_ending_today(tmp_path):
    s = stats.Stats(tmp_path / "stats.json")
    s.record(7, when=TODAY)
    series = s.summary(today=TODAY)["series"]
    assert len(series) == 30 and series[-1] == (TODAY, 7) and series[0][0] == TODAY - timedelta(days=29)


def test_first_run_is_seeded_from_history(tmp_path):
    items = [
        {"text": "раз два три", "mode": "clean", "seconds": 3, "at": "2026-10-22T10:00:00"},
        {"text": "четыре пять", "mode": "edit", "seconds": 0, "at": "2026-10-23T09:00:00"},
        {"text": "битая", "at": "не дата"},
    ]
    summary = stats.Stats(tmp_path / "stats.json", history_items=items).summary(today=TODAY)
    assert summary["words"] == 5 and summary["dictations"] == 1 and summary["edits"] == 1


def test_empty_text_is_not_counted(tmp_path):
    s = stats.Stats(tmp_path / "stats.json")
    s.record(0, seconds=5, when=TODAY)
    assert s.summary(today=TODAY)["dictations"] == 0


def test_duration_format():
    assert stats.format_duration(59) == "1 мин"
    assert stats.format_duration(3600 * 3 + 20 * 60) == "3 ч 20 мин"
    assert stats.format_duration(7200) == "2 ч"
