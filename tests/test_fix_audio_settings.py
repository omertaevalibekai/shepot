"""Список микрофонов, выбор по имени и проверка клавиши ответа (без железа)."""
from shepot import audio


class FakePA:
    def __init__(self, devs, default_api=0):
        self.devs, self.api = devs, default_api

    def get_default_host_api_info(self):
        return {"index": self.api}

    def get_device_count(self):
        return len(self.devs)

    def get_device_info_by_index(self, i):
        return self.devs[i]


DEVS = [
    {"name": "Mic A", "maxInputChannels": 2, "hostApi": 0},
    {"name": "Mic A", "maxInputChannels": 2, "hostApi": 1},
    {"name": "Out", "maxInputChannels": 0, "hostApi": 0},
    {"name": "Mic B", "maxInputChannels": 1, "hostApi": 0},
    {"name": "Mic B", "maxInputChannels": 1, "hostApi": 2},
]


def test_one_host_api_no_duplicates():
    assert audio._input_devices(FakePA(DEVS)) == [(0, "Mic A"), (3, "Mic B")]


def test_resolve_by_name_and_fallback():
    devices = audio._input_devices(FakePA(DEVS))
    assert audio.resolve_device(devices, "Mic B") == 3
    assert audio.resolve_device(devices, "Gone") is None
    assert audio.resolve_device(devices, 7) is None     # чужой индекс — не берём
    assert audio.resolve_device(devices, 0) == 0
    assert audio.resolve_device(devices, None) is None


def test_start_returns_false_on_open_error(monkeypatch):
    class PA(FakePA):
        def open(self, **kw):
            raise OSError("нет микрофона")

        def terminate(self):
            pass

    monkeypatch.setattr(audio.pyaudio, "PyAudio", lambda: PA(DEVS))
    rec = audio.Recorder()
    assert rec.start("Mic A") is False
    assert "нет микрофона" in rec.error
    assert not rec.is_recording
