"""Распознавание на лету без сети: python -m pytest tests/test_live.py

Звук синтетический: блоки «речи» и «тишины» с заданной громкостью.
"""

import threading

from shepot import live

SPEECH, QUIET = 0.3, 0.005


def blocks(seconds: float, level: float) -> list[float]:
    return [level] * live._blocks(seconds)


def test_short_recording_is_not_cut():
    assert live.find_cut(blocks(5, SPEECH)) is None


def test_cut_in_middle_of_pause_after_min_length():
    levels = blocks(7, SPEECH) + blocks(0.6, QUIET)
    cut = live.find_cut(levels)
    assert live._blocks(7) <= cut < len(levels)


def test_pause_before_min_length_is_ignored():
    levels = blocks(3, SPEECH) + blocks(0.6, QUIET) + blocks(2, SPEECH)
    assert live.find_cut(levels) is None


def test_long_speech_without_pause_cut_at_quietest_block():
    levels = blocks(26, SPEECH)
    quietest = len(levels) - 40
    levels[quietest] = 0.1
    assert live.find_cut(levels) == quietest


def test_noisy_microphone_still_finds_pauses():
    noise = 0.035                                   # «тишина» громче SILENCE_LEVEL
    levels = blocks(7, SPEECH) + blocks(0.6, noise)
    assert live.find_cut(levels) is not None


class FakeStt:
    def __init__(self, fail_on: int | None = None):
        self.calls: list[str] = []
        self.fail_on = fail_on
        self.lock = threading.Lock()

    def __call__(self, wav: bytes, previous: str) -> str:
        with self.lock:
            n = len(self.calls) + 1
            self.calls.append(previous)
        if n == self.fail_on:
            raise RuntimeError("сеть")
        return f"кусок{n}"


def feed(job: live.LiveTranscriber, levels: list[float]) -> None:
    for level in levels:
        job.feed(b"\x00\x00" * live.CHUNK, level)


def test_segments_are_joined_in_order_with_previous_text():
    stt = FakeStt()
    job = live.LiveTranscriber(stt)
    feed(job, blocks(7, SPEECH) + blocks(0.6, QUIET) + blocks(7, SPEECH) + blocks(0.6, QUIET)
         + blocks(2, SPEECH))
    assert job.finish() == "кусок1 кусок2 кусок3"
    assert stt.calls == ["", "кусок1", "кусок1 кусок2"]


def test_short_recording_falls_back_to_full_transcription():
    stt = FakeStt()
    job = live.LiveTranscriber(stt)
    feed(job, blocks(4, SPEECH))
    assert job.finish() is None
    assert stt.calls == []


def test_failed_segment_falls_back():
    job = live.LiveTranscriber(FakeStt(fail_on=1))
    feed(job, blocks(7, SPEECH) + blocks(0.6, QUIET) + blocks(3, SPEECH))
    assert job.finish() is None


def test_silent_tail_is_not_sent():
    stt = FakeStt()
    job = live.LiveTranscriber(stt)
    feed(job, blocks(7, SPEECH) + blocks(0.6, QUIET) + blocks(3, QUIET))
    assert job.finish() == "кусок1"
    assert len(stt.calls) == 1


def test_cancel_stops_worker():
    job = live.LiveTranscriber(FakeStt())
    feed(job, blocks(7, SPEECH) + blocks(0.6, QUIET))
    job.cancel()
    job._worker.join(2)
    assert not job._worker.is_alive()
