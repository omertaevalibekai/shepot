"""Глобальные горячие клавиши: режим переключателя и push-to-talk.

Сочетание регистрируется через RegisterHotKey — его держит сама Windows.
Раньше здесь стоял низкоуровневый хук (библиотека keyboard), и он умирал
молча: если процесс хоть раз не ответил хуку примерно за секунду (Python в
это время распознаёт речь или сохраняет скриншот и держит GIL), Windows
снимает хук без всякой ошибки — Ctrl+Space перестаёт работать до
перезапуска. У RegisterHotKey таймаутов нет.

Отпускание клавиши (push-to-talk) и Esc для отмены читаются опросом
GetAsyncKeyState: так Esc не отбирается у других программ.
"""

from __future__ import annotations

import ctypes
import queue
import threading
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_HOTKEY = 0x0312
WM_APP_COMMAND = 0x8001
PM_REMOVE = 0x0001
QS_ALLINPUT = 0x04FF
WAIT_TIMEOUT = 0x0102
MAPVK_VSC_TO_VK_EX = 3

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
HOTKEY_ID = 1
POLL_MS = 30          # опрос Esc и отпускания — незаметно для руки, дёшево для CPU

MODIFIERS = {
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
    "alt": MOD_ALT, "shift": MOD_SHIFT,
    "win": MOD_WIN, "windows": MOD_WIN, "meta": MOD_WIN, "cmd": MOD_WIN,
}
NAMED_KEYS = {
    "space": 0x20, "esc": 0x1B, "escape": 0x1B, "enter": 0x0D, "return": 0x0D,
    "tab": 0x09, "backspace": 0x08, "insert": 0x2D, "ins": 0x2D, "delete": 0x2E, "del": 0x2E,
    "home": 0x24, "end": 0x23, "page up": 0x21, "pgup": 0x21, "page down": 0x22, "pgdown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "pause": 0x13, "caps lock": 0x14, "scroll lock": 0x91, "print screen": 0x2C,
    "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, "\\": 0xDC,
    ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF,
}

user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.MsgWaitForMultipleObjects.argtypes = [
    wintypes.DWORD, ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD, wintypes.DWORD,
]
user32.PeekMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT,
]
user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]


def _key_vk(name: str) -> int | None:
    name = name.strip().lower()
    if name in NAMED_KEYS:
        return NAMED_KEYS[name]
    if len(name) == 1 and (name.isascii() and name.isalnum()):
        return ord(name.upper())
    if name.startswith("f") and name[1:].isdigit() and 1 <= int(name[1:]) <= 24:
        return 0x70 + int(name[1:]) - 1
    try:
        # всё остальное — через раскладку: имя → скан-код → виртуальная клавиша
        import keyboard

        scan = keyboard.key_to_scan_codes(name)[0]
        vk = user32.MapVirtualKeyW(scan, MAPVK_VSC_TO_VK_EX)
        return vk or None
    except Exception:
        return None


def parse(hotkey: str) -> tuple[int, int]:
    """'ctrl+space' → (модификаторы, виртуальная клавиша). ValueError, если не разобрать."""
    parts = [p.strip().lower() for p in (hotkey or "").split("+") if p.strip()]
    if not parts:
        raise ValueError("пустое сочетание")
    if parts[-1] in MODIFIERS:
        raise ValueError("нужна основная клавиша, одних модификаторов мало")
    mods = 0
    for part in parts[:-1]:
        if part not in MODIFIERS:
            raise ValueError(f"неизвестный модификатор {part!r}")
        mods |= MODIFIERS[part]
    vk = _key_vk(parts[-1])
    if vk is None:
        raise ValueError(f"неизвестная клавиша {parts[-1]!r}")
    return mods, vk


def is_valid(hotkey: str) -> bool:
    try:
        parse(hotkey)
        return True
    except ValueError:
        return False


def _down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


