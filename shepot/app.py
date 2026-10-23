"""Контроллер приложения: горячая клавиша → запись → текст → вставка."""

from __future__ import annotations

import threading
import time
import winsound
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QRectF, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QAction, QActionGroup, QColor, QDesktopServices, QIcon, QPainter, QPen, QPixmap,
)
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import (
    audio, clipshot, ghost, hotkeys, inserter, island_place, jump, learn, polish, providers,
    context, live, selection, stats, stt, updates,
)
from .config import Config
from .history import History
from .history_ui import HistoryDialog
from .stats_ui import StatsDialog
from .hotkeys import HotkeyManager
from . import claude_bridge
from .claude_island import ClaudeIsland
from .edit_panel import EditPanel
from .jump_panel import JumpPanel
from .hud import Hud
from .settings_ui import MODE_LABELS, TONE_LABELS, SettingsDialog

APP_TITLE = "Шёпот"

# когда сверять вставленный текст с окном: через 20 с и через минуту, плюс при
# следующем нажатии горячей клавиши; дольше 10 минут вставку не помним
LEARN_CHECKS_MS = (20_000, 60_000)
LEARN_WINDOW_S = 600

IDLE_COLOR = QColor(210, 214, 224)
ACTIVE_COLOR = QColor(96, 205, 255)
BUSY_COLOR = QColor(255, 196, 92)


