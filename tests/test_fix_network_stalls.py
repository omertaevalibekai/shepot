"""Зависшая сеть, сырые ошибки API и журнал ошибок (стресс-тест 2026-11-26).

Контроллер настоящий; микрофон, вставка, горячие клавиши и сеть подменены,
поэтому тест не трогает клавиатуру, буфер и реальные окна.
"""

import os
import struct
import threading
import time
from types import SimpleNamespace

import pytest

from shepot import errlog, polish, providers, stt


# ------------------------------------------------------- понятные ошибки
class _ApiError(Exception):
    def __init__(self, status, body=None):
        super().__init__(f"Error code: {status} - {body!r}")
        self.status_code, self.body = status, body


class APITimeoutError(Exception):
    pass


class APIConnectionError(Exception):
    pass


@pytest.mark.parametrize("exc, expected", [
    (_ApiError(429, {"error": {"message": "x", "code": "insufficient_quota"}}), "Баланс API исчерпан"),
    (_ApiError(429, {"error": {"message": "Rate limit reached", "code": "rate_limit_exceeded"}}), "Слишком много"),
    (_ApiError(401, {"error": {"message": "Incorrect API key"}}), "Ключ не принят"),
    (_ApiError(503, {"error": {"message": "overloaded"}}), "код 503"),
    (_ApiError(400, {"error": {"message": "Audio file is too short"}}), "Audio file is too short"),
    (APITimeoutError("Request timed out."), "не ответил вовремя"),
    (APIConnectionError("Connection error."), "Нет связи"),
])
def test_humanize_has_no_raw_json(exc, expected):
    message = providers.humanize(exc)
    assert expected in message
    assert "{'error'" not in message and "Error code" not in message


def _client_raising(exc):
    def create(**_kwargs):
        raise exc
    return SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)),
                           chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def test_stt_and_polish_show_human_message_and_log_raw(monkeypatch, tmp_path):
    monkeypatch.setattr(errlog, "LOG_PATH", tmp_path / "errors.log")
    exc = _ApiError(429, {"error": {"message": "You exceeded your current quota", "code": "insufficient_quota"}})
    monkeypatch.setattr(stt, "openai_client", lambda: _client_raising(exc))
    monkeypatch.setattr(polish, "openai_client", lambda: _client_raising(exc))
    with pytest.raises(stt.SttError) as stt_err:
        stt._transcribe_openai(b"RIFF", "m", None, "")
    with pytest.raises(polish.PolishError) as pol_err:
        polish._openai(SimpleNamespace(get=lambda k, d=None: "m"), "sys", "text")
    for err in (stt_err, pol_err):
        assert "Баланс API исчерпан" in str(err.value) and "{'error'" not in str(err.value)
    log = (tmp_path / "errors.log").read_text(encoding="utf-8")
    assert "[stt]" in log and "[polish]" in log and "insufficient_quota" in log   # исходник — в журнале


# --------------------------------------------------------------- журнал
def test_errlog_redacts_keys_and_rotates(monkeypatch, tmp_path):
    path = tmp_path / "errors.log"
    monkeypatch.setattr(errlog, "LOG_PATH", path)
    monkeypatch.setattr(errlog, "MAX_BYTES", 300)
    errlog.record("stt", "Incorrect API key provided: sk-proj-abcdEFGH1234567890xyz")
    text = path.read_text(encoding="utf-8")
    assert "abcdEFGH1234567890xyz" not in text and "sk-proj-ab" in text
    for _ in range(20):
        errlog.record("x", "y" * 50)
    assert path.with_name("errors.log.1").exists() and path.stat().st_size < 1000


