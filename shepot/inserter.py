"""Доставка готового текста в активное окно.

Два способа:
  * paste — кладём текст в буфер и шлём Ctrl+V. Быстро и работает почти везде.
  * type  — «печатаем» текст напрямую через SendInput (KEYEVENTF_UNICODE).
            Медленнее, зато не трогает буфер обмена вообще.

Тонкость режима paste: узнать, что приложение уже прочитало буфер, штатными
средствами нельзя, поэтому прежнее содержимое возвращается по таймеру и в
фоновом потоке — UI при этом не замирает. Если приложение тормозит и успевает
прочитать буфер позже, увеличьте задержку в настройках или выберите режим type.
"""

from __future__ import annotations

import ctypes
import threading
import time

import keyboard
import win32clipboard
import win32con

_u32 = ctypes.windll.user32

_MODIFIERS = ("ctrl", "shift", "alt", "windows", "right ctrl", "right shift", "right alt")

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_RETURN = 0x0D
INPUT_KEYBOARD = 1
MAX_EVENTS_PER_CALL = 400


# --------------------------------------------------------------- буфер обмена

def _clipboard_retry(action, attempts: int = 12, delay: float = 0.03):
    """Буфер может быть занят другим приложением — пробуем несколько раз."""
    last: Exception | None = None
    for _ in range(attempts):
        try:
            win32clipboard.OpenClipboard()
        except Exception as exc:
            last = exc
            time.sleep(delay)
            continue
        try:
            return action()
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass
    if last is not None:
        raise last
    raise RuntimeError("Не удалось получить доступ к буферу обмена")


def get_clipboard_text() -> str | None:
    """Текущий текст буфера (None — если там не текст или буфер недоступен)."""

    def _read():
        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
        return None

    try:
        return _clipboard_retry(_read)
    except Exception:
        return None


def set_clipboard_text(text: str) -> None:
    def _write():
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)

    _clipboard_retry(_write)


# ----------------------------------------------------------------- SendInput

class _KeyboardInput(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _Input(ctypes.Structure):
    class _Union(ctypes.Union):
        # союз обязан быть размером с самый большой вариант (MOUSEINPUT, 32 байта
        # на x64) — иначе SendInput отвечает ERROR_INVALID_PARAMETER
        _fields_ = [("ki", _KeyboardInput), ("_padding", ctypes.c_byte * 32)]

    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_ulong), ("u", _Union)]


assert ctypes.sizeof(_Input) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)


def _unicode_event(code_unit: int, up: bool) -> _Input:
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
    return _Input(type=INPUT_KEYBOARD,
                  ki=_KeyboardInput(wVk=0, wScan=code_unit, dwFlags=flags,
                                    time=0, dwExtraInfo=None))


def _vk_event(vk: int, up: bool) -> _Input:
    flags = KEYEVENTF_KEYUP if up else 0
    return _Input(type=INPUT_KEYBOARD,
                  ki=_KeyboardInput(wVk=vk, wScan=0, dwFlags=flags,
                                    time=0, dwExtraInfo=None))


def _send(events: list[_Input]) -> None:
    if not events:
        return
    array = (_Input * len(events))(*events)
    _u32.SendInput(len(events), ctypes.byref(array), ctypes.sizeof(_Input))


def type_text(text: str) -> None:
    """Печатает текст посимвольно, минуя буфер обмена.

    Символы вне BMP (эмодзи) занимают две кодовые единицы UTF-16, и Windows
    доставляет такую пару вперёд остальной очереди. Поэтому каждая пара уходит
    отдельным вызовом SendInput, а накопленное перед ней — сбрасывается.
    """
    events: list[_Input] = []

    def flush(pause: float = 0.0) -> None:
        nonlocal events
        if events:
            _send(events)
            events = []
        if pause:
            time.sleep(pause)

    for char in text:
        if char == "\r":
            continue
        if char == "\n":
            events.append(_vk_event(VK_RETURN, False))
            events.append(_vk_event(VK_RETURN, True))
        else:
            units = _utf16_units(char)
            if len(units) > 1:
                flush(0.012)
                _send([e for unit in units
                       for e in (_unicode_event(unit, False), _unicode_event(unit, True))])
                time.sleep(0.012)
                continue
            events.append(_unicode_event(units[0], False))
            events.append(_unicode_event(units[0], True))
        if len(events) >= MAX_EVENTS_PER_CALL:
            flush(0.002)
    flush()


def _utf16_units(char: str) -> list[int]:
    raw = char.encode("utf-16-le")
    return [int.from_bytes(raw[i : i + 2], "little") for i in range(0, len(raw), 2)]


# -------------------------------------------------------------------- вставка

def _release_modifiers() -> None:
    """Снимает залипшие модификаторы горячей клавиши перед отправкой Ctrl+V."""
    for key in _MODIFIERS:
        try:
            keyboard.release(key)
        except Exception:
            pass


def _restore_clipboard_later(previous: str, delay: float) -> None:
    """Возвращает прежний буфер в фоне, не блокируя интерфейс."""

    def task() -> None:
        time.sleep(delay)
        try:
            set_clipboard_text(previous)
        except Exception:
            pass

    threading.Thread(target=task, daemon=True, name="shepot-clipboard").start()


def insert(
    text: str,
    method: str = "paste",
    restore_clipboard: bool = True,
    restore_delay: float = 0.6,
) -> bool:
    """Доставляет текст в активное окно.

    Возвращает True, если текст был отправлен в окно, и False, если он только
    положен в буфер обмена и вставить его нужно вручную.
    """
    if not text:
        return False

    if method == "type":
        _release_modifiers()
        time.sleep(0.03)
        try:
            type_text(text)
            return True
        except Exception:
            set_clipboard_text(text)      # запасной путь: хотя бы в буфер
            return False

    previous = get_clipboard_text() if restore_clipboard else None
    set_clipboard_text(text)

    if method == "clipboard":             # пользователь вставляет сам
        return False

    time.sleep(0.05)
    _release_modifiers()
    try:
        keyboard.send("ctrl+v")
    except Exception:
        return False

    if previous is not None:
        _restore_clipboard_later(previous, restore_delay)
    return True
