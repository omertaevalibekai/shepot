"""Вставка без библиотеки keyboard: python -m pytest tests/test_inserter.py

Нажатия не уходят в систему: SendInput и буфер обмена подменены.
"""

from types import SimpleNamespace

import pytest

from shepot import inserter

VK_LCTRL, VK_RALT = 0xA2, 0xA5


@pytest.fixture
def sent(monkeypatch):
    calls: list[list[tuple[int, int, int]]] = []

    def fake_send(events):
        if events:
            calls.append([(e.ki.wVk, e.ki.wScan, e.ki.dwFlags) for e in events])

    monkeypatch.setattr(inserter, "_send", fake_send)
    monkeypatch.setattr(inserter.time, "sleep", lambda _s: None)
    return calls


def _held(monkeypatch, *vks):
    real = inserter._u32
    monkeypatch.setattr(inserter, "_u32", SimpleNamespace(
        GetAsyncKeyState=lambda vk: -32768 if vk in vks else 0,
        MapVirtualKeyW=real.MapVirtualKeyW,
    ))


def test_vk_event_carries_scan_code():
    event = inserter._vk_event(0x56, False)               # V
    assert event.ki.wVk == 0x56 and event.ki.wScan == 0x2F
    assert event.ki.dwFlags == 0


def test_right_alt_is_extended_key():
    up = inserter._vk_event(VK_RALT, True)
    assert up.ki.dwFlags == inserter.KEYEVENTF_KEYUP | inserter.KEYEVENTF_EXTENDEDKEY


def test_paste_is_one_ordered_batch(sent):
    inserter.send_paste()
    assert [(vk, flags & inserter.KEYEVENTF_KEYUP) for vk, _scan, flags in sent[0]] == [
        (0x11, 0), (0x56, 0), (0x56, 2), (0x11, 2),
    ]


def test_only_held_modifiers_are_released(monkeypatch, sent):
    _held(monkeypatch, VK_LCTRL, VK_RALT)
    inserter._release_modifiers()
    assert [(vk, flags & inserter.KEYEVENTF_KEYUP) for vk, _s, flags in sent[0]] == [
        (VK_LCTRL, 2), (VK_RALT, 2),
    ]


def test_nothing_sent_when_no_modifier_held(monkeypatch, sent):
    _held(monkeypatch)
    inserter._release_modifiers()
    assert sent == []


def test_paste_insert_restores_clipboard(monkeypatch, sent):
    _held(monkeypatch)
    board = {"text": "было"}
    restored = []
    monkeypatch.setattr(inserter, "get_clipboard_text", lambda: board["text"])
    monkeypatch.setattr(inserter, "set_clipboard_text", lambda t: board.update(text=t))
    monkeypatch.setattr(inserter, "_restore_clipboard_later", lambda prev, _d: restored.append(prev))

    assert inserter.insert("привет", method="paste") is True
    assert board["text"] == "привет"
    assert restored == ["было"]
    assert [vk for vk, _s, _f in sent[-1]] == [0x11, 0x56, 0x56, 0x11]


def test_clipboard_method_sends_no_keys(monkeypatch, sent):
    monkeypatch.setattr(inserter, "get_clipboard_text", lambda: None)
    monkeypatch.setattr(inserter, "set_clipboard_text", lambda _t: None)
    assert inserter.insert("текст", method="clipboard") is False
    assert sent == []


def test_type_method_sends_unicode_and_enter(monkeypatch, sent):
    _held(monkeypatch)
    assert inserter.insert("я\nb", method="type") is True
    flat = [event for batch in sent for event in batch]
    unicode_units = [scan for vk, scan, flags in flat if flags & inserter.KEYEVENTF_UNICODE]
    assert unicode_units == [ord("я"), ord("я"), ord("b"), ord("b")]
    assert [vk for vk, _s, _f in flat if vk == inserter.VK_RETURN] == [0x0D, 0x0D]


def test_empty_text_does_nothing(sent):
    assert inserter.insert("", method="paste") is False
    assert sent == []
