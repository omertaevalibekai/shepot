"""Распознавание на лету: длинная диктовка режется по паузам и уходит кусками, пока человек говорит.

Без этого вся запись отправляется на распознавание только после второго нажатия,
и на минуте речи ждать приходится несколько секунд. Здесь кусок уходит, как только
в речи есть пауза и набралось хотя бы MIN_SEGMENT_S секунд, а после отпускания
клавиши остаётся распознать только хвост.

Куски распознаются по одному и по порядку: в подсказку следующему уходит конец
предыдущего, чтобы модель продолжала фразу, а не начинала заново. Если хоть один
кусок не распознался, finish() возвращает None, и контроллер распознаёт запись
целиком, как раньше. Тишину не отправляем: на ней модель придумывает текст.
"""

from __future__ import annotations

import queue
import threading
from typing import Callable

from .audio import CHUNK, RATE, encode_wav

CHUNK_S = CHUNK / RATE                # ~64 мс на блок
SILENCE_LEVEL = 0.02                  # громкость блока ниже этого — пауза
SPEECH_LEVEL = 0.05                   # выше этого — точно речь
# Подобрано на 38 с синтезированной речи (TTS, не живой голос): при 4 с ждать после
# остановки 0,8–0,9 с против 2,7 с целиком при том же тексте; при 6 с — 2,2 с.
# Пауза 0,3 с уже режет посреди фразы, 0,45 с — между фразами.
MIN_SEGMENT_S = 4.0                   # раньше не режем: короткому куску не хватает контекста
MAX_SEGMENT_S = 25.0                  # дольше без паузы — режем в самом тихом месте
PAUSE_S = 0.45                        # такая пауза — граница фразы
MIN_SPEECH_S = 0.3                    # в куске меньше речи — это тишина, не отправляем
PREVIOUS_CHARS = 200                  # сколько конца предыдущего текста дать в подсказку

Transcribe = Callable[[bytes, str], str]      # (wav, текст до этого куска) → текст


def _blocks(seconds: float) -> int:
    return max(1, round(seconds / CHUNK_S))


def _has_speech(levels: list[float]) -> bool:
    return sum(1 for level in levels if level >= SPEECH_LEVEL) >= _blocks(MIN_SPEECH_S)


def silence_level(levels: list[float]) -> float:
    """Порог паузы под этот микрофон: у шумного «тишина» громче, чем у тихого."""
    floor = sorted(levels)[len(levels) // 10]
    return min(max(SILENCE_LEVEL, floor * 1.5), SPEECH_LEVEL * 0.8)


def find_cut(levels: list[float]) -> int | None:
    """Где резать накопленный кусок: индекс блока или None, если пока рано.

    Режем посередине последней паузы, если кусок уже не короче MIN_SEGMENT_S.
    Паузы нет, а кусок дорос до MAX_SEGMENT_S, — в самом тихом блоке второй половины.
    """
    n = len(levels)
    if n < _blocks(MIN_SEGMENT_S):
        return None
    silence = silence_level(levels)
    pause = _blocks(PAUSE_S)
    quiet = 0
    for i in range(n - 1, _blocks(MIN_SEGMENT_S) - 2, -1):
        if levels[i] < silence:
            quiet += 1
            if quiet >= pause:
                return i + quiet // 2
        else:
            quiet = 0
    if n >= _blocks(MAX_SEGMENT_S):
        half = n // 2
        return min(range(half, n), key=lambda i: levels[i])
    return None


class LiveTranscriber:
    """Принимает блоки звука из записи и распознаёт законченные куски в фоне."""

    def __init__(self, transcribe: Transcribe) -> None:
        self._transcribe = transcribe
        self._lock = threading.Lock()
        self._frames: list[bytes] = []
        self._levels: list[float] = []
        self._texts: list[str] = []
        self._failed = False
        self._sent = 0                                 # сколько кусков ушло на распознавание
        self._jobs: queue.Queue = queue.Queue()
        self._worker = threading.Thread(target=self._run, daemon=True, name="shepot-live")
        self._worker.start()

    @property
    def segments(self) -> int:
        return self._sent

    def feed(self, data: bytes, level: float) -> None:
        """Вызывается из потока записи на каждый блок."""
        if self._failed:
            return
        try:
            with self._lock:
                self._frames.append(data)
                self._levels.append(level)
                cut = find_cut(self._levels)
                if cut is None:
                    return
                frames, levels = self._frames[:cut], self._levels[:cut]
                self._frames, self._levels = self._frames[cut:], self._levels[cut:]
            self._submit(frames, levels)
        except Exception:
            self._failed = True                        # потерянный блок — только запасной путь

    def finish(self, timeout: float = 60.0) -> str | None:
        """Распознаёт хвост и возвращает весь текст.

        None — распознавать запись целиком обычным путём: что-то не распозналось
        или запись была короткой и ни одного куска не отрезали (тогда ничего не выигрываем).
        """
        with self._lock:
            frames, levels = self._frames, self._levels
            self._frames, self._levels = [], []
        if self._sent == 0:
            self.cancel()
            return None
        self._submit(frames, levels)
        self._jobs.put(None)
        self._worker.join(timeout)
        if self._worker.is_alive() or self._failed:
            return None
        return " ".join(t for t in self._texts if t).strip()

    def cancel(self) -> None:
        self._failed = True
        self._jobs.put(None)

    def _submit(self, frames: list[bytes], levels: list[float]) -> None:
        if frames and _has_speech(levels):
            self._sent += 1
            self._jobs.put(encode_wav(frames))

    def _run(self) -> None:
        while True:
            wav = self._jobs.get()
            if wav is None:
                return
            if self._failed:
                continue                               # уже ясно, что нужен запасной путь
            previous = " ".join(self._texts)[-PREVIOUS_CHARS:]
            try:
                self._texts.append(self._transcribe(wav, previous).strip())
            except Exception:
                self._failed = True
