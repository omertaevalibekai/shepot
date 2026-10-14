"""Окно настроек."""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QButtonGroup,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import audio, claude_hooks, clipshot, hotkeys, jump, providers, secrets, startup
from .polish import LANGUAGE_NAMES

MODE_LABELS = [
    ("clean", "Чистовик — убрать «эээ», поправить пунктуацию"),
    ("raw", "Как есть — сырая расшифровка без обработки"),
    ("translate", "Перевод — говорю на одном, вставляется на другом"),
]

TONE_LABELS = [
    ("preserve", "Сохранить мою манеру"),
    ("neutral", "Нейтральный"),
    ("casual", "Разговорный"),
    ("formal", "Деловой"),
]

SOURCE_LANGUAGES = [("auto", "Определять автоматически")] + [
    (code, name) for code, name in sorted(LANGUAGE_NAMES.items(), key=lambda p: p[1])
]

STT_BACKENDS = [
    ("openai", "Облако OpenAI — быстрее и точнее"),
    ("local", "Локально (faster-whisper) — без интернета"),
]

LOCAL_MODELS = ["tiny", "base", "small", "medium", "large-v3"]
LOCAL_DEVICES = [("auto", "Автоматически"), ("cuda", "Видеокарта (CUDA)"), ("cpu", "Процессор")]

LLM_PROVIDERS = [
    ("openai", "OpenAI"),
    ("anthropic", "Anthropic (Claude)"),
    ("none", "Без обработки"),
]

INSERT_METHODS = [
    ("paste", "Через буфер обмена (Ctrl+V) — быстро"),
    ("type", "Набором с клавиатуры — не трогает буфер"),
    ("clipboard", "Только скопировать, вставлю сам"),
]


# Скан-коды модификаторов (с битом расширенной клавиши 0x100): только по ним
# Qt отличает правый Ctrl от левого.
_SIDE_BY_SCAN = {
    0x1D: "left ctrl", 0x11D: "right ctrl", 0x2A: "left shift", 0x36: "right shift",
    0x38: "left alt", 0x138: "right alt",
}
_SIDE_BY_VK = {0x5B: "left win", 0x5C: "right win"}
_GENERIC = {"left ctrl": "ctrl", "right ctrl": "ctrl", "left shift": "shift",
            "right shift": "shift", "left alt": "alt", "right alt": "alt",
            "left win": "win", "right win": "win"}
_MOUSE = {Qt.MiddleButton: "mouse middle", Qt.BackButton: "mouse back",
          Qt.ForwardButton: "mouse forward"}


def _vk_name(vk: int) -> str:
    """Имя клавиши для строки привязки по виртуальному коду Windows."""
    for name, code in hotkeys.NAMED_KEYS.items():
        if code == vk:
            return name
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk).lower()
    if 0x70 <= vk <= 0x87:
        return f"f{vk - 0x6F}"
    if 0x60 <= vk <= 0x69:
        return f"num {vk - 0x60}"
    return f"vk {vk:02x}"


