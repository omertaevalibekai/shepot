"""Замечания ревью: словарь, снимок буфера, микрофон без лишней переинициализации."""
from shepot import audio, inserter, learn


def test_newline_counts_as_sentence_start():
    assert learn._sentence_starts("Список\nпотом сделать") == [True, True, False]


def test_learned_cyrillic_name_still_applies():
    assert learn.apply("позвони алибеку завтра", {"алибеку": "Алибеку"}) == "позвони Алибеку завтра"


def test_snapshot_keeps_only_useful_formats(monkeypatch):
    names = {0xC001: "HTML Format", 0xC002: "Biff12", 0xC003: "PNG"}
    monkeypatch.setattr(inserter.win32clipboard, "GetClipboardFormatName", lambda f: names[f])
    assert inserter._is_copyable(13) and inserter._is_copyable(15) and inserter._is_copyable(8)
    assert not inserter._is_copyable(1) and not inserter._is_copyable(2)
    assert inserter._is_copyable(0xC001) and inserter._is_copyable(0xC003)
    assert not inserter._is_copyable(0xC002)


class _FakeStream:
    def read(self, *_a, **_k):
        import time
        time.sleep(0.01)
        return b"\0\0" * audio.CHUNK

    def stop_stream(self): pass
    def close(self): pass


class _FakePA:
    created = 0

    def __init__(self):
        _FakePA.created += 1

    def terminate(self): pass
    def open(self, **_k): return _FakeStream()


def test_start_does_not_reinit_pyaudio_for_known_device(monkeypatch):
    monkeypatch.setattr(audio.pyaudio, "PyAudio", _FakePA)
    monkeypatch.setattr(audio, "_input_devices", lambda pa: [(1, "USB mic")])
    rec = audio.Recorder()
    before = _FakePA.created
    assert rec.start(device_index="USB mic", max_seconds=5)
    rec.cancel()
    assert _FakePA.created == before          # известный микрофон: PyAudio не пересоздаём
    assert rec.start(device_index="Новая гарнитура", max_seconds=5)
    rec.cancel()
    assert _FakePA.created == before + 1      # незнакомое имя: пересканировали устройства
