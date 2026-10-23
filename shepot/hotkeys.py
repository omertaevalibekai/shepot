"""Глобальные горячие клавиши: переключатель, удержание и пара «старт / стоп».

Привязка — строка вида "ctrl+space", "f9", "right ctrl", "mouse forward".
Слушают её два механизма Windows, оба без таймаутов:

* RegisterHotKey — для сочетаний с обычной клавишей (Ctrl+Пробел, Alt+D, F9,
  медиаклавиши). Сочетание держит сама Windows и не отдаёт его активному окну.
* Raw Input — для того, что RegisterHotKey не умеет: одиночный модификатор
  (правый Ctrl, правый Alt…) и кнопки мыши (средняя, «назад», «вперёд»).
  Окно-приёмник получает каждое нажатие и отпускание в своей очереди
  сообщений. Клавиша при этом не перехватывается — её видят и другие окна.

Низкоуровневый хук (библиотека keyboard) здесь намеренно не используется:
Windows молча снимает его, если процесс хоть раз не ответил примерно за
секунду (Python в это время распознаёт речь и держит GIL), и горячая клавиша
«отваливается» до перезапуска.

Отпускание для режима удержания и Esc для отмены читаются опросом
GetAsyncKeyState: так Esc не отбирается у других программ.
"""

from __future__ import annotations

import ctypes
import queue
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass

from .i18n import t

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_HOTKEY = 0x0312
WM_INPUT = 0x00FF
WM_APP_COMMAND = 0x8001
PM_REMOVE = 0x0001
QS_ALLINPUT = 0x04FF

HWND_MESSAGE = wintypes.HWND(-3)

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
HOTKEY_IDS = {"main": 1, "stop": 2}
POLL_MS = 30          # опрос Esc и отпускания — незаметно для руки, дёшево для CPU

RID_INPUT = 0x10000003
RIM_TYPEMOUSE, RIM_TYPEKEYBOARD = 0, 1
RIDEV_REMOVE, RIDEV_INPUTSINK = 0x00000001, 0x00000100
RI_KEY_BREAK, RI_KEY_E0 = 0x01, 0x02

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
    "pause": 0x13, "caps lock": 0x14, "scroll lock": 0x91, "print screen": 0x2C, "menu": 0x5D,
    "num lock": 0x90, "browser back": 0xA6, "browser forward": 0xA7,
    "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, "\\": 0xDC,
    ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF,
    "num *": 0x6A, "num +": 0x6B, "num -": 0x6D, "num .": 0x6E, "num /": 0x6F,
    "volume mute": 0xAD, "volume down": 0xAE, "volume up": 0xAF,
    "next track": 0xB0, "previous track": 0xB1, "stop media": 0xB2, "play/pause": 0xB3,
}
# Keys that are a modifier by themselves: only Raw Input can tell left from right.
SIDE_KEYS = {
    "left ctrl": 0xA2, "right ctrl": 0xA3, "left shift": 0xA0, "right shift": 0xA1,
    "left alt": 0xA4, "right alt": 0xA5, "left win": 0x5B, "right win": 0x5C,
}
# Mouse buttons: (down flag, up flag) in RAWMOUSE.usButtonFlags.
MOUSE_BUTTONS = {
    "mouse middle": (0x0010, 0x0020),
    "mouse back": (0x0040, 0x0080),       # XBUTTON1
    "mouse forward": (0x0100, 0x0200),    # XBUTTON2
}
MOUSE_VK = {"mouse middle": 0x04, "mouse back": 0x05, "mouse forward": 0x06}

# Без модификатора эти клавиши срабатывали бы при обычном наборе текста.
TYPING_VKS = (
    set(range(0x30, 0x3A)) | set(range(0x41, 0x5B))
    | {0x20, 0x0D, 0x09, 0x08, 0x2E, 0x25, 0x26, 0x27, 0x28, 0x24, 0x23, 0x21, 0x22, 0x1B}
    | {0xC0, 0xBD, 0xBB, 0xDB, 0xDD, 0xDC, 0xBA, 0xDE, 0xBC, 0xBE, 0xBF}
)
# Сочетания, которые все программы используют по-своему.
COMMON_CTRL = {ord(c) for c in "ACVXZYSFTWNPRO"}