class KeyCapture(QPushButton):
    """Кнопка записи привязки: нажал на неё — она ждёт клавишу или кнопку мыши.

    Сочетание фиксируется, когда отпущены все клавиши: так одиночный
    модификатор (правый Ctrl) тоже можно назначить. Esc без других клавиш
    отменяет запись, клик мимо — тоже.
    """

    changed = Signal(str)
    PLACEHOLDER = "Нажмите, чтобы назначить"

    def __init__(self, value: str = "", parent=None) -> None:
        super().__init__(parent)
        self._value = value
        self._listening = False
        self._held: list[str] = []
        self._peak: list[str] = []
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(48)
        self.setFocusPolicy(Qt.StrongFocus)
        self.clicked.connect(self._start)
        self._refresh()

    def value(self) -> str:
        return self._value

    def set_value(self, value: str) -> None:
        self._value = value
        self._refresh()
        self.changed.emit(value)

    # ------------------------------------------------------------- состояние
    def _refresh(self) -> None:
        if self._listening:
            self.setText("●  Нажмите клавишу или кнопку мыши…   Esc — отмена")
            self.setStyleSheet(
                "QPushButton { text-align: left; padding: 10px 14px; border-radius: 10px;"
                " border: 2px solid #d6333f; background: rgba(214,51,63,0.10); color: #d6333f;"
                " font-weight: 600; }"
            )
            return
        shown = hotkeys.display(self._value) if self._value else self.PLACEHOLDER
        self.setText(shown)
        self.setStyleSheet(
            "QPushButton { text-align: left; padding: 10px 14px; border-radius: 10px;"
            " border: 2px dashed rgba(140,150,160,0.75); background: rgba(127,127,127,0.08);"
            f" font-family: 'Cascadia Mono', Consolas, monospace; font-size: 14px;"
            f" font-weight: {'600' if self._value else '400'}; }}"
            " QPushButton:hover { border-color: #2f6fdf; }"
            " QPushButton:focus { border-color: #2f6fdf; }"
        )

    def _start(self) -> None:
        if self._listening:
            return
        self._listening = True
        self._held, self._peak = [], []
        self._refresh()
        self.setFocus()
        self.grabKeyboard()
        self.grabMouse()

    def _finish(self, value: str | None) -> None:
        self._listening = False
        self.releaseKeyboard()
        self.releaseMouse()
        if value is not None:
            self._value = value
            self.changed.emit(value)
        self._refresh()

    # ------------------------------------------------------------- события
    def _ident(self, event) -> str:
        scan, vk = event.nativeScanCode(), event.nativeVirtualKey()
        if scan in _SIDE_BY_SCAN:
            return _SIDE_BY_SCAN[scan]
        if vk in _SIDE_BY_VK:
            return _SIDE_BY_VK[vk]
        return _vk_name(vk)

    def keyPressEvent(self, event) -> None:
        if not self._listening:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
                self._start()
                return
            super().keyPressEvent(event)
            return
        event.accept()
        if event.isAutoRepeat():
            return
        ident = self._ident(event)
        if ident == "esc" and not self._held:
            self._finish(None)
            return
        if ident not in self._held:
            self._held.append(ident)
        if len(self._held) >= len(self._peak):
            self._peak = list(self._held)

    def keyReleaseEvent(self, event) -> None:
        if not self._listening:
            super().keyReleaseEvent(event)
            return
        event.accept()
        if event.isAutoRepeat():
            return
        ident = self._ident(event)
        if ident in self._held:
            self._held.remove(ident)
        if not self._held and self._peak:
            self._finish(self._compose(self._peak))

    def mousePressEvent(self, event) -> None:
        if not self._listening:
            super().mousePressEvent(event)
            return
        event.accept()
        if event.button() in _MOUSE:
            self._finish(_MOUSE[event.button()])
        elif not self.rect().contains(event.position().toPoint()):
            self._finish(None)                      # клик мимо — отмена

    def focusOutEvent(self, event) -> None:
        if self._listening:
            self._finish(None)
        super().focusOutEvent(event)

    @staticmethod
    def _compose(chord: list[str]) -> str:
        mods = [k for k in chord if k in _GENERIC]
        keys = [k for k in chord if k not in _GENERIC]
        if not keys:
            return mods[-1] if len(mods) == 1 else "+".join(
                dict.fromkeys(_GENERIC[m] for m in mods[:-1])) + "+" + mods[-1]
        generic = list(dict.fromkeys(_GENERIC[m] for m in mods))
        return "+".join(generic + [keys[-1]])


