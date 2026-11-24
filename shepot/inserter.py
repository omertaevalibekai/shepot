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

import win32clipboard
import win32con

from .i18n import t

_u32 = ctypes.windll.user32
_u32.GetAsyncKeyState.argtypes = [ctypes.c_int]
_u32.GetAsyncKeyState.restype = ctypes.c_short
_u32.MapVirtualKeyW.argtypes = [ctypes.c_uint, ctypes.c_uint]

# левые и правые Shift, Ctrl, Alt и обе Win
_MODIFIER_VKS = (0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C)
_EXTENDED_VKS = {0xA3, 0xA5, 0x5B, 0x5C}          # правые Ctrl/Alt и Win — расширенные клавиши

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
MAPVK_VK_TO_VSC = 0
VK_CONTROL, VK_V = 0x11, 0x56
VK_RETURN = 0x0D
VK_SHIFT = 0x10
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
    raise RuntimeError(t("Не удалось получить доступ к буферу обмена"))


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


# ------------------------------------------------- снимок буфера (все форматы)

_k32 = ctypes.windll.kernel32
_u32.GetClipboardData.argtypes = [ctypes.c_uint]
_u32.GetClipboardData.restype = ctypes.c_void_p
_u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
_u32.SetClipboardData.restype = ctypes.c_void_p
_k32.GlobalSize.argtypes = [ctypes.c_void_p]
_k32.GlobalSize.restype = ctypes.c_size_t
_k32.GlobalLock.argtypes = [ctypes.c_void_p]
_k32.GlobalLock.restype = ctypes.c_void_p
_k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
_k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
_k32.GlobalAlloc.restype = ctypes.c_void_p
_k32.GlobalFree.argtypes = [ctypes.c_void_p]

GMEM_MOVEABLE = 0x0002
MAX_SNAPSHOT_BYTES = 128 * 1024 * 1024

# форматы-дескрипторы (не память): bitmap, metafile, palette, owner-display;
# CF_DIB/CF_DIBV5 их покрывают, Windows достроит остальное сама
_HANDLE_FORMATS = {2, 3, 9, 14, 0x80}


# Сохраняем только то, что нужно человеку обратно: текст, картинку, файлы, HTML/RTF.
# Все форматы подряд читать нельзя: GetClipboardData заставляет программу-источник
# (Excel, браузер) отрисовать каждый отложенный формат — это секунды на большом копировании.
_KEEP_STANDARD = {13, 8, 15}                  # CF_UNICODETEXT, CF_DIB, CF_HDROP
_KEEP_NAMED = {"HTML Format", "Rich Text Format", "PNG", "Preferred DropEffect"}


def _is_copyable(fmt: int) -> bool:
    if fmt in _HANDLE_FORMATS:
        return False
    if fmt in _KEEP_STANDARD:
        return True
    if fmt < 0xC000:
        return False                          # прочие стандартные Windows достроит сама
    try:
        return win32clipboard.GetClipboardFormatName(fmt) in _KEEP_NAMED
    except Exception:
        return False


def _read_format_bytes(fmt: int) -> bytes | None:
    handle = _u32.GetClipboardData(fmt)
    if not handle:
        return None
    size = _k32.GlobalSize(handle)
    if not size:
        return None
    ptr = _k32.GlobalLock(handle)
    if not ptr:
        return None
    try:
        return ctypes.string_at(ptr, size)
    finally:
        _k32.GlobalUnlock(handle)


def _write_format_bytes(fmt: int, data: bytes) -> None:
    handle = _k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not handle:
        return
    ptr = _k32.GlobalLock(handle)
    if not ptr:
        _k32.GlobalFree(handle)
        return
    ctypes.memmove(ptr, data, len(data))
    _k32.GlobalUnlock(handle)
    if not _u32.SetClipboardData(fmt, handle):
        _k32.GlobalFree(handle)               # буфер не принял — память остаётся нашей


def get_clipboard_snapshot() -> list[tuple[int, bytes]] | None:
    """Снимок всех копируемых форматов буфера (текст, картинка, файлы, HTML...).

    None — если буфер пуст или недоступен.
    """

    def _read():
        result: list[tuple[int, bytes]] = []
        total = 0
        fmt = 0
        while True:
            fmt = win32clipboard.EnumClipboardFormats(fmt)
            if not fmt:
                break
            if not _is_copyable(fmt):
                continue
            try:
                data = _read_format_bytes(fmt)
            except Exception:
                data = None
            if data is None or total + len(data) > MAX_SNAPSHOT_BYTES:
                continue
            total += len(data)
            result.append((fmt, data))
        return result or None

    try:
        return _clipboard_retry(_read)
    except Exception:
        return None