# ------------------------------------------------------------- ctypes types

class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", wintypes.USHORT), ("usUsage", wintypes.USHORT),
                ("dwFlags", wintypes.DWORD), ("hwndTarget", wintypes.HWND)]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [("dwType", wintypes.DWORD), ("dwSize", wintypes.DWORD),
                ("hDevice", wintypes.HANDLE), ("wParam", wintypes.WPARAM)]


class RAWKEYBOARD(ctypes.Structure):
    _fields_ = [("MakeCode", wintypes.USHORT), ("Flags", wintypes.USHORT),
                ("Reserved", wintypes.USHORT), ("VKey", wintypes.USHORT),
                ("Message", wintypes.UINT), ("ExtraInformation", wintypes.ULONG)]


class _MouseButtons(ctypes.Structure):
    _fields_ = [("usButtonFlags", wintypes.USHORT), ("usButtonData", wintypes.USHORT)]


class _MouseButtonsUnion(ctypes.Union):
    _fields_ = [("ulButtons", wintypes.ULONG), ("s", _MouseButtons)]


class RAWMOUSE(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("usFlags", wintypes.USHORT), ("u", _MouseButtonsUnion),
                ("ulRawButtons", wintypes.ULONG), ("lLastX", wintypes.LONG),
                ("lLastY", wintypes.LONG), ("ulExtraInformation", wintypes.ULONG)]


class _RawData(ctypes.Union):
    _fields_ = [("mouse", RAWMOUSE), ("keyboard", RAWKEYBOARD)]


class RAWINPUT(ctypes.Structure):
    _fields_ = [("header", RAWINPUTHEADER), ("data", _RawData)]


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
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.VkKeyScanW.argtypes = [wintypes.WCHAR]
user32.VkKeyScanW.restype = ctypes.c_short
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
user32.CreateWindowExW.restype = wintypes.HWND
user32.RegisterRawInputDevices.argtypes = [ctypes.POINTER(RAWINPUTDEVICE), wintypes.UINT, wintypes.UINT]
user32.GetRawInputData.argtypes = [
    wintypes.HANDLE, wintypes.UINT, wintypes.LPVOID, ctypes.POINTER(wintypes.UINT), wintypes.UINT,
]
user32.GetRawInputData.restype = wintypes.UINT

# ------------------------------------------------------------------ parsing


@dataclass(frozen=True)
class Binding:
    """Разобранная привязка. kind: reg (RegisterHotKey) | key (Raw Input) | mouse."""
    text: str
    kind: str
    mods: int = 0
    vk: int = 0

    @property
    def raw(self) -> bool:
        return self.kind in ("key", "mouse")


def _key_vk(name: str) -> int | None:
    name = name.strip().lower()
    if name in NAMED_KEYS:
        return NAMED_KEYS[name]
    if len(name) == 1 and name.isascii() and name.isalnum():
        return ord(name.upper())
    if name.startswith("f") and name[1:].isdigit() and 1 <= int(name[1:]) <= 24:
        return 0x70 + int(name[1:]) - 1
    if name.startswith("num ") and name[4:].isdigit() and len(name) == 5:
        return 0x60 + int(name[4:])
    if name.startswith("vk ") :
        try:
            value = int(name[3:], 16)
        except ValueError:
            return None
        return value if 0 < value < 0xFF else None
    if len(name) == 1:
        # остальные символы — через текущую раскладку (буквы кириллицы, «ä» и т. п.)
        result = user32.VkKeyScanW(name)
        vk = result & 0xFF
        return vk if result != -1 and vk not in (0, 0xFF) else None
    return None