class ModeCard(QPushButton):
    """Карточка режима: название и одна строка пояснения."""

    def __init__(self, title: str, hint: str, parent=None) -> None:
        super().__init__(f"{title}\n{hint}", parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(64)
        self.setStyleSheet(
            "QPushButton { text-align: left; padding: 10px 12px; border-radius: 10px;"
            " border: 2px solid rgba(140,150,160,0.45); background: rgba(127,127,127,0.06); }"
            " QPushButton:hover { border-color: rgba(140,150,160,0.9); }"
            " QPushButton:checked { border-color: #2f6fdf; background: rgba(47,111,223,0.14); }"
        )


class _CheckThread(QThread):
    """Проверка ключа живым запросом — в отдельном потоке, чтобы окно не висло."""

    done = Signal(bool, str)

    def __init__(self, checker, key: str, parent=None) -> None:
        super().__init__(parent)
        self._checker = checker
        self._key = key

    def run(self) -> None:
        try:
            ok, message = self._checker(self._key)
        except Exception as exc:
            ok, message = False, str(exc)[:200]
        self.done.emit(ok, message)


class KeyField(QWidget):
    """Поле ввода API-ключа со скрытым текстом и кнопкой проверки."""

    def __init__(self, name: str, checker, placeholder: str, parent=None) -> None:
        super().__init__(parent)
        self.name = name
        self._checker = checker
        self._thread: _CheckThread | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        row = QHBoxLayout()
        self.edit = QLineEdit()
        self.edit.setEchoMode(QLineEdit.Password)
        self.edit.setText(secrets.stored(name))
        if not self.edit.text() and secrets.is_from_environment(name):
            self.edit.setPlaceholderText(
                f"Взят из переменной среды: {secrets.masked(name)}"
            )
        else:
            self.edit.setPlaceholderText(placeholder)
        self.edit.textEdited.connect(lambda: self._say("", None))
        row.addWidget(self.edit, 1)

        self.reveal = QPushButton("Показать")
        self.reveal.setCheckable(True)
        self.reveal.setFixedWidth(88)
        self.reveal.toggled.connect(self._toggle_reveal)
        row.addWidget(self.reveal)

        self.check_button = QPushButton("Проверить")
        self.check_button.setFixedWidth(96)
        self.check_button.clicked.connect(self._check)
        row.addWidget(self.check_button)
        layout.addLayout(row)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

    def value(self) -> str:
        return self.edit.text().strip()

    def _toggle_reveal(self, shown: bool) -> None:
        self.edit.setEchoMode(QLineEdit.Normal if shown else QLineEdit.Password)
        self.reveal.setText("Скрыть" if shown else "Показать")

    def _check(self) -> None:
        key = self.value() or secrets.get(self.name)
        if not key:
            self._say("Сначала вставьте ключ.", False)
            return
        self.check_button.setEnabled(False)
        self._say("Проверяю…", None)
        self._thread = _CheckThread(self._checker, key, self)
        self._thread.done.connect(self._checked)
        self._thread.start()

    def _checked(self, ok: bool, message: str) -> None:
        self.check_button.setEnabled(True)
        self._say(message, ok)

    def _say(self, message: str, ok: bool | None) -> None:
        color = {True: "#4caf78", False: "#e06a6a", None: "#888"}[ok]
        self.status.setStyleSheet(f"color: {color};")
        self.status.setText(message)


def _fill(combo: QComboBox, pairs, current) -> None:
    for value, label in pairs:
        combo.addItem(label, value)
    index = combo.findData(current)
    combo.setCurrentIndex(index if index >= 0 else 0)


class SettingsDialog(QDialog):
    def __init__(self, cfg, parent=None, focus_keys: bool = False) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("Шёпот — настройки")
        self.setMinimumWidth(640)

        # окно высокое — прячем содержимое в прокрутку, чтобы влезало на любой экран
        inner = QWidget()
        content = QVBoxLayout(inner)
        content.setContentsMargins(4, 4, 12, 4)
        keys_group = self._keys_group()
        content.addWidget(keys_group)
        content.addWidget(self._hotkey_group())
        content.addWidget(self._text_group())
        content.addWidget(self._engine_group())
        content.addWidget(self._behaviour_group())
        content.addWidget(self._clipshot_group())
        content.addWidget(self._windows_group())
        content.addWidget(self._claude_group())
        content.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidget(inner)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)

        layout = QVBoxLayout(self)
        layout.addWidget(scroll, 1)

        screen = self.screen().availableGeometry() if self.screen() else None
        height = min(940, int(screen.height() * 0.86)) if screen else 860
        self.resize(660, height)

        if focus_keys:
            self.openai_key.edit.setFocus()

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._sync_enabled()

    # --------------------------------------------------------------- секции
    def _keys_group(self) -> QGroupBox:
        box = QGroupBox("Ключи API")
        form = QFormLayout(box)

        self.openai_key = KeyField(
            secrets.OPENAI, providers.check_openai, "sk-…"
        )
        form.addRow("OpenAI", self.openai_key)

        self.anthropic_key = KeyField(
            secrets.ANTHROPIC, providers.check_anthropic, "sk-ant-… (необязательно)"
        )
        form.addRow("Anthropic", self.anthropic_key)

        hint = QLabel(
            "Ключ OpenAI нужен для облачного распознавания и чистки текста — "
            "взять на platform.openai.com/api-keys. Anthropic нужен, только если "
            "выбрать Claude для обработки текста.\n"
            "Ключи шифруются средствами Windows под вашей учётной записью и "
            "хранятся в %APPDATA%\\Shepot."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888;")
        form.addRow("", hint)
        return box

    def _hotkey_group(self) -> QGroupBox:
        box = QGroupBox("Горячие клавиши")
        layout = QVBoxLayout(box)
        layout.setSpacing(12)

        cards = QHBoxLayout()
        self.mode_group = QButtonGroup(self)
        self.mode_cards: dict[str, ModeCard] = {}
        for key, title, hint in (
            ("toggle", "Переключатель", "нажал — старт, ещё раз — стоп"),
            ("ptt", "Удерживать", "держу — пишет, отпустил — стоп"),
            ("pair", "Две клавиши", "одна — старт, другая — стоп"),
        ):
            card = ModeCard(title, hint)
            self.mode_group.addButton(card)
            self.mode_cards[key] = card
            cards.addWidget(card)
        layout.addLayout(cards)
        current = self.cfg.get("hotkey_mode")
        self.mode_cards.get(current, self.mode_cards["toggle"]).setChecked(True)
        self.mode_group.buttonToggled.connect(lambda *_: self._sync_hotkeys())

        form = QFormLayout()
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.hotkey_capture = KeyCapture(self.cfg.get("hotkey"))
        self.hotkey_label = QLabel("Клавиша")
        form.addRow(self.hotkey_label, self.hotkey_capture)
        self.stop_capture = KeyCapture(self.cfg.get("hotkey_stop") or "")
        self.stop_label = QLabel("Закончить и вставить")
        form.addRow(self.stop_label, self.stop_capture)
        layout.addLayout(form)
        self.hotkey_capture.changed.connect(lambda _: self._sync_hotkeys())
        self.stop_capture.changed.connect(lambda _: self._sync_hotkeys())

        self.hotkey_verdict = QLabel()
        self.hotkey_verdict.setWordWrap(True)
        layout.addWidget(self.hotkey_verdict)

        bottom = QHBoxLayout()
        self.cancel_with_esc = QCheckBox("Esc отменяет запись без вставки")
        self.cancel_with_esc.setChecked(bool(self.cfg.get("cancel_key")))
        bottom.addWidget(self.cancel_with_esc)
        bottom.addStretch(1)
        reset = QPushButton("Вернуть Ctrl + Пробел")
        reset.clicked.connect(self._reset_hotkey)
        bottom.addWidget(reset)
        layout.addLayout(bottom)

        hint = QLabel(
            "Подходит любая клавиша или сочетание, одиночный правый Ctrl / Alt / Shift, "
            "а также средняя и боковые кнопки мыши. Сочетания перехватываются Шёпотом; "
            "одиночные модификаторы и кнопки мыши другие программы тоже видят."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888;")
        layout.addWidget(hint)

        self._sync_hotkeys()
        return box

    def _hotkey_mode(self) -> str:
        for key, card in self.mode_cards.items():
            if card.isChecked():
                return key
        return "toggle"

    def _reset_hotkey(self) -> None:
        self.mode_cards["toggle"].setChecked(True)
        self.hotkey_capture.set_value("ctrl+space")

    def _hotkey_problem(self) -> tuple[str, str]:
        """Итог проверки привязок: (ok | warn | bad, текст)."""
        mode = self._hotkey_mode()
        main = self.hotkey_capture.value()
        if not main:
            return "bad", "Назначьте клавишу: нажмите на поле, затем нужную клавишу."
        verdict = hotkeys.assess(main)
        if verdict[0] == "bad":
            return verdict
        if mode == "pair":
            stop = self.stop_capture.value()
            if not stop:
                return "bad", "Назначьте клавишу, которая заканчивает запись."
            stop_verdict = hotkeys.assess(stop, other=main)
            if stop_verdict[0] == "bad" or verdict[0] == "ok":
                verdict = stop_verdict if stop_verdict[0] != "ok" else verdict
        for value in [main] + ([self.stop_capture.value()] if mode == "pair" else []):
            taken = hotkeys.try_register(value)
            if taken:
                return "bad", taken
        if mode == "ptt" and main in hotkeys.MOUSE_BUTTONS and verdict[0] == "ok":
            return "ok", f"Готово. Держите {hotkeys.display(main)}, пока говорите."
        return verdict

    def _sync_hotkeys(self) -> None:
        mode = self._hotkey_mode()
        pair = mode == "pair"
        self.stop_label.setVisible(pair)
        self.stop_capture.setVisible(pair)
        self.hotkey_label.setText({"toggle": "Старт и стоп", "ptt": "Удерживать",
                                   "pair": "Начать запись"}[mode])
        kind, text = self._hotkey_problem()
        colors = {"ok": ("#1f8a4c", "rgba(31,138,76,0.12)"),
                  "warn": ("#a8620a", "rgba(168,98,10,0.12)"),
                  "bad": ("#c62f3a", "rgba(198,47,58,0.12)")}
        fg, bg = colors[kind]
        self.hotkey_verdict.setText(text)
        self.hotkey_verdict.setStyleSheet(
            f"QLabel {{ color: {fg}; background: {bg}; border-radius: 8px; padding: 8px 12px; }}"
        )

    def _text_group(self) -> QGroupBox:
        box = QGroupBox("Обработка текста")
        form = QFormLayout(box)

        self.mode = QComboBox()
        _fill(self.mode, MODE_LABELS, self.cfg.get("mode"))
        self.mode.currentIndexChanged.connect(self._sync_enabled)
        form.addRow("Режим", self.mode)

        self.tone = QComboBox()
        _fill(self.tone, TONE_LABELS, self.cfg.get("tone"))
        form.addRow("Тон", self.tone)

        self.source_language = QComboBox()
        _fill(self.source_language, SOURCE_LANGUAGES, self.cfg.get("source_language"))
        form.addRow("Язык речи", self.source_language)

        self.latin_terms = QComboBox()
        _fill(self.latin_terms, [(True, "Латиницей: deploy, Figma"),
                                 (False, "Как слышатся: деплой, фигма")],
              bool(self.cfg.get("latin_terms")))
        self.latin_terms.setToolTip("Как писать английские слова и названия в русской речи.")
        form.addRow("Английские слова", self.latin_terms)

        self.target_language = QComboBox()
        _fill(
            self.target_language,
            sorted(LANGUAGE_NAMES.items(), key=lambda p: p[1]),
            self.cfg.get("target_language"),
        )
        form.addRow("Переводить на", self.target_language)

        self.vocabulary = QPlainTextEdit("\n".join(self.cfg.get("vocabulary") or []))
        self.vocabulary.setPlaceholderText("Одно имя, бренд или термин в строке")
        self.vocabulary.setFixedHeight(76)
        form.addRow("Словарь", self.vocabulary)

        self.learn_corrections = QCheckBox("Запоминать мои исправления названий и терминов")
        self.learn_corrections.setChecked(bool(self.cfg.get("learn_corrections")))
        self.learn_corrections.setToolTip(
            "Исправили «фигму» на «Figma» после вставки — слово попадёт в словарь,\n"
            "и в следующий раз распознается правильно. Текст окна читается только\n"
            "на этом компьютере и никуда не отправляется."
        )
        form.addRow("", self.learn_corrections)

        self.custom_instruction = QPlainTextEdit(self.cfg.get("custom_instruction") or "")
        self.custom_instruction.setPlaceholderText(
            "Например: всегда обращайся на «вы», не используй эмодзи"
        )
        self.custom_instruction.setFixedHeight(56)
        form.addRow("Своё правило", self.custom_instruction)
        return box

    def _engine_group(self) -> QGroupBox:
        box = QGroupBox("Движки")
        form = QFormLayout(box)

        self.stt_backend = QComboBox()
        _fill(self.stt_backend, STT_BACKENDS, self.cfg.get("stt_backend"))
        self.stt_backend.currentIndexChanged.connect(self._sync_enabled)
        form.addRow("Распознавание", self.stt_backend)

        self.stt_model = QLineEdit(self.cfg.get("stt_model"))
        form.addRow("Облачная модель", self.stt_model)

        self.local_model = QComboBox()
        _fill(self.local_model, [(m, m) for m in LOCAL_MODELS], self.cfg.get("local_model"))
        form.addRow("Локальная модель", self.local_model)

        self.local_device = QComboBox()
        _fill(self.local_device, LOCAL_DEVICES, self.cfg.get("local_device"))
        form.addRow("Считать на", self.local_device)

        self.llm_provider = QComboBox()
        _fill(self.llm_provider, LLM_PROVIDERS, self.cfg.get("llm_provider"))
        self.llm_provider.currentIndexChanged.connect(self._sync_enabled)
        form.addRow("Обработка текста", self.llm_provider)

        self.llm_model = QLineEdit(self.cfg.get("llm_model"))
        form.addRow("Модель OpenAI", self.llm_model)

        self.anthropic_model = QLineEdit(self.cfg.get("anthropic_model"))
        form.addRow("Модель Claude", self.anthropic_model)
        return box

    def _behaviour_group(self) -> QGroupBox:
        box = QGroupBox("Поведение")
        form = QFormLayout(box)

        self.device = QComboBox()
        self.device.addItem("Устройство по умолчанию", None)
        try:
            for index, name in audio.list_input_devices():
                self.device.addItem(name, index)
        except Exception:
            pass
        selected = self.device.findData(self.cfg.get("input_device"))
        self.device.setCurrentIndex(selected if selected >= 0 else 0)
        form.addRow("Микрофон", self.device)

        self.max_seconds = QSpinBox()
        self.max_seconds.setRange(5, 900)
        self.max_seconds.setSuffix(" с")
        self.max_seconds.setValue(int(self.cfg.get("max_seconds")))
        form.addRow("Максимум записи", self.max_seconds)

        self.insert_method = QComboBox()
        _fill(self.insert_method, INSERT_METHODS, self.cfg.get("insert_method"))
        self.insert_method.currentIndexChanged.connect(self._sync_enabled)
        form.addRow("Вставка", self.insert_method)

        self.restore_clipboard = QCheckBox("Возвращать прежнее содержимое буфера")
        self.restore_clipboard.setChecked(bool(self.cfg.get("restore_clipboard")))
        self.restore_clipboard.toggled.connect(self._sync_enabled)
        form.addRow("", self.restore_clipboard)

        self.restore_delay = QDoubleSpinBox()
        self.restore_delay.setRange(0.1, 5.0)
        self.restore_delay.setSingleStep(0.1)
        self.restore_delay.setDecimals(1)
        self.restore_delay.setSuffix(" с")
        self.restore_delay.setValue(float(self.cfg.get("restore_delay")))
        self.restore_delay.setToolTip(
            "Если медленное приложение вставляет старое содержимое буфера — увеличьте."
        )
        form.addRow("Возврат буфера через", self.restore_delay)

        self.voice_edit = QCheckBox("Правка выделенного текста по горячей клавише")
        self.voice_edit.setChecked(bool(self.cfg.get("voice_edit")))
        self.voice_edit.setToolTip(
            "Выделите текст и нажмите горячую клавишу: вместо записи откроется панель —\n"
            "выберите стиль или напишите, как изменить, и выделение заменится.\n"
            "В Windows Terminal результат встаёт в строку ввода. В редакторах кода и других\n"
            "терминалах не включается: там Ctrl+C прерывает процесс."
        )
        form.addRow("", self.voice_edit)

        self.hud = QCheckBox("Показывать индикатор поверх окон")
        self.hud.setChecked(bool(self.cfg.get("hud")))
        form.addRow("", self.hud)

        self.play_sounds = QCheckBox("Звуковые сигналы")
        self.play_sounds.setChecked(bool(self.cfg.get("play_sounds")))
        form.addRow("", self.play_sounds)

        self.autostart = QCheckBox("Запускать вместе с Windows")
        self.autostart.setChecked(startup.is_enabled())
        form.addRow("", self.autostart)
        return box

    def _claude_group(self) -> QGroupBox:
        box = QGroupBox("Claude Code")
        form = QFormLayout(box)
        self.claude_island = QCheckBox("Показывать сессии Claude Code в острове")
        self.claude_island.setChecked(bool(self.cfg.get("claude_island")))
        form.addRow("", self.claude_island)

        self.claude_status = QLabel()
        self.claude_status.setWordWrap(True)
        self.claude_connect = QPushButton()
        self.claude_connect.clicked.connect(self._toggle_claude_hooks)
        row = QHBoxLayout()
        row.addWidget(self.claude_connect)
        row.addWidget(self.claude_status, 1)
        form.addRow("Хуки", row)
        hint = QLabel(
            "Подключение дописывает хуки Шёпота в ~/.claude/settings.json (рядом кладётся "
            "резервная копия; чужие хуки не трогаются). Остров показывает, кто из сессий думает, "
            "закончил или ждёт разрешения, а разрешение можно дать прямо в нём. Пока Шёпот "
            "выключен, Claude Code работает как обычно, но пишет об ошибке хука — тогда отключите."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        form.addRow("", hint)
        self._sync_claude_hooks()
        return box

    def _sync_claude_hooks(self) -> None:
        installed = claude_hooks.is_installed()
        self.claude_connect.setText("Отключить от Claude Code" if installed else "Подключить к Claude Code")
        self.claude_status.setText("Подключено. Новые сессии Claude Code видны сразу, "
                                   "уже открытые — после перезапуска." if installed else "Не подключено.")

    def _toggle_claude_hooks(self) -> None:
        try:
            if claude_hooks.is_installed():
                claude_hooks.uninstall()
            else:
                claude_hooks.install()
        except (OSError, ValueError) as exc:
            self.claude_status.setText(f"Не получилось изменить настройки Claude Code: {exc}")
            return
        self._sync_claude_hooks()

    def _clipshot_group(self) -> QGroupBox:
        box = QGroupBox("Скриншоты в терминал")
        form = QFormLayout(box)

        self.clipshot = QCheckBox("Превращать картинку из буфера в путь к файлу")
        self.clipshot.setChecked(bool(self.cfg.get("clipshot")))
        self.clipshot.toggled.connect(self._sync_enabled)
        form.addRow("", self.clipshot)

        self.clipshot_dir = QLineEdit(self.cfg.get("clipshot_dir") or "")
        self.clipshot_dir.setPlaceholderText(str(clipshot.default_dir()))
        form.addRow("Папка", self.clipshot_dir)

        self.clipshot_keep_days = QSpinBox()
        self.clipshot_keep_days.setRange(0, 365)
        self.clipshot_keep_days.setSuffix(" дн.")
        self.clipshot_keep_days.setSpecialValueText("не удалять")
        self.clipshot_keep_days.setValue(int(self.cfg.get("clipshot_keep_days")))
        form.addRow("Хранить снимки", self.clipshot_keep_days)

        hint = QLabel(
            "Терминал не принимает картинку по Ctrl+V. Со включённым тумблером "
            "снимок с Win+Shift+S сохраняется в PNG, а в буфер вместо картинки "
            "ложится путь к нему — его и вставляет Ctrl+V.\n"
            "Тумблер есть и в меню значка в трее, чтобы включать на ходу."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888;")
        form.addRow("", hint)
        return box

    def _windows_group(self) -> QGroupBox:
        box = QGroupBox("Окна и проекты")
        form = QFormLayout(box)

        self.ghost = QCheckBox("Окно-призрак: Alt + колесо над окном меняет прозрачность")
        self.ghost.setChecked(bool(self.cfg.get("ghost")))
        self.ghost.setToolTip(
            "Шаг 10 %. Alt + средняя кнопка возвращает 100 %. На 30 % и ниже окно\n"
            "пропускает клики насквозь. Alt + колесо перестаёт доходить до программ\n"
            "(например, до быстрой прокрутки в VS Code или масштаба в Photoshop)."
        )
        form.addRow("", self.ghost)

        self.jump = QCheckBox("Прыжок в проект: поиск по папкам и запуск рабочего места")
        self.jump.setChecked(bool(self.cfg.get("jump")))
        self.jump.toggled.connect(self._sync_enabled)
        form.addRow("", self.jump)

        self.jump_capture = KeyCapture(self.cfg.get("jump_hotkey") or "win+j")
        form.addRow("Клавиша прыжка", self.jump_capture)

        self.jump_root = QLineEdit(self.cfg.get("jump_root") or "")
        self.jump_root.setPlaceholderText(str(jump.default_root()))
        form.addRow("Папка проектов", self.jump_root)

        hint = QLabel(
            "Прыжок открывает VS Code и Windows Terminal с вкладками Claude Code, Codex и сервера "
            "(npm run dev) и ставит окна рядом; уже открытые окна проекта не дублирует. Что "
            "открывать, отмечается галочками в самой панели (Alt + 1…5) и запоминается для "
            "проекта. Win + P занят Windows (второй экран), поэтому по умолчанию Win + J."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888;")
        form.addRow("", hint)
        return box

    def _jump_problem(self) -> str:
        if not self.jump.isChecked():
            return ""
        text = self.jump_capture.value()
        try:
            binding = hotkeys.parse_binding(text)
        except ValueError as exc:
            return f"не получилось разобрать сочетание: {exc}"
        if binding.raw:
            return "для прыжка нужно сочетание с обычной клавишей, например Win + J"
        if binding.text == hotkeys.parse_binding(self.hotkey_capture.value()).text:
            return "совпадает с клавишей диктовки"
        kind, message = hotkeys.assess(text)
        if kind == "bad":
            return message
        return hotkeys.try_register(text) or ""

    # ----------------------------------------------------------- активность
    def _sync_enabled(self) -> None:
        local = self.stt_backend.currentData() == "local"
        self.stt_model.setEnabled(not local)
        self.local_model.setEnabled(local)
        self.local_device.setEnabled(local)

        provider = self.llm_provider.currentData()
        processing = self.mode.currentData() != "raw" and provider != "none"
        self.llm_model.setEnabled(processing and provider == "openai")
        self.anthropic_model.setEnabled(processing and provider == "anthropic")
        self.tone.setEnabled(processing)
        self.custom_instruction.setEnabled(processing)
        self.target_language.setEnabled(self.mode.currentData() == "translate")

        uses_clipboard = self.insert_method.currentData() == "paste"
        self.restore_clipboard.setEnabled(uses_clipboard)
        self.restore_delay.setEnabled(uses_clipboard and self.restore_clipboard.isChecked())

        catching = self.clipshot.isChecked()
        self.clipshot_dir.setEnabled(catching)
        self.clipshot_keep_days.setEnabled(catching)

        jumping = self.jump.isChecked()
        self.jump_capture.setEnabled(jumping)
        self.jump_root.setEnabled(jumping)

    # -------------------------------------------------------------- сохранение
    def _accept(self) -> None:
        kind, problem = self._hotkey_problem()
        if kind == "bad":
            QMessageBox.warning(self, "Шёпот", f"Горячая клавиша: {problem}")
            return
        hotkey_mode = self._hotkey_mode()
        jump_problem = self._jump_problem()
        if jump_problem:
            QMessageBox.warning(self, "Шёпот", f"Клавиша прыжка: {jump_problem}")
            return

        vocabulary = [
            line.strip()
            for line in self.vocabulary.toPlainText().splitlines()
            if line.strip()
        ]

        self.cfg.update(
            {
                "hotkey": self.hotkey_capture.value(),
                "hotkey_stop": self.stop_capture.value() if hotkey_mode == "pair" else "",
                "hotkey_mode": hotkey_mode,
                "cancel_key": "esc" if self.cancel_with_esc.isChecked() else "",
                "mode": self.mode.currentData(),
                "tone": self.tone.currentData(),
                "source_language": self.source_language.currentData(),
                "latin_terms": bool(self.latin_terms.currentData()),
                "learn_corrections": self.learn_corrections.isChecked(),
                "target_language": self.target_language.currentData(),
                "vocabulary": vocabulary,
                # удалили слово из словаря — забываем и выученные замены на него
                "replacements": {
                    old: new for old, new in (self.cfg.get("replacements") or {}).items()
                    if new.lower() in {term.lower() for term in vocabulary}
                },
                "custom_instruction": self.custom_instruction.toPlainText().strip(),
                "stt_backend": self.stt_backend.currentData(),
                "stt_model": self.stt_model.text().strip() or "gpt-4o-transcribe",
                "local_model": self.local_model.currentData(),
                "local_device": self.local_device.currentData(),
                "llm_provider": self.llm_provider.currentData(),
                "llm_model": self.llm_model.text().strip() or "gpt-4o-mini",
                "anthropic_model": self.anthropic_model.text().strip() or "claude-opus-5",
                "input_device": self.device.currentData(),
                "max_seconds": self.max_seconds.value(),
                "insert_method": self.insert_method.currentData(),
                "restore_clipboard": self.restore_clipboard.isChecked(),
                "restore_delay": self.restore_delay.value(),
                "clipshot": self.clipshot.isChecked(),
                "claude_island": self.claude_island.isChecked(),
                "ghost": self.ghost.isChecked(),
                "jump": self.jump.isChecked(),
                "jump_hotkey": self.jump_capture.value() or "win+j",
                "jump_root": self.jump_root.text().strip(),
                "clipshot_dir": self.clipshot_dir.text().strip(),
                "clipshot_keep_days": self.clipshot_keep_days.value(),
                "voice_edit": self.voice_edit.isChecked(),
                "hud": self.hud.isChecked(),
                "play_sounds": self.play_sounds.isChecked(),
                "autostart": self.autostart.isChecked(),
            }
        )
        self.cfg.save()

        for field in (self.openai_key, self.anthropic_key):
            typed = field.value()
            # пустое поле не стирает ключ, взятый из переменной среды
            if typed or secrets.stored(field.name):
                secrets.set_key(field.name, typed)
        providers.reset()

        try:
            startup.set_enabled(self.autostart.isChecked())
        except OSError as exc:
            QMessageBox.warning(self, "Шёпот", f"Не удалось изменить автозапуск: {exc}")

        self.accept()
