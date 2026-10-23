"""Поиск выделения без настоящих окон: python -m pytest tests/test_selection.py

Окно, UI Automation, буфер обмена и SendInput подменены.
"""

from types import SimpleNamespace

import pytest

from shepot import selection


@pytest.fixture
def desk(monkeypatch):
    state = SimpleNamespace(app="notepad.exe", uia=None, board="старый буфер", seq=1,
                            copies="", held=set(), sent=[])

    def fake_send(events):
        state.sent.append([e.ki.wVk for e in events])
        if state.copies:                      # окно ответило на Ctrl+C
            state.board, state.seq = state.copies, state.seq + 1

    monkeypatch.setattr(selection, "foreground_app", lambda: state.app)
    monkeypatch.setattr(selection, "_is_elevated_foreground", lambda: False)
    monkeypatch.setattr(selection, "focused_selection", lambda trim_padding=False: state.uia)
    monkeypatch.setattr(selection, "_u32", SimpleNamespace(
        GetClipboardSequenceNumber=lambda: state.seq,
        GetAsyncKeyState=lambda vk: -32768 if vk in state.held else 0,
    ))
    monkeypatch.setattr(selection.inserter, "_send", fake_send)
    monkeypatch.setattr(selection.inserter, "get_clipboard_text", lambda: state.board)
    monkeypatch.setattr(selection.inserter, "set_clipboard_text",
                        lambda text: setattr(state, "board", text))
    return state


def test_uia_selection_needs_no_keys(desk):
    desk.uia = "выделено"
    assert selection.grab() == "выделено"
    assert desk.sent == []


def test_ctrl_c_fallback_restores_clipboard(desk):
    desk.copies = "скопировано"
    assert selection.grab(wait_copy=0.2) == "скопировано"
    assert desk.sent == [[0x11, 0x43, 0x43, 0x11]]
    assert desk.board == "старый буфер"


def test_nothing_copied_means_no_selection(desk):
    assert selection.grab(wait_copy=0.05) is None
    assert desk.board == "старый буфер"


def test_terminal_never_gets_keys(desk):
    desk.app = "windowsterminal.exe"
    desk.uia = "  ls -la  "
    assert selection.grab() == "  ls -la  "
    desk.uia = None
    assert selection.grab() is None
    assert desk.sent == []


def test_held_alt_blocks_ctrl_c(desk):
    desk.held = {selection.VK_MENU}
    desk.copies = "не должно"
    assert selection.grab(wait_keys=0.02) is None
    assert desk.sent == []


def test_user_held_ctrl_sends_only_c(desk):
    desk.held = {selection.VK_CONTROL}
    desk.copies = "текст"
    assert selection.grab(wait_keys=0.02, wait_copy=0.2) == "текст"
    assert desk.sent == [[0x43, 0x43]]
