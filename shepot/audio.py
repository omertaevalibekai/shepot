"""Запись с микрофона в WAV-байты (16 кГц, моно, 16 бит)."""

from __future__ import annotations

import io
import threading
import wave

import numpy as np
import pyaudio

from .i18n import t

RATE = 16_000
CHANNELS = 1
SAMPLE_WIDTH = 2          # 16 бит
CHUNK = 1024              # ~64 мс — достаточно частые обновления уровня
FORMAT = pyaudio.paInt16


def _input_devices(pa) -> list[tuple[int, str]]:
    """Микрофоны одного host API (по умолчанию у PortAudio, в Windows это MME), без дублей по имени."""
    try:
        api = int(pa.get_default_host_api_info().get("index", -1))
    except Exception:
        api = -1
    devices: list[tuple[int, str]] = []
    seen: set[str] = set()
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if int(info.get("maxInputChannels", 0)) <= 0:
            continue
        if api >= 0 and int(info.get("hostApi", api)) != api:
            continue
        name = str(info.get("name", t("Устройство {n}", n=i)))
        if name in seen:
            continue
        seen.add(name)
        devices.append((i, name))
    return devices


def list_input_devices() -> list[tuple[int, str]]:
    """Список доступных микрофонов: [(индекс, название), ...]."""
    pa = pyaudio.PyAudio()
    try:
        return _input_devices(pa)
    finally:
        pa.terminate()


def resolve_device(devices: list[tuple[int, str]], spec) -> int | None:
    """Имя (или старый индекс) → индекс в текущем списке; не нашли — None, то есть микрофон по умолчанию."""
    if spec is None or spec == "":
        return None
    if isinstance(spec, str):
        return next((i for i, name in devices if name == spec), None)
    if isinstance(spec, int) and not isinstance(spec, bool):
        return spec if any(i == spec for i, _ in devices) else None
    return None


def encode_wav(frames: list[bytes]) -> bytes:
    """Склеивает PCM-блоки в WAV-контейнер."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(CHANNELS)
        wav.setsampwidth(SAMPLE_WIDTH)
        wav.setframerate(RATE)
        wav.writeframes(b"".join(frames))
    return buf.getvalue()


class Recorder:
    """Пишет звук в фоновом потоке, отдаёт уровень сигнала через callback."""

    def __init__(self, on_level=None, on_auto_stop=None) -> None:
        self._on_level = on_level
        self._on_auto_stop = on_auto_stop
        self._pa = pyaudio.PyAudio()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_evt = threading.Event()
        self._frames: list[bytes] = []
        self._recording = False
        self._error: str | None = None
        self.on_block = None              # (pcm, громкость) на каждый блок — для распознавания на лету

    # ------------------------------------------------------------- свойства
    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._recording

    @property
    def error(self) -> str | None:
        return self._error

    @property
    def duration(self) -> float:
        with self._lock:
            samples = sum(len(f) for f in self._frames) // SAMPLE_WIDTH
        return samples / RATE

    # -------------------------------------------------------------- команды
    def start(self, device_index: int | str | None = None, max_seconds: float = 120.0) -> bool:
        """Открывает микрофон сразу: False — не открылся (причина в .error).

        device_index — имя устройства (или старый индекс); не найдено → микрофон по умолчанию.
        """
        with self._lock:
            if self._recording:
                return False
            self._recording = True
            self._frames = []
            self._error = None
        try:
            # PyAudio не видит подключённые позже устройства. Пересоздавать его на каждое
            # нажатие дорого (сотни мс), поэтому только когда нужного микрофона нет в списке
            # или он не открылся.
            missing = isinstance(device_index, str) and device_index not in {
                name for _i, name in _input_devices(self._pa)}
            if missing:
                self._refresh_pa()
            try:
                stream = self._open(device_index)
            except Exception:
                if missing or not self._refresh_pa():
                    raise
                stream = self._open(device_index)
        except Exception as exc:  # микрофона нет, занят, отключён
            self._error = str(exc) or t("устройство не открылось")
            with self._lock:
                self._recording = False
            return False
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run,
            args=(stream, max_seconds),
            daemon=True,
            name="shepot-recorder",
        )
        self._thread.start()
        return True

    def _open(self, device_index):
        index = resolve_device(_input_devices(self._pa), device_index)
        return self._pa.open(
            format=FORMAT, channels=CHANNELS, rate=RATE, input=True,
            input_device_index=index, frames_per_buffer=CHUNK,
        )

    def _refresh_pa(self) -> bool:
        """Пересоздаёт PyAudio, чтобы увидеть новые устройства. False — нельзя сейчас."""
        if any(th.name == "shepot-recorder" and th.is_alive() for th in threading.enumerate()):
            return False                  # прошлый поток ещё читает старый stream
        try:
            self._pa.terminate()
        except Exception:
            pass
        self._pa = pyaudio.PyAudio()
        return True

    def stop(self) -> bytes | None:
        """Останавливает запись и возвращает WAV-байты (None — если пусто)."""
        if not self.is_recording and self._thread is None:
            return None
        self._stop_evt.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        with self._lock:
            self._recording = False
            frames = self._frames
            self._frames = []
        if not frames:
            return None
        return encode_wav(frames)

    def cancel(self) -> None:
        """Прерывает запись и выбрасывает накопленное."""
        self._stop_evt.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        with self._lock:
            self._recording = False
            self._frames = []

    def close(self) -> None:
        self.cancel()
        try:
            self._pa.terminate()
        except Exception:
            pass

    # ----------------------------------------------------------------- поток
    def _run(self, stream, max_seconds: float) -> None:
        try:
            max_chunks = int(max_seconds * RATE / CHUNK)
            read = 0
            while not self._stop_evt.is_set():
                data = stream.read(CHUNK, exception_on_overflow=False)
                with self._lock:
                    self._frames.append(data)
                read += 1
                level = _rms(data)
                if self._on_level is not None:
                    self._on_level(level)
                on_block = self.on_block
                if on_block is not None:
                    try:
                        on_block(data, level)
                    except Exception:
                        self.on_block = None      # сломался — запись важнее, дальше без него
                if read >= max_chunks:
                    # дошли до лимита — просим контроллер завершить расшифровку
                    if self._on_auto_stop is not None:
                        self._on_auto_stop()
                    break
        except Exception as exc:  # микрофон занят, отключён и т.п.
            self._error = str(exc)
            with self._lock:
                self._frames = []
        finally:
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass


def _rms(pcm: bytes) -> float:
    """Громкость блока в диапазоне 0..1."""
    if not pcm:
        return 0.0
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    if samples.size == 0:
        return 0.0
    rms = float(np.sqrt(np.mean(samples * samples)))
    return min(1.0, rms / 6000.0)