def make_icon(color: QColor) -> QIcon:
    """Рисует иконку микрофона, чтобы не тащить бинарные ресурсы."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    painter.setPen(Qt.NoPen)
    painter.setBrush(color)
    painter.drawRoundedRect(QRectF(24, 9, 16, 29), 8, 8)

    pen = QPen(color, 5)
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawArc(QRectF(16, 18, 32, 30), 180 * 16, 180 * 16)
    painter.drawLine(32, 48, 32, 56)
    painter.end()
    return QIcon(pixmap)


def _log_edit(info: dict) -> None:
    """Журнал поиска выделения: какое окно, каким способом, сколько символов. Сам текст не пишем."""
    try:
        from .config import CONFIG_DIR
        path = CONFIG_DIR / "edit.log"
        if path.exists() and path.stat().st_size > 64_000:
            path.unlink()
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{stamp} {info}\n")
    except Exception:
        pass


def _beep(frequency: int, duration: int) -> None:
    threading.Thread(
        target=lambda: _safe_beep(frequency, duration), daemon=True
    ).start()


def _safe_beep(frequency: int, duration: int) -> None:
    try:
        winsound.Beep(frequency, duration)
    except RuntimeError:
        pass


class Controller(QObject):
    # сигналы приходят из потоков keyboard/записи — Qt сам ставит их в очередь
    requestStart = Signal()
    requestFinish = Signal()
    requestCancel = Signal()
    levelChanged = Signal(float)
    resultReady = Signal(str, float)
    inserted = Signal(bool)
    failed = Signal(str)
    selectionFound = Signal(str)
    editReady = Signal(str)
    termsLearned = Signal(list)
    requestJump = Signal()
    jumpStep = Signal(str)
    jumpDone = Signal(int)

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self.app = app
        self.cfg = Config()
        island_place.PLACE.bind(self.cfg)          # остров помнит, куда его перетащили
        self.history = History(int(self.cfg.get("history_limit")))
        self.stats = stats.Stats(history_items=self.history.items())
        self.hud = Hud()
        self.recorder = audio.Recorder(
            on_level=self.levelChanged.emit,
            on_auto_stop=self.requestFinish.emit,
        )
        self.hotkeys = HotkeyManager(
            on_press=self.requestStart.emit,
            on_release=self.requestFinish.emit,
            on_cancel=self.requestCancel.emit,
        )
        self._busy = False
        # голосовая правка: выделение забирается в фоне, пока человек говорит
        self._selection: str | None = None
        self._selection_ready = threading.Event()
        self._selection_ready.set()
        self._job_is_edit = False
        self._target_window = 0
        self._target_is_terminal = False
        self._target_app = ""
        # одна диктовка: распознавание на лету и контекст окна (ждём, пока прочитается)
        self._live: live.LiveTranscriber | None = None
        self._ctx: tuple[dict, threading.Event] = ({"ctx": context.WindowContext()}, threading.Event())
        self._stopped_at = 0.0
        self._last_insert: tuple[int, str, float] | None = None
        self.edit_panel = EditPanel()
        self._settings_dialog: SettingsDialog | None = None
        self._history_dialog: HistoryDialog | None = None
        self._stats_dialog: StatsDialog | None = None

        # прыжок в проект: своя клавиша, отдельная от диктовки
        self.jump_keys = HotkeyManager(
            on_press=self.requestJump.emit, on_release=lambda: None, on_cancel=lambda: None,
        )
        self.jump_panel = JumpPanel()
        self.jump_panel.chosen.connect(self._jump_to)
        self.jump_panel.optionsChanged.connect(self._jump_options)
        self.jump_panel.pinToggled.connect(self._jump_pin)
        self.requestJump.connect(self.open_jump)
        self.jumpStep.connect(self._jump_progress)
        self.jumpDone.connect(self._jump_finished)
        self._jumping: dict | None = None
        jump.warm_tools()                      # codex ищется около секунды — заранее
        self.ghost = ghost.Ghost(parent=self, dictation_hud=self.hud)

        self.clipshot = clipshot.Watcher(self.cfg, parent=self)
        self.clipshot.captured.connect(self._report_screenshot)

        self.requestStart.connect(self.start_dictation)
        self.requestFinish.connect(self.finish_dictation)
        self.requestCancel.connect(self.cancel_dictation)
        self.levelChanged.connect(self.hud.set_level)
        self.resultReady.connect(self._deliver)
        self.inserted.connect(self._report_insertion)
        self.failed.connect(self._show_error)
        self.selectionFound.connect(self._open_edit_panel)
        self.editReady.connect(self._deliver_edit)
        self.termsLearned.connect(self._remember_terms)
        self.edit_panel.chosen.connect(self._run_edit)
        self.edit_panel.cancelled.connect(self._edit_cancelled)

        self.tray = QSystemTrayIcon(make_icon(IDLE_COLOR))
        self.tray.activated.connect(self._tray_activated)
        self._build_menu()
        self.tray.show()

        self._apply_hotkeys(announce=False)
        self.clipshot.set_enabled(bool(self.cfg.get("clipshot")))
        self.ghost.set_enabled(bool(self.cfg.get("ghost")))
        # остров сессий Claude Code: мост слушает хуки на 127.0.0.1
        self.claude_bridge = claude_bridge.Bridge()
        self.claude_island = ClaudeIsland(self.claude_bridge, self.hud)
        self.ghost.claude_island = self.claude_island
        if not self.claude_bridge.start():
            _log_edit({"claude_bridge": self.claude_bridge.error})
        self._sync_claude_island()
        self.updates = updates.Checker(parent=self)
        self.updates.found.connect(self._update_found)
        self.updates.set_enabled(bool(self.cfg.get("check_updates")))
        self._warm_up()
        # первый запуск: без ключа диктовать нечем — сразу открываем настройки
        QTimer.singleShot(500, self._prompt_for_key_if_needed)

    def _prompt_for_key_if_needed(self) -> None:
        if providers.missing_key(self.cfg) is None:
            return
        self.tray.showMessage(
            APP_TITLE,
            "Вставьте ключ API, и можно диктовать.",
            QSystemTrayIcon.Information,
            5000,
        )
        self.open_settings(focus_keys=True)

    # ------------------------------------------------------------------ трей
    def _build_menu(self) -> None:
        menu = QMenu()

        # пункт появляется, только когда на GitHub есть версия новее
        self.update_action = QAction("", menu)
        self.update_action.setVisible(False)
        self.update_action.triggered.connect(self._open_update)
        menu.addAction(self.update_action)
        self._update_url = updates.RELEASES_URL

        self.dictate_action = QAction("Начать диктовку", menu)
        self.dictate_action.triggered.connect(self.toggle_dictation)
        menu.addAction(self.dictate_action)

        self.clipshot_action = QAction("Скриншоты в терминал", menu, checkable=True)
        self.clipshot_action.setChecked(bool(self.cfg.get("clipshot")))
        self.clipshot_action.setToolTip(
            "Win+Shift+S — и Ctrl+V вставит путь к снимку вместо картинки"
        )
        self.clipshot_action.toggled.connect(self.set_clipshot)
        menu.addAction(self.clipshot_action)

        self.jump_action = QAction("Прыжок в проект…", menu)
        self.jump_action.triggered.connect(self.open_jump)
        menu.addAction(self.jump_action)
        menu.addSeparator()

        mode_menu = menu.addMenu("Режим")
        self._mode_group = QActionGroup(menu)
        self._mode_group.setExclusive(True)
        for value, label in MODE_LABELS:
            action = QAction(label.split(" — ")[0], menu, checkable=True)
            action.setData(value)
            action.setChecked(self.cfg.get("mode") == value)
            action.triggered.connect(lambda _c, v=value: self._set("mode", v))
            self._mode_group.addAction(action)
            mode_menu.addAction(action)

        tone_menu = menu.addMenu("Тон")
        self._tone_group = QActionGroup(menu)
        self._tone_group.setExclusive(True)
        for value, label in TONE_LABELS:
            action = QAction(label, menu, checkable=True)
            action.setData(value)
            action.setChecked(self.cfg.get("tone") == value)
            action.triggered.connect(lambda _c, v=value: self._set("tone", v))
            self._tone_group.addAction(action)
            tone_menu.addAction(action)

        menu.addSeparator()
        stats_action = QAction("Статистика…", menu)
        stats_action.triggered.connect(self.open_stats)
        menu.addAction(stats_action)

        history_action = QAction("История…", menu)
        history_action.triggered.connect(self.open_history)
        menu.addAction(history_action)

        settings_action = QAction("Настройки…", menu)
        settings_action.triggered.connect(lambda: self.open_settings())
        menu.addAction(settings_action)

        menu.addSeparator()
        quit_action = QAction("Выход", menu)
        quit_action.triggered.connect(self.quit)
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)
        self._menu = menu
        self._update_tooltip()

    def _update_tooltip(self) -> None:
        mode = dict(MODE_LABELS).get(self.cfg.get("mode"), "")
        self.tray.setToolTip(
            f"{APP_TITLE}\n{self._hotkey_hint()}\n{mode}"
        )

    def _hotkey_hint(self) -> str:
        start = hotkeys.display(self.cfg.get("hotkey"))
        if self.cfg.get("hotkey_mode") == "pair":
            return f"{start} — начать, {hotkeys.display(self.cfg.get('hotkey_stop'))} — вставить"
        if self.cfg.get("hotkey_mode") == "ptt":
            return f"держать {start} — диктовка"
        return f"{start} — диктовка"

    def _tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.Trigger:
            self.toggle_dictation()

    def _set(self, key: str, value) -> None:
        self.cfg.set(key, value)
        self.cfg.save()
        self._update_tooltip()

    # ------------------------------------------------------------ скриншоты
    def set_clipshot(self, enabled: bool) -> None:
        """Тумблер: перехват картинок из буфера включается сразу, без перезапуска."""
        enabled = bool(enabled)
        changed = enabled != self.clipshot.enabled
        self.clipshot.set_enabled(enabled)
        self._set("clipshot", enabled)
        if self.clipshot_action.isChecked() != enabled:
            # пришли из настроек, а не из меню — галочку правим молча
            self.clipshot_action.blockSignals(True)
            self.clipshot_action.setChecked(enabled)
            self.clipshot_action.blockSignals(False)
        if changed and self.cfg.get("hud"):
            message = "Скриншоты в терминал" if enabled else "Перехват скриншотов выключен"
            self.hud.show_state("done", message, hide_after=1.4)

    def _update_found(self, version: str, url: str) -> None:
        self._update_url = url
        self.update_action.setText(f"Скачать версию {version}")
        self.update_action.setVisible(True)
        if self.cfg.get("update_notified") != version:        # всплывашка — один раз на версию
            self.cfg.set("update_notified", version)
            self.cfg.save()
            self.tray.showMessage(APP_TITLE, f"Вышла версия {version}. Скачать — в меню значка.",
                                  QSystemTrayIcon.Information, 6000)

    def _open_update(self) -> None:
        QDesktopServices.openUrl(QUrl(self._update_url))

    def _report_screenshot(self, _path: str) -> None:
        if self.cfg.get("hud"):
            self.hud.show_state("done", "Скрин → путь в буфере", hide_after=1.2)
        if self.cfg.get("play_sounds"):
            _beep(1180, 45)

    # ----------------------------------------------------------- горячие клавиши
    def _apply_hotkeys(self, announce: bool = True) -> bool:
        try:
            self.hotkeys.apply(
                self.cfg.get("hotkey"),
                self.cfg.get("hotkey_mode"),
                self.cfg.get("cancel_key"),
                self.cfg.get("hotkey_stop") or "",
            )
        except Exception as exc:
            if announce:
                QMessageBox.warning(
                    None, APP_TITLE, f"Не удалось назначить горячую клавишу: {exc}"
                )
            return False
        finally:
            self._apply_jump_key(announce)
        self._update_tooltip()
        return True

    def _apply_jump_key(self, announce: bool) -> None:
        if not self.cfg.get("jump"):
            self.jump_keys.stop()
            return
        key = self.cfg.get("jump_hotkey") or "win+j"
        try:
            self.jump_keys.apply(key, "ptt", "")
        except Exception as exc:
            _log_edit({"jump_hotkey": str(exc)})
            if announce:
                QMessageBox.warning(None, APP_TITLE, f"Не удалось назначить клавишу прыжка: {exc}")
            return
        self.jump_action.setText(f"Прыжок в проект…\t{hotkeys.display(key)}")

    # ------------------------------------------------------------ прыжок
    def open_jump(self) -> None:
        if self.jump_panel.isVisible():
            self.jump_panel.close_panel()          # повторное нажатие закрывает
            return
        root = Path(self.cfg.get("jump_root") or str(jump.default_root()))
        projects = jump.scan(root, self.cfg.get("jump_recent") or {}, self.cfg.get("jump_pinned") or [])
        if not projects:
            self._show_error(f"Нет проектов в папке {root}")
            return
        self.jump_panel.open(projects, self.cfg.get("jump_options") or {},
                             self.cfg.get("jump_pinned") or [], root.name)

    def _jump_options(self, key: str, options: dict) -> None:
        saved = dict(self.cfg.get("jump_options") or {})
        saved[key] = options
        self._set("jump_options", saved)

    def _jump_pin(self, key: str) -> None:
        pinned = list(self.cfg.get("jump_pinned") or [])
        if key in pinned:
            pinned.remove(key)
        else:
            pinned.append(key)
        self._set("jump_pinned", pinned)

    def _jump_to(self, project, options: dict, terminal_only: bool) -> None:
        try:
            plan = jump.start(project, options, terminal_only,
                              on_step=self.jumpStep.emit, on_done=self.jumpDone.emit)
        except OSError as exc:
            self._show_error(f"Не открылось: {exc}")
            return
        recent = dict(self.cfg.get("jump_recent") or {})
        recent[str(project.path)] = time.time()
        # помним последние 30 проектов
        self._set("jump_recent", dict(sorted(recent.items(), key=lambda kv: kv[1])[-30:]))
        self._jumping = {"project": project, "plan": plan, "done": set()}
        self._jump_progress()

    def _jump_progress(self, part: str = "") -> None:
        state = self._jumping
        if not state or not self.cfg.get("hud"):
            return
        if part:
            state["done"].add(part)
        names = {"code": "VS Code", "terminal": "терминал"}
        steps = [names[p] + (" ✓" if p in state["done"] else " …") for p in state["plan"].parts]
        if state["plan"].web:
            steps.append("браузер ✓")
        message = " · ".join([state["project"].name] + steps)
        self.hud.show_state("processing", message)

    def _jump_finished(self, count: int) -> None:
        state, self._jumping = self._jumping, None
        if not state or not self.cfg.get("hud"):
            return
        plan, project = state["plan"], state["project"]
        if not plan.parts:
            text = f"{project.name} · ничего не отмечено" if not plan.web else f"{project.name} · браузер"
        elif "reuse" in (plan.editor, plan.terminal) and "new" not in (plan.editor, plan.terminal):
            text = f"{project.name} · переключился"
        elif count < len(plan.parts):
            text = f"{project.name} · открываю дольше обычного"
        else:
            text = f"{project.name} · {count} " + ("окно готово" if count == 1 else "окна готовы")
        self.hud.show_state("done", text, hide_after=2.4, hint=project.branch)

    # ------------------------------------------------------------------ поток
    def toggle_dictation(self) -> None:
        if self.recorder.is_recording:
            self.finish_dictation()
        else:
            self.start_dictation()

    def start_dictation(self) -> None:
        self._check_learning()                        # до новой вставки сверяем прошлую
        if self.edit_panel.isVisible():
            self.edit_panel.cancel()                  # повторное нажатие закрывает панель правки
            return
        if self._busy or self.recorder.is_recording:
            return
        if providers.missing_key(self.cfg) is not None:
            self._show_error("Не указан ключ API — откройте настройки")
            self._prompt_for_key_if_needed()
            return
        # окно, в котором человек работал: в него вернём фокус и вставим правку
        self._target_window = selection.foreground_window()
        self._target_app = selection.foreground_app()
        self._target_is_terminal = selection.is_terminal(self._target_app)
        self._begin_live()                            # до записи: иначе первые блоки пройдут мимо
        started = self.recorder.start(
            device_index=self.cfg.get("input_device"),
            max_seconds=float(self.cfg.get("max_seconds")),
        )
        if not started:
            self._drop_live()
            return
        self.tray.setIcon(make_icon(ACTIVE_COLOR))
        if self.cfg.get("play_sounds"):
            _beep(880, 55)
        if self.cfg.get("hud"):
            self.hud.show_state("listening")
        # Запись идёт сразу, чтобы не потерять первые слова. Параллельно смотрим,
        # нет ли выделения: если есть — это не диктовка, а правка, запись отменяем.
        self._selection = None
        if self.cfg.get("voice_edit"):
            self._selection_ready.clear()
            threading.Thread(target=self._grab_selection, daemon=True,
                             name="shepot-selection").start()

    def _begin_live(self) -> None:
        """Контекст окна читается в фоне; длинная запись распознаётся кусками по ходу."""
        snapshot = _Snapshot(self.cfg.as_dict())
        holder, ready = {"ctx": context.WindowContext()}, threading.Event()
        self._ctx = (holder, ready)
        if snapshot.get("window_context"):
            app, hwnd = self._target_app, self._target_window

            def grab() -> None:
                try:
                    holder["ctx"] = context.capture(app, hwnd)
                except Exception:
                    pass
                finally:
                    ready.set()

            threading.Thread(target=grab, daemon=True, name="shepot-context").start()
        else:
            ready.set()

        self._live = None
        if snapshot.get("live_transcribe"):
            base = stt.build_hint(snapshot)

            def transcribe(wav: bytes, previous: str) -> str:
                ready.wait(1.0)
                return stt.transcribe(wav, snapshot,
                                      hint=context.stt_hint(base, holder["ctx"], previous))

            self._live = live.LiveTranscriber(transcribe)
        self.recorder.on_block = self._live.feed if self._live else None

    def _drop_live(self) -> None:
        self.recorder.on_block = None
        current, self._live = self._live, None
        if current is not None:
            current.cancel()

    def _grab_selection(self) -> None:
        try:
            text = selection.grab(before_send=lambda: self.hotkeys.ignore_other_keys(0.5))
        except Exception as exc:
            text = None
            selection.last["error"] = repr(exc)
        self._selection = text
        self._selection_ready.set()
        _log_edit(selection.last)
        if text:
            self.selectionFound.emit(text)

    def _open_edit_panel(self, text: str) -> None:
        if self.recorder.is_recording:
            self.recorder.cancel()
            self.hotkeys.notify_stopped()
        self._drop_live()
        self._busy = False                            # короткое удержание уже ушло в _process — он отступит
        self.tray.setIcon(make_icon(IDLE_COLOR))
        anchor = self.hud.capsule_anchor()
        width = self.hud.hand_over()
        self.edit_panel.open(text, anchor, width)

    def _run_edit(self, instruction: str, width: float) -> None:
        source = self._selection
        if not source:
            return
        self._busy = True
        self.tray.setIcon(make_icon(BUSY_COLOR))
        if self.cfg.get("hud"):
            self.hud.show_state("processing", "Переписываю", from_width=width)
        snapshot = _Snapshot(self.cfg.as_dict())

        def work() -> None:
            try:
                text = polish.edit(source, instruction, snapshot)
            except Exception as exc:                  # сеть, ключ, отказ модели
                self.failed.emit(f"Не переписал: {exc}")
                return
            self.editReady.emit(text)

        threading.Thread(target=work, daemon=True, name="shepot-edit").start()

    def _deliver_edit(self, text: str) -> None:
        self._busy = False
        self.tray.setIcon(make_icon(IDLE_COLOR))
        self._job_is_edit = True
        self.history.add(text, "edit", 0.0)
        target = self._target_window
        method = self.cfg.get("insert_method")
        restore = bool(self.cfg.get("restore_clipboard"))
        delay = float(self.cfg.get("restore_delay"))

        def deliver() -> None:
            # панель забрала фокус — возвращаем его окну, где осталось выделение
            if not selection.focus_window(target):
                inserter.set_clipboard_text(text)
                self.inserted.emit(False)
                return
            try:
                ok = inserter.insert(text, method=method,
                                     restore_clipboard=restore, restore_delay=delay)
            except Exception:
                ok = False
            if ok:
                self.stats.record(stats.count_words(text), app=self._target_app, edit=True)
            self.inserted.emit(ok)

        threading.Thread(target=deliver, daemon=True, name="shepot-insert").start()

    def _edit_cancelled(self) -> None:
        selection.focus_window(self._target_window)

    def finish_dictation(self) -> None:
        if not self.recorder.is_recording:
            return
        wav = self.recorder.stop()
        self._stopped_at = time.monotonic()
        self.recorder.on_block = None
        live_job, self._live = self._live, None
        self.hotkeys.notify_stopped()
        self.tray.setIcon(make_icon(BUSY_COLOR))
        if self.cfg.get("play_sounds"):
            _beep(660, 55)

        error = self.recorder.error
        if error:
            if live_job is not None:
                live_job.cancel()
            self._show_error(f"Микрофон недоступен: {error}")
            return

        seconds = _wav_seconds(wav)
        if seconds < float(self.cfg.get("min_seconds")):
            if live_job is not None:
                live_job.cancel()
            self.tray.setIcon(make_icon(IDLE_COLOR))
            if self._selection_ready.is_set() and not self._selection:
                self.hud.hide_now()                   # иначе остров вот-вот уступит место панели
            return

        self._busy = True
        if self.cfg.get("hud"):
            self.hud.show_state("processing")
        threading.Thread(
            target=self._process, args=(wav, seconds, live_job, self._ctx), daemon=True,
            name="shepot-pipeline",
        ).start()

    def cancel_dictation(self) -> None:
        if not self.recorder.is_recording:
            return
        self.recorder.cancel()
        self._drop_live()
        self.hotkeys.notify_stopped()
        self.tray.setIcon(make_icon(IDLE_COLOR))
        self.hud.hide_now()

    def _process(self, wav: bytes, seconds: float, live_job, ctx) -> None:
        snapshot = _Snapshot(self.cfg.as_dict())
        self._selection_ready.wait(1.0)               # очень короткая запись: выделение ещё в пути
        if self._selection:
            if live_job is not None:
                live_job.cancel()
            return                                    # это была правка: ею занимается панель
        self._job_is_edit = False
        holder, ready = ctx
        try:
            # куски уже распознаны по ходу речи; не вышло или запись короткая — целиком
            transcript = live_job.finish() if live_job is not None else None
            ready.wait(1.0)
            window = holder["ctx"]
            if transcript is None:
                hint = context.stt_hint(stt.build_hint(snapshot), window)
                transcript = stt.transcribe(wav, snapshot, hint=hint)
            transcript = learn.apply(transcript, snapshot.get("replacements") or {})
            text = polish.polish(transcript, snapshot, context.polish_note(window))
        except (stt.SttError, polish.PolishError) as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:                      # не роняем трей из-за сети
            self.failed.emit(f"Непредвиденная ошибка: {exc}")
            return
        self.resultReady.emit(text, seconds)

    # ------------------------------------------------------------- результат
    def _deliver(self, text: str, seconds: float) -> None:
        self._busy = False
        self.tray.setIcon(make_icon(IDLE_COLOR))

        if not text.strip():
            if self.cfg.get("hud"):
                self.hud.show_state("done", "Ничего не распознано", hide_after=1.6)
            return

        self.history.add(text, self.cfg.get("mode"), seconds)

        # вставка спит между нажатиями — уводим её с потока интерфейса,
        # иначе индикатор замирает на время доставки
        method = self.cfg.get("insert_method")
        restore = bool(self.cfg.get("restore_clipboard"))
        delay = float(self.cfg.get("restore_delay"))

        def deliver() -> None:
            try:
                ok = inserter.insert(text, method=method,
                                     restore_clipboard=restore, restore_delay=delay)
            except Exception:
                ok = False
            if ok:
                # запомнить, что и куда вставили: позже сверим с текстом окна
                self._last_insert = (selection.foreground_window(), text, time.monotonic())
                self.stats.record(stats.count_words(text), seconds,
                                  wait=time.monotonic() - self._stopped_at, app=self._target_app)
            self.inserted.emit(ok)

        threading.Thread(target=deliver, daemon=True, name="shepot-insert").start()

    # ------------------------------------------------------- обучение словаря
    def _check_learning(self) -> None:
        """Человек поправил вставленный текст? Исправленные термины — в словарь."""
        record = self._last_insert
        if not record or not self.cfg.get("learn_corrections"):
            return
        hwnd, text, at = record
        if time.monotonic() - at > LEARN_WINDOW_S:
            self._last_insert = None
            return
        if selection.foreground_window() != hwnd:
            return                                    # человек в другом окне — читать нечего

        def work() -> None:
            try:
                document = selection.document_text()
            except Exception:
                document = None
            if document:
                pairs = learn.find_correction_pairs(text, document)
                if pairs:
                    self.termsLearned.emit(pairs)

        threading.Thread(target=work, daemon=True, name="shepot-learn").start()

    def _remember_terms(self, pairs: list) -> None:
        old_pairs = dict(self.cfg.get("replacements") or {})
        replacements = learn.remember_pairs(old_pairs, [tuple(p) for p in pairs])
        vocabulary, added = learn.merge(list(self.cfg.get("vocabulary") or []), [p[1] for p in pairs])
        if replacements == old_pairs and not added:
            return                                    # это уже знали
        self.cfg.set("replacements", replacements)
        self._set("vocabulary", vocabulary)
        added = added or [p[1] for p in pairs if old_pairs.get(p[0].lower()) != p[1]]
        quiet = not (self.recorder.is_recording or self._busy or self.edit_panel.isVisible())
        if self.cfg.get("hud") and quiet:
            self.hud.show_state("done", "Запомнил: " + ", ".join(added), hide_after=2.2)

    def _report_insertion(self, delivered: bool) -> None:
        if delivered and not self._job_is_edit:
            for delay in LEARN_CHECKS_MS:
                QTimer.singleShot(delay, self._check_learning)
        if self.cfg.get("hud"):
            if self._job_is_edit and delivered and self._target_is_terminal:
                # в терминале выделен вывод, заменить его нельзя — текст встал в строку ввода
                self.hud.show_state("done", "Вставлено в строку ввода", hide_after=1.8)
            elif self._job_is_edit and delivered:
                self.hud.show_state("done", "Заменено", hide_after=1.8, hint="Ctrl+Z вернёт")
            elif self._job_is_edit:
                self.hud.show_state("done", "Готово, вставьте Ctrl+V", hide_after=3.0)
            else:
                message = "Вставлено" if delivered else "Скопировано в буфер"
                self.hud.show_state("done", message, hide_after=1.2)

    def _show_error(self, message: str) -> None:
        self._busy = False
        self.tray.setIcon(make_icon(IDLE_COLOR))
        if self.cfg.get("play_sounds"):
            _beep(320, 160)
        if self.cfg.get("hud"):
            self.hud.show_state("error", message, hide_after=4.0)
        else:
            self.tray.showMessage(APP_TITLE, message, QSystemTrayIcon.Warning, 4000)

    # ----------------------------------------------------------------- окна
    def open_settings(self, focus_keys: bool = False) -> None:
        if self._settings_dialog is not None and self._settings_dialog.isVisible():
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
            return
        self.cancel_dictation()
        self.hotkeys.stop()                    # чтобы поле записи клавиш не срабатывало
        self.jump_keys.stop()
        dialog = SettingsDialog(self.cfg, focus_keys=focus_keys)
        self._settings_dialog = dialog
        accepted = dialog.exec()
        self._settings_dialog = None
        self._apply_hotkeys()
        if accepted:
            self._sync_menu()
            self._warm_up()

    def _warm_up(self) -> None:
        """Заранее открывает соединения и грузит локальную модель."""

        def task() -> None:
            snapshot = _Snapshot(self.cfg.as_dict())
            providers.warm(snapshot)
            stt.preload(snapshot)

        threading.Thread(target=task, daemon=True, name="shepot-warmup").start()

    def _sync_menu(self) -> None:
        for action in self._mode_group.actions():
            action.setChecked(action.data() == self.cfg.get("mode"))
        for action in self._tone_group.actions():
            action.setChecked(action.data() == self.cfg.get("tone"))
        self.set_clipshot(bool(self.cfg.get("clipshot")))
        self.ghost.set_enabled(bool(self.cfg.get("ghost")))
        self.updates.set_enabled(bool(self.cfg.get("check_updates")))
        self._sync_claude_island()
        self._update_tooltip()

    def _sync_claude_island(self) -> None:
        self.claude_island.enabled = bool(self.cfg.get("claude_island"))

    def open_stats(self) -> None:
        if self._stats_dialog is not None and self._stats_dialog.isVisible():
            self._stats_dialog.raise_()
            self._stats_dialog.activateWindow()
            return
        dialog = StatsDialog(self.stats)
        self._stats_dialog = dialog
        dialog.exec()
        self._stats_dialog = None

    def open_history(self) -> None:
        if self._history_dialog is not None and self._history_dialog.isVisible():
            self._history_dialog.raise_()
            self._history_dialog.activateWindow()
            return
        dialog = HistoryDialog(self.history)
        self._history_dialog = dialog
        dialog.exec()
        self._history_dialog = None

    def quit(self) -> None:
        self.clipshot.set_enabled(False)
        self.hotkeys.stop()
        self.jump_keys.stop()
        self.ghost.stop()                      # помощник вернёт окна непрозрачными
        self.claude_bridge.stop()
        self.recorder.close()
        self.hud.hide_now()
        self.tray.hide()
        self.app.quit()


class _Snapshot:
    """Неизменяемый срез настроек на время одной диктовки."""

    def __init__(self, data: dict) -> None:
        self._data = data

    def get(self, key: str, default=None):
        return self._data.get(key, default)


def _wav_seconds(wav: bytes | None) -> float:
    if not wav or len(wav) <= 44:
        return 0.0
    return (len(wav) - 44) / (audio.SAMPLE_WIDTH * audio.RATE)
