"""Выделенный текст активного окна — для голосовой правки.

Штатного способа спросить у чужого окна «что выделено» нет, поэтому берём его
так же, как человек: Ctrl+C. Есть ли выделение, узнаём по номеру версии буфера
(GetClipboardSequenceNumber): если окно ничего не скопировало, номер не меняется.
Прежний текст буфера сразу возвращаем на место.

Опасное место — терминалы: там Ctrl+C прерывает процесс, а Windows Terminal
без выделения пропускает в программу даже Ctrl+Shift+C (проверено: программа
получает прерывание). Поэтому у Windows Terminal выделение читаем через UI
Automation — без единого нажатия. Остальные терминалы и редакторы со встроенным
терминалом пропускаем по имени процесса.
"""

from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes

from . import inserter

_u32 = ctypes.windll.user32
_k32 = ctypes.windll.kernel32

VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN, VK_C = 0x10, 0x11, 0x12, 0x5B, 0x5C, 0x43

# терминалы: выделение читаем через UI Automation, клавиш не шлём вообще
UIA_APPS = ("windowsterminal.exe", "cmd.exe", "powershell.exe", "pwsh.exe",
            "conhost.exe", "openconsole.exe")

# Ctrl+C здесь — прерывание процесса, а UI Automation у них нет или не проверена
DEFAULT_SKIP_APPS = (
    "wt.exe", "mintty.exe", "alacritty.exe", "wezterm-gui.exe",
    "code.exe", "cursor.exe", "windsurf.exe", "idea64.exe", "pycharm64.exe",
    "webstorm64.exe", "rider64.exe", "clion64.exe", "goland64.exe",
)


last: dict = {}     # как прошла последняя попытка grab — для журнала, без самого текста


def _is_elevated_foreground() -> bool | None:
    """Окно запущено от администратора? Тогда Windows не даст ни читать его, ни нажимать в нём."""
    pid = wintypes.DWORD()
    _u32.GetWindowThreadProcessId(_u32.GetForegroundWindow(), ctypes.byref(pid))
    process = _k32.OpenProcess(0x1000, False, pid.value)
    if not process:
        return None
    token = wintypes.HANDLE()
    try:
        if not ctypes.windll.advapi32.OpenProcessToken(process, 0x0008, ctypes.byref(token)):
            return True                     # доступ к токену закрыт — почти наверняка администратор
        elevation, size = wintypes.DWORD(), wintypes.DWORD()
        ctypes.windll.advapi32.GetTokenInformation(token, 20, ctypes.byref(elevation), 4, ctypes.byref(size))
        _k32.CloseHandle(token)
        return bool(elevation.value)
    finally:
        _k32.CloseHandle(process)


def foreground_window() -> int:
    return int(_u32.GetForegroundWindow() or 0)


def focus_window(hwnd: int, timeout: float = 0.5) -> bool:
    """Вернуть фокус окну, где осталось выделение (панель правки его забирала)."""
    if not hwnd or not _u32.IsWindow(hwnd):
        return False
    if _u32.GetForegroundWindow() == hwnd:
        return True
    if _u32.IsIconic(hwnd):
        _u32.ShowWindow(hwnd, 9)                   # SW_RESTORE
    _u32.SetForegroundWindow(hwnd)
    deadline = time.monotonic() + timeout
    while _u32.GetForegroundWindow() != hwnd:
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.01)
    time.sleep(0.06)                               # окну нужно мгновение, чтобы вернуть каретку
    return True


def foreground_app() -> str:
    """Имя exe активного окна в нижнем регистре ('' — если не удалось)."""
    hwnd = _u32.GetForegroundWindow()
    if not hwnd:
        return ""
    pid = wintypes.DWORD()
    _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = _k32.OpenProcess(0x1000, False, pid.value)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if not _k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        return os.path.basename(buf.value).lower()
    finally:
        _k32.CloseHandle(handle)


def _down(vk: int) -> bool:
    return bool(_u32.GetAsyncKeyState(vk) & 0x8000)