def parse_binding(text: str) -> Binding:
    """'ctrl+space' / 'right ctrl' / 'mouse forward' → Binding. ValueError, если не разобрать."""
    clean = (text or "").strip().lower()
    if not clean:
        raise ValueError(t("пустое сочетание"))
    if clean in SIDE_KEYS:
        return Binding(clean, "key", vk=SIDE_KEYS[clean])
    if clean in MOUSE_BUTTONS:
        return Binding(clean, "mouse", vk=MOUSE_VK[clean])
    parts = [p.strip() for p in clean.split("+") if p.strip()]
    if not parts:
        raise ValueError(t("пустое сочетание"))
    if parts[-1] in MODIFIERS:
        raise ValueError(t("нужна основная клавиша, одних модификаторов мало"))
    mods = 0
    for part in parts[:-1]:
        if part not in MODIFIERS:
            raise ValueError(t("неизвестный модификатор {key}", key=repr(part)))
        mods |= MODIFIERS[part]
    vk = _key_vk(parts[-1])
    if vk is None:
        raise ValueError(t("неизвестная клавиша {key}", key=repr(parts[-1])))
    return Binding("+".join(parts), "reg", mods=mods, vk=vk)


def parse(hotkey: str) -> tuple[int, int]:
    """Совместимость: (модификаторы, клавиша) для сочетаний RegisterHotKey."""
    binding = parse_binding(hotkey)
    if binding.raw:
        raise ValueError(t("это не сочетание для RegisterHotKey"))
    return binding.mods, binding.vk


def is_valid(hotkey: str) -> bool:
    try:
        parse_binding(hotkey)
        return True
    except ValueError:
        return False


# --------------------------------------------------------------- display

_RU = {
    "ctrl": "Ctrl", "control": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win",
    "windows": "Win", "space": "Пробел", "esc": "Esc", "enter": "Enter", "tab": "Tab",
    "backspace": "Backspace", "caps lock": "Caps Lock", "pause": "Pause",
    "scroll lock": "Scroll Lock", "print screen": "Print Screen", "insert": "Insert",
    "delete": "Delete", "home": "Home", "end": "End", "page up": "Page Up",
    "page down": "Page Down", "up": "↑", "down": "↓", "left": "←", "right": "→",
    "menu": "Menu", "left ctrl": "Левый Ctrl", "right ctrl": "Правый Ctrl",
    "left shift": "Левый Shift", "right shift": "Правый Shift", "left alt": "Левый Alt",
    "right alt": "Правый Alt", "left win": "Левый Win", "right win": "Правый Win",
    "mouse middle": "Мышь: средняя", "mouse back": "Мышь: назад",
    "mouse forward": "Мышь: вперёд", "play/pause": "Play/Pause", "next track": "След. трек",
    "previous track": "Пред. трек", "stop media": "Стоп", "volume mute": "Без звука",
    "volume up": "Громче", "volume down": "Тише",
}


def display_parts(text: str) -> list[str]:
    """Названия клавиш для показа: ['Ctrl', 'Пробел']."""
    clean = (text or "").strip().lower()
    if not clean:
        return []
    if clean in _RU and ("+" not in clean):
        return [t(_RU[clean])]
    out = []
    for part in clean.split("+"):
        part = part.strip()
        if part in _RU:
            out.append(t(_RU[part]))
        elif len(part) == 1:
            out.append(part.upper())
        elif part.startswith("f") and part[1:].isdigit():
            out.append(part.upper())
        else:
            out.append(part.capitalize())
    return out


def display(text: str) -> str:
    return " + ".join(display_parts(text))


def assess(text: str, other: str | None = None) -> tuple[str, str]:
    """Оценка привязки для подсказки в настройках: (ok | warn | bad, текст)."""
    try:
        binding = parse_binding(text)
    except ValueError as exc:
        return "bad", t("Не получилось разобрать сочетание: {error}.", error=exc)
    name = display(text)
    if other and parse_binding(other).text == binding.text:
        return "bad", t("Старт и стоп на одной клавише — это режим «Переключатель». Выберите разные клавиши.")
    if binding.kind == "reg":
        if binding.mods & MOD_WIN and binding.vk == ord("L"):
            return "bad", t("Win + L Windows забирает себе (блокировка экрана). Выберите другое.")
        if binding.mods == 0 and binding.vk in TYPING_VKS:
            return "bad", t("«{key}» без Ctrl или Alt срабатывала бы при обычном наборе текста. Добавьте модификатор.", key=name)
        if binding.mods == MOD_CONTROL and binding.vk in COMMON_CTRL:
            return "warn", t("{key} — стандартное сочетание во многих программах. Шёпот перехватит его, и там оно перестанет работать.", key=name)
        return "ok", t("Готово. {key} перехватывается Шёпотом и в активное окно не попадёт.", key=name)
    if binding.text == "mouse back":
        return "warn", t("Кнопку «назад» браузер тоже получит и уйдёт на прошлую страницу. Удобнее «вперёд» или средняя.")
    if binding.text in ("left win", "right win"):
        return "warn", t("Одиночный Win ещё и открывает меню «Пуск». Лучше правый Ctrl или правый Alt.")
    return "ok", t("Готово. {key} слушается напрямую: работает при любой нагрузке, "
                    "другие программы клавишу тоже видят.", key=name)


