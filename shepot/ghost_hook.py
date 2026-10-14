"""Окно-призрак, часть-помощник: отдельный процесс с хуком мыши.

Alt + колесо над окном меняет его прозрачность шагом 10 %, Alt + средняя кнопка
возвращает 100 %. На 30 % и ниже окно пропускает клики насквозь.

Почему отдельный процесс. Колесо с Alt нужно не просто увидеть, а забрать у
окна (иначе VS Code заодно прокрутит текст), а это умеет только
низкоуровневый хук. Хук вызывается на каждое движение мыши; если процесс
не ответил вовремя, мышь по всей системе дёргается, а Windows молча снимает
хук. В Шёпоте Python бывает занят распознаванием, поэтому хук живёт здесь:
в процессе нет ничего, кроме него и короткого исполнителя.

Связь с Шёпотом: команды по stdin (строка "quit" или конец потока — выход),
события по stdout строками JSON. Шёпот умер — помощник замечает это по
дескриптору процесса. Перед выходом все окна возвращаются как были.

Запуск: python -m shepot.ghost_hook --ghost-helper <pid Шёпота>
(в сборке: Shepot.exe --ghost-helper <pid>).
"""

from __future__ import annotations

import ctypes
import json
import os
import queue
import sys
import threading
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_MOUSE_LL = 14
WM_QUIT = 0x0012
WM_MOUSEWHEEL = 0x020A
WM_MBUTTONDOWN, WM_MBUTTONUP = 0x0207, 0x0208
VK_MENU, VK_CONTROL, VK_LWIN, VK_RWIN = 0x12, 0x11, 0x5B, 0x5C
VK_MASK = 0xE8                  # неназначенная клавиша: «гасит» одиночный Alt, чтобы не открылось меню
KEYEVENTF_KEYUP = 0x0002
GWL_EXSTYLE = -20
WS_EX_LAYERED, WS_EX_TRANSPARENT = 0x00080000, 0x00000020
LWA_ALPHA = 0x2
GA_ROOT = 2
SYNCHRONIZE = 0x00100000
STD_INPUT_HANDLE, STD_OUTPUT_HANDLE = -10, -11
INFINITE = 0xFFFFFFFF

STEP = 10                       # шаг, %
MIN_PCT = 20                    # ниже окно уже не найти глазами
THRU_PCT = 30                   # с этого значения клики проходят насквозь
WHEEL_DELTA = 120
# окна оболочки: рабочий стол, панель задач, «Пуск»
SHELL_CLASSES = {
    "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
    "Windows.UI.Core.CoreWindow", "XamlExplorerHostIslandWindow", "TopLevelWindowForOverflowXamlIsland",
}

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.COLORREF, ctypes.c_ubyte, wintypes.DWORD]
user32.GetLayeredWindowAttributes.argtypes = [
    wintypes.HWND, ctypes.POINTER(wintypes.COLORREF), ctypes.POINTER(ctypes.c_ubyte), ctypes.POINTER(wintypes.DWORD),
]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
kernel32.GetStdHandle.restype = wintypes.HANDLE
kernel32.ReadFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                              ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
kernel32.WriteFile.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD,
                               ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]


def _down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def _alt_only() -> bool:
    """Alt зажат, а Ctrl и Win — нет (Ctrl+Alt — это AltGr на многих раскладках)."""
    return _down(VK_MENU) and not (_down(VK_CONTROL) or _down(VK_LWIN) or _down(VK_RWIN))


def _class_name(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _pid(hwnd) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def window_rect(hwnd) -> list[int]:
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return [rect.left, rect.top, rect.right, rect.bottom]


def _contains(hwnd, x: int, y: int) -> bool:
    left, top, right, bottom = window_rect(hwnd)
    return left <= x < right and top <= y < bottom


# ------------------------------------------------------------------ окна

class Saved:
    """Каким окно было до нас — чтобы вернуть ровно так же."""

    def __init__(self, exstyle: int, alpha: int | None) -> None:
        self.exstyle = exstyle
        self.alpha = alpha          # None — окно не было полупрозрачным
        self.pct = 100


def _set_exstyle(hwnd, value: int) -> bool:
    ctypes.set_last_error(0)
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, value)
    if ctypes.get_last_error():
        return False
    return (user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF) == (value & 0xFFFFFFFF)


def capture(hwnd) -> Saved | None:
    """Запомнить исходный вид. None — окно рисует прозрачность само (UpdateLayeredWindow)."""
    exstyle = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    if not exstyle & WS_EX_LAYERED:
        return Saved(exstyle, None)
    key, alpha, flags = wintypes.COLORREF(), ctypes.c_ubyte(), wintypes.DWORD()
    if not user32.GetLayeredWindowAttributes(hwnd, ctypes.byref(key), ctypes.byref(alpha), ctypes.byref(flags)):
        return None
    return Saved(exstyle, alpha.value if flags.value & LWA_ALPHA else 255)