class HotkeyManager:
    """Слушает горячую клавишу и сообщает контроллеру о начале/конце диктовки.

    Колбэки вызываются в собственном потоке менеджера — вызывающая сторона
    обязана сама переносить работу в свой поток (у нас это сигналы Qt).
    """

    def __init__(self, on_press, on_release, on_cancel) -> None:
        self._on_press = on_press
        self._on_release = on_release
        self._on_cancel = on_cancel
        self._commands: queue.Queue = queue.Queue()
        self._thread_id = 0
        self._ready = threading.Event()
        self._hotkey = ""
        self._mode = "toggle"
        self._vk = 0
        self._cancel_vk = 0
        self._registered = False
        self._held = False
        self._ptt_down = False
        self._cancel_was_down = False
        self._thread = threading.Thread(target=self._loop, name="shepot-hotkeys", daemon=True)
        self._thread.start()
        self._ready.wait(5)

    # ------------------------------------------------------------- установка
    def apply(self, hotkey: str, mode: str, cancel_key: str = "esc") -> None:
        """Переустанавливает привязки. ValueError — если сочетание не разобрать
        или его уже заняла другая программа."""
        mods, vk = parse(hotkey)              # ошибки разбора — сразу, в вызывающем потоке
        cancel_vk = 0
        if cancel_key:
            try:
                cancel_vk = parse(cancel_key)[1]
            except ValueError:
                cancel_vk = 0
        error = self._call(("apply", hotkey, mode, mods, vk, cancel_vk))
        if error:
            raise ValueError(error)

    def stop(self) -> None:
        self._call(("stop",))

    def _call(self, command: tuple) -> str | None:
        """Выполнить команду в потоке менеджера: RegisterHotKey привязан к потоку."""
        done = threading.Event()
        result: list = [None]
        self._commands.put((command, done, result))
        user32.PostThreadMessageW(self._thread_id, WM_APP_COMMAND, 0, 0)
        if not done.wait(3):
            return "поток горячих клавиш не отвечает"
        return result[0]

    # -------------------------------------------------------- поток менеджера
    def _loop(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        msg = wintypes.MSG()
        # Создать очередь сообщений потока до того, как в неё начнут писать.
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
        self._ready.set()
        while True:
            user32.MsgWaitForMultipleObjects(0, None, False, POLL_MS, QS_ALLINPUT)
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                    self._on_hotkey()
                elif msg.message == WM_APP_COMMAND:
                    self._run_commands()
            self._poll()

    def _run_commands(self) -> None:
        while True:
            try:
                command, done, result = self._commands.get_nowait()
            except queue.Empty:
                return
            try:
                result[0] = self._execute(command)
            except Exception as exc:              # не даём потоку умереть
                result[0] = str(exc)
            done.set()

    def _execute(self, command: tuple) -> str | None:
        self._unregister()
        self._held = False
        self._ptt_down = False
        if command[0] == "stop":
            self._cancel_vk = 0
            return None
        _, hotkey, mode, mods, vk, cancel_vk = command
        if not user32.RegisterHotKey(None, HOTKEY_ID, mods | MOD_NOREPEAT, vk):
            code = ctypes.get_last_error()
            if code == 1409:                       # ERROR_HOTKEY_ALREADY_REGISTERED
                return f"сочетание {hotkey} уже занято другой программой — выберите другое"
            return f"Windows не приняла сочетание {hotkey} (код {code})"
        self._registered = True
        self._hotkey, self._mode, self._vk, self._cancel_vk = hotkey, mode, vk, cancel_vk
        self._cancel_was_down = _down(cancel_vk) if cancel_vk else False
        return None

    def _unregister(self) -> None:
        if self._registered:
            user32.UnregisterHotKey(None, HOTKEY_ID)
            self._registered = False

    # -------------------------------------------------------------- события
    def _on_hotkey(self) -> None:
        if self._mode == "ptt":
            if not self._ptt_down:
                self._ptt_down = True
                self._held = True
                self._on_press()
            return
        if self._held:
            self._held = False
            self._on_release()
        else:
            self._held = True
            self._on_press()

    def _poll(self) -> None:
        if self._ptt_down and not _down(self._vk):
            # push-to-talk: отпустили основную клавишу — расшифровываем
            self._ptt_down = False
            if self._held:
                self._held = False
                self._on_release()
        if self._cancel_vk:
            down = _down(self._cancel_vk)
            if down and not self._cancel_was_down:
                self._held = False
                self._ptt_down = False
                self._on_cancel()
            self._cancel_was_down = down

    # -------------------------------------------------------------- состояние
    def notify_stopped(self) -> None:
        """Контроллер остановил запись сам (таймаут, ошибка) — сбрасываем флаг."""
        self._held = False