def try_register(text: str) -> str | None:
    """Пробная регистрация: None — свободно, иначе текст ошибки. Только для сочетаний."""
    try:
        binding = parse_binding(text)
    except ValueError as exc:
        return str(exc)
    if binding.raw:
        return None
    probe_id = 0x7FF0
    if not user32.RegisterHotKey(None, probe_id, binding.mods | MOD_NOREPEAT, binding.vk):
        code = ctypes.get_last_error()
        if code == 1409:                          # ERROR_HOTKEY_ALREADY_REGISTERED
            return t("{key} уже занято другой программой — выберите другое.", key=display(text))
        return t("Windows не приняла {key} (код {code}).", key=display(text), code=code)
    user32.UnregisterHotKey(None, probe_id)
    return None


def _down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def _specific_vk(kb: RAWKEYBOARD) -> int:
    """Raw Input даёт общий VK_CONTROL/VK_MENU/VK_SHIFT — уточняем сторону."""
    vk = kb.VKey
    if vk == 0x11:
        return 0xA3 if kb.Flags & RI_KEY_E0 else 0xA2
    if vk == 0x12:
        return 0xA5 if kb.Flags & RI_KEY_E0 else 0xA4
    if vk == 0x10:
        return 0xA1 if kb.MakeCode == 0x36 else 0xA0
    return vk


# ------------------------------------------------------------------ manager