def apply(hwnd, saved: Saved, pct: int) -> bool:
    """Поставить прозрачность pct (20–100). False — Windows не дала (окно администратора)."""
    if pct >= 100:
        return restore(hwnd, saved)
    exstyle = saved.exstyle | WS_EX_LAYERED
    if pct <= THRU_PCT:
        exstyle |= WS_EX_TRANSPARENT
    if not _set_exstyle(hwnd, exstyle):
        return False
    alpha = round(255 * pct / 100)
    if not user32.SetLayeredWindowAttributes(hwnd, 0, alpha, LWA_ALPHA):
        return False
    saved.pct = pct
    return True


def restore(hwnd, saved: Saved) -> bool:
    if not user32.IsWindow(hwnd):
        return True
    if saved.alpha is not None:
        user32.SetLayeredWindowAttributes(hwnd, 0, saved.alpha, LWA_ALPHA)
    else:
        # снять «слой» целиком нельзя без перерисовки — сначала непрозрачность, потом стиль
        user32.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA)
    ok = _set_exstyle(hwnd, saved.exstyle)
    saved.pct = 100
    return ok


def force_opaque(hwnd: int) -> None:
    """Аварийный возврат из Шёпота, если помощник упал: непрозрачно и без «насквозь»."""
    if not user32.IsWindow(hwnd):
        return
    exstyle = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    if exstyle & WS_EX_LAYERED:
        user32.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA)
    if exstyle & WS_EX_TRANSPARENT:
        _set_exstyle(hwnd, exstyle & ~WS_EX_TRANSPARENT)


# ------------------------------------------------------------------ помощник

FADE_S = 0.12                   # шаг прозрачности перетекает, а не прыгает
PEEK_AFTER_S = 0.18             # столько держать один Alt над призраком, чтобы подглядеть
VK_TAB, VK_F4 = 0x09, 0x73