def _wait_modifiers_released(timeout: float) -> set[int]:
    """Ждём, пока отпустят модификаторы горячей клавиши; возвращаем те, что всё ещё зажаты."""
    deadline = time.monotonic() + timeout
    keys = (VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN)
    while True:
        held = {vk for vk in keys if _down(vk)}
        if not held or time.monotonic() >= deadline:
            return held
        time.sleep(0.01)


def grab(skip_apps=DEFAULT_SKIP_APPS, wait_keys: float = 0.6, wait_copy: float = 0.35,
         before_send=None) -> str | None:
    """Текст, выделенный в активном окне, или None, если выделения нет.

    Вызывать из фонового потока: функция ждёт отпускания клавиш и ответа окна.
    """
    app = foreground_app()
    last.clear()
    last.update(app=app or "?", elevated=_is_elevated_foreground())
    if app in UIA_APPS:
        text = focused_selection(trim_padding=True)
        last["way"] = "uia-terminal"
        last["chars"] = len(text) if text is not None else None
        return text if text and text.strip() else None
    if not app or app in {name.lower() for name in skip_apps}:
        last["way"] = "skipped"
        return None

    # Сначала спрашиваем без нажатий (браузеры, Word, Блокнот это умеют);
    # Ctrl+C — только если окно через UI Automation выделение не отдаёт
    text = focused_selection()
    last["way"], last["chars"] = "uia", len(text) if text is not None else None
    if text and text.strip():
        return text

    last["way"] = "ctrl+c"
    held = _wait_modifiers_released(wait_keys)
    if held - {VK_CONTROL}:
        # зажаты Alt/Shift/Win (режим удержания с таким сочетанием):
        # Ctrl+C превратился бы в другое сочетание — не рискуем
        return None

    previous = inserter.get_clipboard_text()
    before = _u32.GetClipboardSequenceNumber()

    if VK_CONTROL in held:
        # Ctrl держит сам человек (удержание правого Ctrl и т. п.). Синтетический
        # «Ctrl отпущен» сбил бы определение отпускания, поэтому шлём только C.
        if not _down(VK_CONTROL):
            return None                     # отпустил прямо сейчас — одиночная «c» напечаталась бы в окно
        events = [inserter._vk_event(VK_C, False), inserter._vk_event(VK_C, True)]
    else:
        events = [inserter._vk_event(VK_CONTROL, False), inserter._vk_event(VK_C, False),
                  inserter._vk_event(VK_C, True), inserter._vk_event(VK_CONTROL, True)]
    if before_send is not None:
        before_send()                       # предупредить горячие клавиши, что нажатия наши
    inserter._send(events)

    deadline = time.monotonic() + wait_copy
    while _u32.GetClipboardSequenceNumber() == before:
        if time.monotonic() >= deadline:
            return None                     # окно ничего не скопировало: выделения нет
        time.sleep(0.01)
    time.sleep(0.02)                        # даём окну дописать все форматы
    text = inserter.get_clipboard_text()

    if previous is not None:
        try:
            inserter.set_clipboard_text(previous)
        except Exception:
            pass
    if not text or not text.strip():
        return None
    return text


def is_terminal(app: str) -> bool:
    """Окно, где правку некуда вставить «поверх»: результат уйдёт в строку ввода."""
    return app in UIA_APPS


# ------------------------------------------------------------ UI Automation
# Прямые вызовы IUIAutomation по таблице методов: comtypes ради трёх вызовов не тянем.

class _GUID(ctypes.Structure):
    _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort), ("c", ctypes.c_ushort),
                ("d", ctypes.c_ubyte * 8)]


def _guid(text: str) -> _GUID:
    value = _GUID()
    ctypes.windll.ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(value))
    return value


def _vcall(obj, index: int, *args, argtypes=()):
    table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    method = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)(table[index])
    return method(obj, *args)


def _release(obj) -> None:
    if obj:
        table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(table[2])(obj)


_CLSID_CUIAutomation = "{ff48dba4-60ef-4201-aa87-54103eef594e}"
_IID_IUIAutomation = "{30cbe57d-d9d0-452a-ab13-7ac5ac4825ee}"
_IID_IUIAutomationTextPattern = "{32eba289-3583-42c9-9c59-3b6d9a1e9b6a}"
_UIA_TextPatternId = 10014


