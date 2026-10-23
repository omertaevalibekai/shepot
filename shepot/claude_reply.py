"""Ответ сессии Claude Code голосом: текст уходит прямо во ввод её терминала.

Окно не переключаем и клавиш не шлём. Шёпот на мгновение подключается к
консоли процесса claude (AttachConsole) и кладёт текст в её буфер ввода
(WriteConsoleInputW) — для Claude Code это выглядит как набор с клавиатуры
в его собственном терминале, даже если этот терминал свёрнут или в другой
вкладке. Работает и в обычной консоли, и в Windows Terminal (ConPTY).

Какой процесс отвечает за сессию, мост узнаёт по TCP-соединению, с которого
пришёл хук: Claude Code шлёт хуки сам (owner_pid).

Пока Шёпот подключён к чужой консоли, Ctrl+C в ней дошёл бы и до него —
на это время обработчик Ctrl+C отключаем.
"""

from __future__ import annotations

import ctypes
import socket
import struct
import threading
from ctypes import wintypes

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_iphlp = ctypes.WinDLL("iphlpapi")

KEY_EVENT = 0x0001
VK_RETURN = 0x0D
GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
FILE_SHARE_READ, FILE_SHARE_WRITE = 1, 2
OPEN_EXISTING = 3
INVALID_HANDLE = wintypes.HANDLE(-1).value
TCP_TABLE_OWNER_PID_CONNECTIONS = 4
AF_INET = 2

_lock = threading.Lock()          # консоль у процесса одна: подключаемся по очереди


class SendError(RuntimeError):
    pass


class _KeyEventRecord(ctypes.Structure):
    _fields_ = [("bKeyDown", wintypes.BOOL), ("wRepeatCount", wintypes.WORD),
                ("wVirtualKeyCode", wintypes.WORD), ("wVirtualScanCode", wintypes.WORD),
                ("UnicodeChar", wintypes.WCHAR), ("dwControlKeyState", wintypes.DWORD)]


class _InputRecord(ctypes.Structure):
    class _Event(ctypes.Union):
        _fields_ = [("KeyEvent", _KeyEventRecord), ("_pad", ctypes.c_byte * 16)]

    _fields_ = [("EventType", wintypes.WORD), ("Event", _Event)]


_k32.AttachConsole.argtypes = [wintypes.DWORD]
_k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                             wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
_k32.CreateFileW.restype = wintypes.HANDLE
_k32.WriteConsoleInputW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_InputRecord), wintypes.DWORD,
                                    ctypes.POINTER(wintypes.DWORD)]
_k32.SetConsoleCtrlHandler.argtypes = [ctypes.c_void_p, wintypes.BOOL]
_k32.CloseHandle.argtypes = [wintypes.HANDLE]


def records(text: str, submit: bool = True) -> list[_InputRecord]:
    """Нажатие и отпускание на каждый символ; переводы строк — пробелы (Enter отправил бы раньше)."""
    flat = " ".join(text.split())
    result = []

    def key(char: str, vk: int = 0) -> None:
        for down in (True, False):
            rec = _InputRecord()
            rec.EventType = KEY_EVENT
            ev = rec.Event.KeyEvent
            ev.bKeyDown, ev.wRepeatCount, ev.wVirtualKeyCode = down, 1, vk
            ev.UnicodeChar = char
            result.append(rec)

    for char in flat:
        if ord(char) > 0xFFFF:
            continue                      # вне BMP (эмодзи) консоль по одному record не примет
        key(char)
    if submit:
        key("\r", VK_RETURN)
    return result


def send(pid: int, text: str, submit: bool = True) -> None:
    """Печатает text в консоль процесса pid и (по умолчанию) жмёт в ней Enter."""
    if not pid:
        raise SendError("неизвестен процесс сессии")
    batch = records(text, submit)
    if not batch:
        return
    with _lock:
        _k32.FreeConsole()                            # своей консоли у Шёпота нет, но вдруг
        if not _k32.AttachConsole(pid):
            raise SendError(f"не подключиться к консоли процесса {pid} "
                            f"(ошибка {ctypes.get_last_error()})")
        _k32.SetConsoleCtrlHandler(None, True)        # Ctrl+C из той консоли нас не касается
        try:
            handle = _k32.CreateFileW("CONIN$", GENERIC_READ | GENERIC_WRITE,
                                      FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None)
            if handle in (None, INVALID_HANDLE):
                raise SendError("у консоли нет ввода")
            try:
                array = (_InputRecord * len(batch))(*batch)
                written = wintypes.DWORD()
                if not _k32.WriteConsoleInputW(handle, array, len(batch), ctypes.byref(written)) \
                        or written.value != len(batch):
                    raise SendError(f"консоль приняла {written.value} из {len(batch)} событий")
            finally:
                _k32.CloseHandle(handle)
        finally:
            _k32.FreeConsole()
            _k32.SetConsoleCtrlHandler(None, False)


# ------------------------------------------------- чей это хук: PID по порту

class _TcpRow(ctypes.Structure):
    _fields_ = [("state", wintypes.DWORD), ("local_addr", wintypes.DWORD), ("local_port", wintypes.DWORD),
                ("remote_addr", wintypes.DWORD), ("remote_port", wintypes.DWORD), ("pid", wintypes.DWORD)]


def owner_pid(client_port: int, server_port: int) -> int:
    """PID процесса, который открыл соединение 127.0.0.1:client_port → :server_port; 0 — не нашли."""
    size = wintypes.DWORD(0)
    get = _iphlp.GetExtendedTcpTable
    get(None, ctypes.byref(size), False, AF_INET, TCP_TABLE_OWNER_PID_CONNECTIONS, 0)
    buf = ctypes.create_string_buffer(size.value + 4096)
    size = wintypes.DWORD(len(buf))
    if get(buf, ctypes.byref(size), False, AF_INET, TCP_TABLE_OWNER_PID_CONNECTIONS, 0) != 0:
        return 0
    count = struct.unpack_from("I", buf, 0)[0]
    rows = ctypes.cast(ctypes.addressof(buf) + 4, ctypes.POINTER(_TcpRow * count)).contents
    for row in rows:
        local = socket.ntohs(row.local_port & 0xFFFF)
        remote = socket.ntohs(row.remote_port & 0xFFFF)
        if local == client_port and remote == server_port:
            return int(row.pid)
    return 0