class Helper:
    def __init__(self, owner_pid: int, emit) -> None:
        self.owner_pid = owner_pid
        self.emit = emit
        self.windows: dict[int, Saved] = {}
        self.jobs: queue.Queue = queue.Queue()
        self.lock = threading.RLock()
        self._acc: dict[int, tuple[int, float]] = {}   # накопленное колесо тачпада
        self._fades: dict[int, tuple[int, int, float]] = {}   # hwnd → (откуда, куда, начало)
        self._alpha: dict[int, int] = {}               # что сейчас на экране
        self._swallow_mup = False
        self._wheel_at = 0.0
        self._peek = 0                                 # окно, в которое подглядываем
        self._thread_id = 0
        self._quitting = False
        self._stop = threading.Event()
        self._hook = None
        self._proc = HOOKPROC(self._on_mouse)        # ссылка, иначе сборщик заберёт колбэк

    # --- хук: только решает, забрать ли событие; всё тяжёлое — в исполнителе
    def _on_mouse(self, code, wparam, lparam):
        try:
            if code == 0:
                if wparam == WM_MOUSEWHEEL and _alt_only():
                    info = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                    delta = ctypes.c_short(info.mouseData >> 16).value
                    self._wheel_at = time.monotonic()
                    self.jobs.put(("wheel", info.pt.x, info.pt.y, delta))
                    return 1
                if wparam == WM_MBUTTONDOWN and _alt_only():
                    info = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                    self._swallow_mup = True
                    self._wheel_at = time.monotonic()
                    self.jobs.put(("reset", info.pt.x, info.pt.y, 0))
                    return 1
                if wparam == WM_MBUTTONUP and self._swallow_mup:
                    self._swallow_mup = False
                    return 1
        except Exception:
            pass
        return user32.CallNextHookEx(None, code, wparam, lparam)

    # --- поиск окна
    def ghost_at(self, x: int, y: int) -> int:
        with self.lock:
            for hwnd, saved in self.windows.items():
                if user32.IsWindowVisible(hwnd) and _contains(hwnd, x, y):
                    if saved.pct <= THRU_PCT:
                        return hwnd
                    under = user32.WindowFromPoint(wintypes.POINT(x, y))
                    if under and int(user32.GetAncestor(under, GA_ROOT) or 0) == hwnd:
                        return hwnd
        return 0

    def target(self, x: int, y: int) -> int:
        # окно «насквозь» мышь уже не находит — ищем его среди своих
        hwnd = self.ghost_at(x, y)
        if hwnd:
            return hwnd
        hwnd = user32.WindowFromPoint(wintypes.POINT(x, y))
        hwnd = user32.GetAncestor(hwnd, GA_ROOT) if hwnd else None
        if not hwnd:
            return 0
        if _class_name(hwnd) in SHELL_CLASSES or _pid(hwnd) in (os.getpid(), self.owner_pid):
            return 0
        return int(hwnd)

    # --- прозрачность с плавным переходом
    def _fade(self, hwnd: int, to_alpha: int) -> None:
        start = self._alpha.get(hwnd, 255)
        if start == to_alpha:
            self._fades.pop(hwnd, None)
            return
        self._fades[hwnd] = (start, to_alpha, time.monotonic())

    def _step_fades(self) -> None:
        now = time.monotonic()
        with self.lock:
            for hwnd, (start, end, t0) in list(self._fades.items()):
                k = min(1.0, (now - t0) / FADE_S)
                alpha = round(start + (end - start) * (1 - (1 - k) ** 2))
                user32.SetLayeredWindowAttributes(hwnd, 0, alpha, LWA_ALPHA)
                self._alpha[hwnd] = alpha
                if k >= 1.0:
                    del self._fades[hwnd]
                    saved = self.windows.get(hwnd)
                    if saved is not None and saved.pct >= 100:
                        # дошли до полной плотности — снимаем и наш «слой»
                        restore(hwnd, saved)
                        self.windows.pop(hwnd, None)
                        self._alpha.pop(hwnd, None)

    def set_pct(self, hwnd: int, saved: Saved, pct: int) -> bool:
        """Поставить стиль сразу, а прозрачность довести плавно."""
        if pct < 100:
            exstyle = saved.exstyle | WS_EX_LAYERED
            if pct <= THRU_PCT:
                exstyle |= WS_EX_TRANSPARENT
            if not _set_exstyle(hwnd, exstyle):
                return False
            if hwnd not in self._alpha:
                self._alpha[hwnd] = 255 if saved.alpha is None else saved.alpha
                user32.SetLayeredWindowAttributes(hwnd, 0, self._alpha[hwnd], LWA_ALPHA)
        saved.pct = pct
        self.windows[hwnd] = saved
        self._fade(hwnd, 255 if pct >= 100 else round(255 * pct / 100))
        return True

    def ghosts(self) -> dict[str, int]:
        return {str(h): s.pct for h, s in self.windows.items() if s.pct < 100}

    def report(self, hwnd: int, x: int, y: int, **extra) -> None:
        saved = self.windows.get(hwnd)
        pct = saved.pct if saved else 100
        self.emit({"hwnd": hwnd, "pct": pct, "thru": pct <= THRU_PCT, "rect": window_rect(hwnd),
                   "x": x, "y": y, "ghosts": self.ghosts(), **extra})

    # --- исполнитель
    def handle(self, kind: str, x: int, y: int, delta: int) -> None:
        if self._peek:
            self.unpeek()
        hwnd = self.target(x, y)
        if _down(VK_MENU):
            # Alt отпустят без других клавиш — и программа откроет меню; «пустая»
            # клавиша, нажатая под Alt, делает его сочетанием
            user32.keybd_event(VK_MASK, 0, 0, 0)
            user32.keybd_event(VK_MASK, 0, KEYEVENTF_KEYUP, 0)
        if not hwnd:
            return
        with self.lock:
            saved = self.windows.get(hwnd)
            current = saved.pct if saved else 100
            if kind == "reset":
                pct = 100
            else:
                acc, last = self._acc.get(hwnd, (0, 0.0))
                if time.monotonic() - last > 0.6:
                    acc = 0
                acc += delta
                steps = int(acc / WHEEL_DELTA)
                self._acc[hwnd] = (acc - steps * WHEEL_DELTA, time.monotonic())
                if not steps:
                    return
                pct = max(MIN_PCT, min(100, current + steps * STEP))
            if pct == current and saved is None:
                self.report(hwnd, x, y)
                return
            if saved is None:
                saved = capture(hwnd)
                if saved is None:
                    self.emit({"hwnd": hwnd, "error": "own", "rect": window_rect(hwnd), "x": x, "y": y})
                    return
            if not self.set_pct(hwnd, saved, pct):
                restore(hwnd, saved)
                self.windows.pop(hwnd, None)
                self._alpha.pop(hwnd, None)
                self.emit({"hwnd": hwnd, "error": "denied", "rect": window_rect(hwnd), "x": x, "y": y})
                return
            self.report(hwnd, x, y)

    # --- «подглядеть»: один Alt над призраком делает его плотным, пока держишь
    def _watch_peek(self, state: dict) -> None:
        alt = _alt_only() and not (_down(VK_TAB) or _down(VK_F4))
        now = time.monotonic()
        if not alt:
            state["since"] = 0.0
            if self._peek:
                self.unpeek()
            return
        if not state["since"]:
            state["since"] = now
            return
        if self._peek or now - state["since"] < PEEK_AFTER_S or self._wheel_at >= state["since"]:
            return
        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        hwnd = self.ghost_at(point.x, point.y)
        if not hwnd:
            return
        with self.lock:
            self._peek = hwnd
            self._fade(hwnd, 255)
        user32.keybd_event(VK_MASK, 0, 0, 0)
        user32.keybd_event(VK_MASK, 0, KEYEVENTF_KEYUP, 0)
        self.report(hwnd, point.x, point.y, peek=True)

    def unpeek(self) -> None:
        with self.lock:
            hwnd, self._peek = self._peek, 0
            saved = self.windows.get(hwnd)
            if not hwnd or saved is None:
                return
            self._fade(hwnd, round(255 * saved.pct / 100))
        self.report(hwnd, 0, 0, peek=False)

    def _ticker(self) -> None:
        state = {"since": 0.0}
        while not self._stop.wait(0.016):
            try:
                self._step_fades()
                if self.windows:
                    self._watch_peek(state)
            except Exception as exc:
                self.emit({"log": repr(exc)})

    def _work(self) -> None:
        while True:
            job = self.jobs.get()
            if job is None:
                return
            try:
                if job[0] == "restore":
                    self.restore_all()
                    self.emit({"ghosts": {}})
                else:
                    self.handle(*job)
            except Exception as exc:
                self.emit({"log": repr(exc)})

    def restore_all(self) -> None:
        with self.lock:
            self._fades.clear()
            self._peek = 0
            for hwnd, saved in list(self.windows.items()):
                try:
                    restore(hwnd, saved)
                except Exception:
                    pass
            self.windows.clear()
            self._alpha.clear()

    def quit(self) -> None:
        self._quitting = True
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)

    def run(self) -> int:
        self._thread_id = kernel32.GetCurrentThreadId()
        worker = threading.Thread(target=self._work, name="ghost-worker", daemon=True)
        worker.start()
        ticker = threading.Thread(target=self._ticker, name="ghost-ticker", daemon=True)
        ticker.start()
        self._hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._proc, kernel32.GetModuleHandleW(None), 0)
        if not self._hook:
            self.emit({"fatal": f"hook {ctypes.get_last_error()}"})
            return 1
        self.emit({"ready": True})
        if self._quitting:                         # Шёпот передумал, пока ставили хук
            self.quit()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.UnhookWindowsHookEx(self._hook)
        self.jobs.put(None)
        worker.join(1.0)
        self._stop.set()
        ticker.join(1.0)
        self.restore_all()
        return 0