def focused_selection(trim_padding: bool = False) -> str | None:
    """Выделение в элементе с фокусом через TextPattern; None — если узнать нельзя.

    trim_padding — для терминала: он добивает выделение до края экрана пробелами.
    """
    ole32 = ctypes.windll.ole32
    PV = ctypes.POINTER(ctypes.c_void_p)
    initialized = ole32.CoInitializeEx(None, 2) >= 0           # COINIT_APARTMENTTHREADED
    automation, element, pattern, ranges, item = (ctypes.c_void_p() for _ in range(5))
    try:
        ole32.CoCreateInstance(ctypes.byref(_guid(_CLSID_CUIAutomation)), None, 1,
                               ctypes.byref(_guid(_IID_IUIAutomation)), ctypes.byref(automation))
        _vcall(automation, 8, ctypes.byref(element), argtypes=(PV,))          # GetFocusedElement
        _vcall(element, 14, _UIA_TextPatternId, ctypes.byref(_guid(_IID_IUIAutomationTextPattern)),
               ctypes.byref(pattern), argtypes=(ctypes.c_int, ctypes.POINTER(_GUID), PV))  # GetCurrentPatternAs
        if not pattern:
            return None
        _vcall(pattern, 5, ctypes.byref(ranges), argtypes=(PV,))              # GetSelection
        count = ctypes.c_int()
        _vcall(ranges, 3, ctypes.byref(count), argtypes=(ctypes.POINTER(ctypes.c_int),))
        parts = []
        for index in range(count.value):
            _vcall(ranges, 4, index, ctypes.byref(item), argtypes=(ctypes.c_int, PV))
            bstr = ctypes.c_void_p()
            _vcall(item, 12, -1, ctypes.byref(bstr), argtypes=(ctypes.c_int, PV))   # GetText
            if bstr.value:
                parts.append(ctypes.wstring_at(bstr.value))
                ctypes.windll.oleaut32.SysFreeString(bstr)
            _release(item)
            item = ctypes.c_void_p()
        text = "".join(parts)
        if trim_padding:
            lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
            text = "\n".join(lines).strip("\n")
        return text
    except OSError:
        return None
    finally:
        for obj in (item, ranges, pattern, element, automation):
            _release(obj)
        if initialized:
            ole32.CoUninitialize()


def document_text(limit: int = 200_000) -> str | None:
    """Весь текст элемента с фокусом (поле ввода, документ) через UI Automation, без нажатий."""
    ole32 = ctypes.windll.ole32
    PV = ctypes.POINTER(ctypes.c_void_p)
    initialized = ole32.CoInitializeEx(None, 2) >= 0
    automation, element, pattern, whole = (ctypes.c_void_p() for _ in range(4))
    try:
        ole32.CoCreateInstance(ctypes.byref(_guid(_CLSID_CUIAutomation)), None, 1,
                               ctypes.byref(_guid(_IID_IUIAutomation)), ctypes.byref(automation))
        _vcall(automation, 8, ctypes.byref(element), argtypes=(PV,))          # GetFocusedElement
        _vcall(element, 14, _UIA_TextPatternId, ctypes.byref(_guid(_IID_IUIAutomationTextPattern)),
               ctypes.byref(pattern), argtypes=(ctypes.c_int, ctypes.POINTER(_GUID), PV))
        if not pattern:
            return None
        _vcall(pattern, 7, ctypes.byref(whole), argtypes=(PV,))               # get_DocumentRange
        bstr = ctypes.c_void_p()
        _vcall(whole, 12, limit, ctypes.byref(bstr), argtypes=(ctypes.c_int, PV))   # GetText
        if not bstr.value:
            return ""
        text = ctypes.wstring_at(bstr.value)
        ctypes.windll.oleaut32.SysFreeString(bstr)
        return text
    except OSError:
        return None
    finally:
        for obj in (whole, pattern, element, automation):
            _release(obj)
        if initialized:
            ole32.CoUninitialize()


def count_words(text: str) -> str:
    """«1 слово», «3 слова», «21 слово», «12 слов»."""
    n = len(text.split())
    if n % 10 == 1 and n % 100 != 11:
        word = "слово"
    elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        word = "слова"
    else:
        word = "слов"
    return f"{n} {word}"
