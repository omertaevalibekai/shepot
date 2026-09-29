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
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import audio, clipshot, hotkeys, providers, secrets, startup
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


class HotkeyEdit(QLineEdit):
    """Поле, которое записывает нажатое сочетание клавиш."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setPlaceholderText("Нажмите сочетание…")

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta):
            return
        parts = []
        mods = event.modifiers()
        if mods & Qt.ControlModifier:
            parts.append("ctrl")
        if mods & Qt.AltModifier:
            parts.append("alt")
        if mods & Qt.ShiftModifier:
            parts.append("shift")
        if mods & Qt.MetaModifier:
            parts.append("windows")
        name = QKeySequence(key).toString().strip().lower()
        if not name:
            return
        parts.append(name)
        candidate = "+".join(parts)
        if hotkeys.is_valid(candidate):
            self.setText(candidate)


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
        box = QGroupBox("Горячая клавиша")
        form = QFormLayout(box)

        self.hotkey_edit = HotkeyEdit()
        self.hotkey_edit.setText(self.cfg.get("hotkey"))
        form.addRow("Сочетание", self.hotkey_edit)

        self.hotkey_mode = QComboBox()
        _fill(
            self.hotkey_mode,
            [("toggle", "Нажал — пишет, нажал ещё раз — вставляет"),
             ("ptt", "Держу — пишет, отпустил — вставляет")],
            self.cfg.get("hotkey_mode"),
        )
        form.addRow("Режим", self.hotkey_mode)

        hint = QLabel("Esc отменяет текущую диктовку.")
        hint.setStyleSheet("color: #777;")
        form.addRow("", hint)
        return box

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

    # -------------------------------------------------------------- сохранение
    def _accept(self) -> None:
        hotkey = self.hotkey_edit.text().strip()
        if not hotkeys.is_valid(hotkey):
            QMessageBox.warning(self, "Шёпот", "Укажите корректное сочетание клавиш.")
            return

        vocabulary = [
            line.strip()
            for line in self.vocabulary.toPlainText().splitlines()
            if line.strip()
        ]

        self.cfg.update(
            {
                "hotkey": hotkey,
                "hotkey_mode": self.hotkey_mode.currentData(),
                "mode": self.mode.currentData(),
                "tone": self.tone.currentData(),
                "source_language": self.source_language.currentData(),
                "target_language": self.target_language.currentData(),
                "vocabulary": vocabulary,
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
                "clipshot_dir": self.clipshot_dir.text().strip(),
                "clipshot_keep_days": self.clipshot_keep_days.value(),
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