# ------------------------------------------------------------------ связь

def _stdout_writer():
    handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
    lock = threading.Lock()

    def emit(event: dict) -> None:
        if not handle or handle == wintypes.HANDLE(-1).value:
            return
        data = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
        written = wintypes.DWORD()
        with lock:
            kernel32.WriteFile(handle, data, len(data), ctypes.byref(written), None)

    return emit


def _watch_stdin(helper: "Helper") -> None:
    """Команды Шёпота построчно: restore — вернуть все окна, quit (или конец потока) — выход."""
    handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
    if not handle or handle == wintypes.HANDLE(-1).value:
        return
    buf = ctypes.create_string_buffer(256)
    got = wintypes.DWORD()
    pending = b""
    while True:
        if not kernel32.ReadFile(handle, buf, 256, ctypes.byref(got), None) or not got.value:
            break                                  # Шёпот закрыл канал
        pending += buf.raw[:got.value]
        *lines, pending = pending.split(b"\n")
        for line in lines:
            command = line.strip()
            if command == b"restore":
                helper.jobs.put(("restore",))
            elif command == b"quit":
                helper.quit()
                return
    helper.quit()


def _watch_owner(pid: int, on_quit) -> None:
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        on_quit()
        return
    kernel32.WaitForSingleObject(handle, INFINITE)
    on_quit()


def helper_main(argv: list[str]) -> int:
    try:
        owner = int(argv[argv.index("--ghost-helper") + 1])
    except (ValueError, IndexError):
        owner = 0
    helper = Helper(owner, _stdout_writer())
    threading.Thread(target=_watch_stdin, args=(helper,), daemon=True).start()
    if owner:
        threading.Thread(target=_watch_owner, args=(owner, helper.quit), daemon=True).start()
    return helper.run()


if __name__ == "__main__":
    sys.exit(helper_main(sys.argv))