def test_errlog_never_raises(monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setattr(errlog, "LOG_PATH", blocker / "sub" / "errors.log")   # папка — это файл
    errlog.record("x", "y")                                                     # не падает


# ------------------------------------------------ дедлайн и Esc в обработке
@pytest.fixture
def controller(monkeypatch, tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from shepot import app as A, claude_bridge, config, history, inserter, jump, stats

    monkeypatch.setattr(errlog, "LOG_PATH", tmp_path / "errors.log")
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(history, "HISTORY_PATH", tmp_path / "history.json")
    monkeypatch.setattr(stats, "STATS_PATH", tmp_path / "stats.json")
    monkeypatch.setattr(stats.Stats.__init__, "__defaults__", (tmp_path / "stats.json", None))

    class FakeKeys:
        def __init__(self, *a, **k):
            pass

        def __getattr__(self, _name):
            return lambda *a, **k: True

    class FakeRecorder:
        def __init__(self, **_k):
            self.is_recording, self.error, self.on_block = False, None, None

        def start(self, **_k):
            self.is_recording = True
            return True

        def stop(self):
            self.is_recording = False
            n = 16000 * 2
            return b"RIFF" + b"\0" * 40 + struct.pack("<h", 1) + b"\0" * (n * 2 - 2)

        def cancel(self):
            self.is_recording = False

        def close(self):
            pass

    monkeypatch.setattr(A, "HotkeyManager", FakeKeys)
    monkeypatch.setattr(A.audio, "Recorder", FakeRecorder)
    monkeypatch.setattr(jump, "warm_tools", lambda: None)
    monkeypatch.setattr(providers, "warm", lambda cfg: None)
    monkeypatch.setattr(providers, "missing_key", lambda cfg: None)

    def no_network():
        raise providers.ProviderError("в тестах сети нет")
    monkeypatch.setattr(providers, "openai_client", no_network)       # ключ из окружения не уходит в сеть
    monkeypatch.setattr(providers, "anthropic_client", no_network)
    monkeypatch.setattr(stt, "openai_client", no_network)
    monkeypatch.setattr(polish, "openai_client", no_network)
    monkeypatch.setattr(polish, "anthropic_client", no_network)
    start = claude_bridge.Bridge.start
    monkeypatch.setattr(claude_bridge.Bridge, "start", lambda self, port=0: start(self, 0))
    inserted = []
    monkeypatch.setattr(inserter, "insert", lambda text, **k: inserted.append(text) or True)

    from shepot import i18n
    monkeypatch.setattr(i18n, "system_language", lambda: "en")   # как на раннере CI: Windows по-английски
    qapp = QApplication.instance() or QApplication([])
    c = A.Controller(qapp)
    i18n.set_language("ru")                                       # тексты острова проверяем по-русски
    for key, value in {"play_sounds": False, "voice_edit": False, "live_transcribe": False,
                       "learn_corrections": False, "window_context": False, "hud": True, "mode": "raw"}.items():
        c.cfg._data[key] = value
    shown = []
    original = c.hud.show_state
    c.hud.show_state = lambda state, message="", *a, **k: (shown.append((state, message)), original(state, message, *a, **k))[1]
    c._test = SimpleNamespace(inserted=inserted, shown=shown, qapp=qapp, A=A)
    yield c
    c.claude_bridge.stop()
    i18n.set_language("ru")


def _run_until(c, done, limit=10.0):
    t0 = time.monotonic()
    while not done() and time.monotonic() - t0 < limit:
        c._test.qapp.processEvents()
        time.sleep(0.01)
    c._test.qapp.processEvents()
    for th in threading.enumerate():
        if th.name == "shepot-insert":
            th.join(2)
    c._test.qapp.processEvents()


def _dictate(c):
    c.requestStart.emit()
    c._test.qapp.processEvents()
    c.requestFinish.emit()
    c._test.qapp.processEvents()


def test_hung_recognition_gives_up_at_deadline(controller, monkeypatch):
    c, A = controller, controller._test.A
    release = threading.Event()
    monkeypatch.setattr(A.stt, "transcribe", lambda *a, **k: release.wait(30) and "поздно")
    monkeypatch.setattr(A, "PROCESS_BASE_S", 0.3)
    monkeypatch.setattr(A, "PROCESS_PER_AUDIO_S", 0.0)
    t0 = time.monotonic()
    _dictate(c)
    _run_until(c, lambda: not c._busy)
    assert time.monotonic() - t0 < 3, "зависший сервер держит Шёпот дольше дедлайна"
    assert any(s == "error" and "не ответил" in m for s, m in c._test.shown)
    release.set()                                   # поздний ответ не вставляется
    _run_until(c, lambda: any(i["text"] == "поздно" for i in c.history.items()), 3)
    assert c._test.inserted == []
    assert c.history.items()[0]["text"] == "поздно"     # но и не теряется


def test_hung_polish_inserts_raw_text_at_deadline(controller, monkeypatch):
    c, A = controller, controller._test.A
    release = threading.Event()
    monkeypatch.setattr(A.stt, "transcribe", lambda *a, **k: "сырой текст")
    monkeypatch.setattr(A.polish, "polish", lambda *a, **k: release.wait(30) and "чистый")
    c.cfg._data["mode"] = "clean"
    monkeypatch.setattr(A, "PROCESS_BASE_S", 0.3)
    monkeypatch.setattr(A, "PROCESS_PER_AUDIO_S", 0.0)
    _dictate(c)
    _run_until(c, lambda: bool(c._test.inserted))
    assert c._test.inserted == ["сырой текст"]
    assert any(s == "error" and "Чистка" in m for s, m in c._test.shown)
    release.set()
    time.sleep(0.2)
    _run_until(c, lambda: False, 0.3)
    assert c._test.inserted == ["сырой текст"]          # поздняя чистка не вставляется второй раз


def test_esc_during_processing_frees_the_hotkey(controller, monkeypatch):
    c, A = controller, controller._test.A
    release = threading.Event()
    monkeypatch.setattr(A.stt, "transcribe", lambda *a, **k: release.wait(30) and "первая")
    _dictate(c)
    assert c._busy
    c.requestCancel.emit()                              # Esc во время обработки
    c._test.qapp.processEvents()
    assert not c._busy
    assert any(m == "Отменено" for _s, m in c._test.shown)
    monkeypatch.setattr(A.stt, "transcribe", lambda *a, **k: "вторая")
    _dictate(c)                                         # новая диктовка не получает «Ещё обрабатываю»
    _run_until(c, lambda: bool(c._test.inserted))
    release.set()
    _run_until(c, lambda: False, 0.3)
    assert c._test.inserted == ["вторая"]
    assert not any("Ещё обрабатываю" in m for _s, m in c._test.shown)


def test_normal_dictation_still_inserts(controller, monkeypatch):
    c, A = controller, controller._test.A
    monkeypatch.setattr(A.stt, "transcribe", lambda *a, **k: "привет")
    monkeypatch.setattr(A.polish, "polish", lambda *a, **k: "Привет.")
    _dictate(c)
    _run_until(c, lambda: bool(c._test.inserted))
    assert c._test.inserted == ["Привет."] and not c._busy