def restore_clipboard_snapshot(snapshot: list[tuple[int, bytes]]) -> None:
    def _write():
        win32clipboard.EmptyClipboard()
        for fmt, data in snapshot:
            _write_format_bytes(fmt, data)

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
    # скан-код рядом с виртуальной клавишей: часть программ смотрит только на него
    flags = (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if vk in _EXTENDED_VKS else 0)
    scan = _u32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
    return _Input(type=INPUT_KEYBOARD,
                  ki=_KeyboardInput(wVk=vk, wScan=scan, dwFlags=flags,
                                    time=0, dwExtraInfo=None))


def _send(events: list[_Input]) -> None:
    if not events:
        return
    array = (_Input * len(events))(*events)
    sent = _u32.SendInput(len(events), ctypes.byref(array), ctypes.sizeof(_Input))
    if sent != len(events):
        # окно администратора (UIPI) или заблокированный ввод: нажатия не дошли
        raise OSError(f"SendInput: отправлено {sent} из {len(events)} событий")


# Здесь Shift+Enter значит другое (в Excel — ячейка выше), нужен обычный Enter.
_PLAIN_ENTER_APPS = {"excel.exe"}


def _shift_enter_ok() -> bool:
    try:
        from .selection import foreground_app     # внутри: циклический импорт
        return (foreground_app() or "").lower() not in _PLAIN_ENTER_APPS
    except Exception:
        return True


def type_text(text: str) -> None:
    try:
        _type_text(text)
    except Exception:
        _release_modifiers()                      # пачка с Shift-up могла не дойти
        raise


def _type_text(text: str) -> None:
    """Печатает текст посимвольно, минуя буфер обмена.

    Символы вне BMP (эмодзи) занимают две кодовые единицы UTF-16, и Windows
    доставляет такую пару вперёд остальной очереди. Поэтому каждая пара уходит
    отдельным вызовом SendInput, а накопленное перед ней — сбрасывается.
    """
    events: list[_Input] = []
    shift_enter = _shift_enter_ok()

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
            # Shift+Enter: перевод строки без отправки сообщения в мессенджерах
            if shift_enter:
                events.append(_vk_event(VK_SHIFT, False))
            events.append(_vk_event(VK_RETURN, False))
            events.append(_vk_event(VK_RETURN, True))
            if shift_enter:
                events.append(_vk_event(VK_SHIFT, True))
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
    held = [vk for vk in _MODIFIER_VKS if _u32.GetAsyncKeyState(vk) & 0x8000]
    _send([_vk_event(vk, True) for vk in held])


def send_paste() -> None:
    """Ctrl+V одним вызовом SendInput: нажатия не перемешаются с чужими."""
    _send([_vk_event(VK_CONTROL, False), _vk_event(VK_V, False),
           _vk_event(VK_V, True), _vk_event(VK_CONTROL, True)])


def _blocked_by_uipi() -> bool:
    """Активное окно запущено от администратора, а мы нет: нажатия туда не дойдут."""
    try:
        from .selection import _is_elevated_foreground     # selection сам импортирует inserter
        if not _is_elevated_foreground():
            return False
        return not ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


def _restore_clipboard_later(previous, delay: float) -> None:
    """Возвращает прежний буфер (снимок всех форматов) в фоне, не блокируя интерфейс."""

    def task() -> None:
        time.sleep(delay)
        try:
            if isinstance(previous, str):
                set_clipboard_text(previous)
            else:
                restore_clipboard_snapshot(previous)
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
        try:
            if _blocked_by_uipi():
                raise OSError("UIPI")
            _release_modifiers()
            time.sleep(0.03)
            type_text(text)
            return True
        except Exception:
            set_clipboard_text(text)      # запасной путь: хотя бы в буфер
            return False

    previous = get_clipboard_snapshot() if restore_clipboard else None
    set_clipboard_text(text)

    if method == "clipboard":             # пользователь вставляет сам
        return False

    if _blocked_by_uipi():
        return False                      # текст остаётся в буфере, старый не возвращаем

    time.sleep(0.05)
    try:
        _release_modifiers()
        send_paste()
    except Exception:
        return False                      # нажатия не дошли — текст остаётся в буфере

    if previous is not None:
        _restore_clipboard_later(previous, restore_delay)
    return True