class HotkeyManager:
    """Слушает привязки и сообщает контроллеру о начале/конце диктовки.

    Режимы: toggle (одна клавиша — старт и стоп), ptt (держу — пишет),
    pair (одна клавиша начинает, другая заканчивает).

    Колбэки вызываются в собственном потоке менеджера — вызывающая сторона
    обязана сама переносить работу в свой поток (у нас это сигналы Qt).
    """

    def __init__(self, on_press, on_release, on_cancel) -> None:
        self._on_press = on_press
        self._on_release = on_release
        self._on_cancel = on_cancel
        self._commands: queue.Queue = queue.Queue()
        self._thread_id = 0
        self._hwnd = None
        self._ready = threading.Event()
        self._mode = "toggle"
        self._bindings: dict[str, Binding] = {}
        self._registered: list[int] = []
        self._raw_on = False
        self._cancel_vk = 0
        self._held = False              # идёт ли запись, начатая клавишей
        self._ptt_vk = 0                # удержание через RegisterHotKey: ждём отпускания
        self._raw_down: set[str] = set()
        self._dirty: set[str] = set()   # к одиночному модификатору нажали ещё клавишу
        self._ignore_until = 0.0        # Шёпот сам шлёт Ctrl+C — это не сочетание человека
        self._cancel_was_down = False
        self._thread = threading.Thread(target=self._loop, name="shepot-hotkeys", daemon=True)
        self._thread.start()
        self._ready.wait(5)

    # ------------------------------------------------------------- установка
    def apply(self, hotkey: str, mode: str, cancel_key: str = "esc", stop_hotkey: str = "") -> None:
        """Переустанавливает привязки. ValueError — если сочетание не разобрать
        или его уже заняла другая программа."""
        bindings = {"main": parse_binding(hotkey)}
        if mode == "pair":
            if not stop_hotkey:
                raise ValueError(t("для режима «Две клавиши» нужна клавиша остановки"))
            bindings["stop"] = parse_binding(stop_hotkey)
            if bindings["stop"].text == bindings["main"].text:
                raise ValueError(t("клавиши старта и остановки должны различаться"))
        cancel_vk = 0
        if cancel_key:
            try:
                cancel_vk = parse(cancel_key)[1]
            except ValueError:
                cancel_vk = 0
        error = self._call(("apply", mode, bindings, cancel_vk))
        if error:
            raise ValueError(error)

    def stop(self) -> None:
        self._call(("stop",))

    def _call(self, command: tuple) -> str | None:
        """Выполнить команду в потоке менеджера: RegisterHotKey и Raw Input привязаны к потоку."""
        done = threading.Event()
        result: list = [None]
        self._commands.put((command, done, result))
        user32.PostThreadMessageW(self._thread_id, WM_APP_COMMAND, 0, 0)
        if not done.wait(3):
            return t("поток горячих клавиш не отвечает")
        return result[0]

    # -------------------------------------------------------- поток менеджера
    def _loop(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        msg = wintypes.MSG()
        # Создать очередь сообщений потока до того, как в неё начнут писать.
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
        # Окно-приёмник для Raw Input: сообщение WM_INPUT адресуется окну.
        self._hwnd = user32.CreateWindowExW(
            0, "STATIC", "shepot-hotkeys", 0, 0, 0, 0, 0, HWND_MESSAGE, None, None, None,
        )
        self._ready.set()
        while True:
            user32.MsgWaitForMultipleObjects(0, None, False, POLL_MS, QS_ALLINPUT)
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                if msg.message == WM_HOTKEY:
                    try:
                        self._on_hotkey(int(msg.wParam))
                    except Exception:
                        pass
                elif msg.message == WM_APP_COMMAND:
                    self._run_commands()
                    continue
                elif msg.message == WM_INPUT:
                    try:
                        self._on_raw(msg.lParam)
                    except Exception:      # одно кривое событие не должно убить клавиши
                        pass
                user32.DispatchMessageW(ctypes.byref(msg))
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
        self._clear()
        if command[0] == "stop":
            return None
        _, mode, bindings, cancel_vk = command
        for slot, binding in bindings.items():
            if binding.raw:
                continue
            if not user32.RegisterHotKey(None, HOTKEY_IDS[slot], binding.mods | MOD_NOREPEAT, binding.vk):
                code = ctypes.get_last_error()
                self._clear()
                if code == 1409:                  # ERROR_HOTKEY_ALREADY_REGISTERED
                    return t("сочетание {key} уже занято другой программой — выберите другое", key=display(binding.text))
                return t("Windows не приняла сочетание {key} (код {code})", key=display(binding.text), code=code)
            self._registered.append(HOTKEY_IDS[slot])
        if any(b.raw for b in bindings.values()):
            if not self._raw_input(True):
                self._clear()
                return t("Windows не дала слушать клавиатуру и мышь (код {code})", code=ctypes.get_last_error())
        self._mode, self._bindings, self._cancel_vk = mode, bindings, cancel_vk
        self._cancel_was_down = _down(cancel_vk) if cancel_vk else False
        return None

    def _clear(self) -> None:
        for hotkey_id in self._registered:
            user32.UnregisterHotKey(None, hotkey_id)
        self._registered = []
        if self._raw_on:
            self._raw_input(False)
        self._bindings = {}
        self._cancel_vk = 0
        self._held = False
        self._ptt_vk = 0
        self._raw_down.clear()
        self._dirty.clear()

    def _raw_input(self, enable: bool) -> bool:
        devices = (RAWINPUTDEVICE * 2)()
        flags = RIDEV_INPUTSINK if enable else RIDEV_REMOVE
        target = self._hwnd if enable else None
        devices[0] = RAWINPUTDEVICE(0x01, 0x06, flags, target)   # клавиатура
        devices[1] = RAWINPUTDEVICE(0x01, 0x02, flags, target)   # мышь
        ok = bool(user32.RegisterRawInputDevices(devices, 2, ctypes.sizeof(RAWINPUTDEVICE)))
        self._raw_on = enable and ok
        return ok

    # -------------------------------------------------------------- события
    def _slot_down(self, slot: str) -> None:
        mode = self._mode
        if mode == "pair":
            if slot == "main" and not self._held:
                self._held = True
                self._on_press()
            elif slot == "stop" and self._held:
                self._held = False
                self._on_release()
            return
        if mode == "ptt":
            if not self._held:
                self._held = True
                self._on_press()
            return
        if self._held:
            self._held = False
            self._on_release()
        else:
            self._held = True
            self._on_press()

    def _slot_up(self, slot: str) -> None:
        if self._mode == "ptt" and slot == "main" and self._held:
            self._held = False
            self._on_release()

    def _on_hotkey(self, hotkey_id: int) -> None:
        slot = "main" if hotkey_id == HOTKEY_IDS["main"] else "stop"
        binding = self._bindings.get(slot)
        if binding is None:
            return
        if self._mode == "ptt" and slot == "main":
            if not self._held:
                self._ptt_vk = binding.vk          # отпускание заметит опрос
            self._slot_down(slot)
            return
        self._slot_down(slot)

    def _on_raw(self, handle) -> None:
        size = wintypes.UINT(0)
        header = ctypes.sizeof(RAWINPUTHEADER)
        user32.GetRawInputData(handle, RID_INPUT, None, ctypes.byref(size), header)
        if not size.value:
            return
        buffer = ctypes.create_string_buffer(size.value)
        if user32.GetRawInputData(handle, RID_INPUT, buffer, ctypes.byref(size), header) != size.value:
            return
        raw = ctypes.cast(buffer, ctypes.POINTER(RAWINPUT)).contents
        if raw.header.dwType == RIM_TYPEKEYBOARD:
            kb = raw.data.keyboard
            self._raw_key(_specific_vk(kb), not (kb.Flags & RI_KEY_BREAK))
        elif raw.header.dwType == RIM_TYPEMOUSE:
            flags = raw.data.mouse.s.usButtonFlags
            for slot, binding in self._bindings.items():
                if binding.kind != "mouse":
                    continue
                down_flag, up_flag = MOUSE_BUTTONS[binding.text]
                if flags & down_flag:
                    self._slot_down(slot)
                if flags & up_flag:
                    self._slot_up(slot)

    def _raw_key(self, vk: int, down: bool) -> None:
        mine = [s for s, b in self._bindings.items() if b.kind == "key" and b.vk == vk]
        if not mine:
            if down and time.monotonic() >= self._ignore_until:
                # Нажали другую клавишу, пока зажат наш модификатор: это было
                # сочетание вроде Ctrl+C, а не команда Шёпоту.
                for slot in self._raw_down:
                    if slot not in self._dirty:
                        self._dirty.add(slot)
                        if self._mode == "ptt" and self._held:
                            self._held = False
                            self._on_cancel()
            return
        for slot in mine:
            if down:
                if slot in self._raw_down:
                    continue                       # автоповтор
                self._raw_down.add(slot)
                self._dirty.discard(slot)
                if self._mode == "ptt":
                    self._slot_down(slot)          # удержание: запись с нажатия
            else:
                self._raw_down.discard(slot)
                clean = slot not in self._dirty
                self._dirty.discard(slot)
                if self._mode == "ptt":
                    if clean:
                        self._slot_up(slot)
                elif clean:
                    # Одиночный модификатор срабатывает на отпускании, и только
                    # если между нажатием и отпусканием не было других клавиш.
                    self._slot_down(slot)

    def _poll(self) -> None:
        if self._ptt_vk and not _down(self._ptt_vk):
            # удержание через RegisterHotKey: отпустили основную клавишу
            self._ptt_vk = 0
            self._slot_up("main")
        if self._cancel_vk:
            down = _down(self._cancel_vk)
            if down and not self._cancel_was_down:
                self._held = False
                self._ptt_vk = 0
                self._on_cancel()
            self._cancel_was_down = down

    # -------------------------------------------------------------- состояние
    def ignore_other_keys(self, seconds: float) -> None:
        """Ближайшие нажатия чужих клавиш — наши собственные (Ctrl+C для правки):
        не считать их сочетанием, которое отменяет удержание."""
        self._ignore_until = time.monotonic() + seconds

    def notify_stopped(self) -> None:
        """Контроллер остановил запись сам (таймаут, ошибка) — сбрасываем флаг."""
        self._held = False
        self._ptt_vk = 0
