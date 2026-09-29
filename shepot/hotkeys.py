"""Глобальные горячие клавиши: режим переключателя и push-to-talk."""

from __future__ import annotations

import threading

import keyboard


def is_valid(hotkey: str) -> bool:
    try:
        keyboard.parse_hotkey(hotkey)
        return True
    except Exception:
        return False


class HotkeyManager:
    """Слушает горячую клавишу и сообщает контроллеру о начале/конце диктовки.

    Колбэки вызываются в потоке библиотеки keyboard — вызывающая сторона
    обязана сама переносить работу в свой поток.
    """

    def __init__(self, on_press, on_release, on_cancel) -> None:
        self._on_press = on_press
        self._on_release = on_release
        self._on_cancel = on_cancel
        self._lock = threading.Lock()
        self._handles: list = []
        self._hook = None
        self._hotkey = ""
        self._mode = "toggle"
        self._held = False

    # ------------------------------------------------------------- установка
    def apply(self, hotkey: str, mode: str, cancel_key: str = "esc") -> None:
        """Переустанавливает привязки. Возвращает управление сразу."""
        with self._lock:
            self._clear()
            self._hotkey = hotkey
            self._mode = mode
            self._held = False

            if not is_valid(hotkey):
                raise ValueError(f"Некорректное сочетание клавиш: {hotkey!r}")

            if mode == "ptt":
                # push-to-talk: держим — пишем, отпустили — расшифровываем
                self._hook = keyboard.hook(self._on_event, suppress=False)
            else:
                self._handles.append(
                    keyboard.add_hotkey(hotkey, self._toggle, suppress=True)
                )

            if cancel_key and is_valid(cancel_key):
                self._handles.append(
                    keyboard.add_hotkey(cancel_key, self._cancel, suppress=False)
                )

    def stop(self) -> None:
        with self._lock:
            self._clear()

    def _clear(self) -> None:
        for handle in self._handles:
            try:
                keyboard.remove_hotkey(handle)
            except Exception:
                pass
        self._handles = []
        if self._hook is not None:
            try:
                keyboard.unhook(self._hook)
            except Exception:
                pass
            self._hook = None

    # -------------------------------------------------------------- события
    def _toggle(self) -> None:
        if self._held:
            self._held = False
            self._on_release()
        else:
            self._held = True
            self._on_press()

    def _cancel(self) -> None:
        if self._held:
            self._held = False
        self._on_cancel()

    def _on_event(self, event) -> None:
        """Push-to-talk: отслеживаем состояние всего сочетания целиком."""
        if event.name is None:
            return
        try:
            pressed = keyboard.is_pressed(self._hotkey)
        except Exception:
            return
        if pressed and not self._held:
            self._held = True
            self._on_press()
        elif not pressed and self._held:
            self._held = False
            self._on_release()

    # -------------------------------------------------------------- состояние
    def notify_stopped(self) -> None:
        """Контроллер остановил запись сам (таймаут, ошибка) — сбрасываем флаг."""
        self._held = False
