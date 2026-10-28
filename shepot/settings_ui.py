"""Окно настроек в духе iOS: разделы слева, группы-карточки справа, светлая и тёмная тема.

Значения читаются из виджетов только при сохранении: окно закрывается кнопкой
«Готово», крестиком или Esc — и всё сохраняется (как в настройках iPhone).
Смена языка или оформления сохраняет введённое и пересобирает окно заново.
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRectF, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDoubleSpinBox, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QScrollArea, QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from . import __version__, audio, claude_hooks, clipshot, hotkeys, i18n, ios, jump, logo, providers, secrets, startup
from .i18n import t
from .polish import LANGUAGE_NAMES


def mode_labels() -> list[tuple[str, str]]:
    return [("clean", t("Чистовик")), ("raw", t("Как есть")), ("translate", t("Перевод"))]


def tone_labels() -> list[tuple[str, str]]:
    return [("preserve", t("Моя манера")), ("neutral", t("Нейтральный")),
            ("casual", t("Разговорный")), ("formal", t("Деловой"))]


LOCAL_MODELS = ["tiny", "base", "small", "medium", "large-v3"]

# значки разделов: команды в квадрате 24×24 (см. ios.draw_icon)
ICONS = {
    "general": "C 12 12 3; M 12 2.5 L 12 5.5; M 12 18.5 L 12 21.5; M 2.5 12 L 5.5 12; M 18.5 12 L 21.5 12;"
               " M 5.3 5.3 L 7.4 7.4; M 16.6 16.6 L 18.7 18.7; M 5.3 18.7 L 7.4 16.6; M 16.6 7.4 L 18.7 5.3",
    "keys": "M 3 6 L 21 6 L 21 18 L 3 18 Z; M 7 14 L 17 14; M 7 10 L 7.2 10; M 11 10 L 11.2 10; M 15 10 L 15.2 10",
    "text": "M 5 7 L 5 5 L 19 5 L 19 7; M 12 5 L 12 19; M 9 19 L 15 19",
    "engine": "M 3.5 11 L 3.5 13; M 7.5 8.5 L 7.5 15.5; M 11.5 5 L 11.5 19; M 15.5 8 L 15.5 16; M 19.5 10.5 L 19.5 13.5",
    "insert": "M 9 4 L 15 4; M 9 20 L 15 20; M 12 4 L 12 20",
    "claude": "M 12 3 L 13.8 8.2 L 19 10 L 13.8 11.8 L 12 17 L 10.2 11.8 L 5 10 L 10.2 8.2 Z",
    "windows": "M 4 5 L 11 5 L 11 11 L 4 11 Z; M 13 5 L 20 5 L 20 11 L 13 11 Z;"
               " M 4 13 L 11 13 L 11 19 L 4 19 Z; M 13 13 L 20 13 L 20 19 L 13 19 Z",
    "shots": "M 4 8 L 4 5 L 7 5; M 17 5 L 20 5 L 20 8; M 20 16 L 20 19 L 17 19; M 7 19 L 4 19 L 4 16; M 9 12 L 15 12",
}
COLORS = {"general": "#8E8E93", "keys": "#FF9F0A", "text": "#0A84FF", "engine": "#BF5AF2",
          "insert": "#30D158", "claude": "#D97757", "windows": "#5E5CE6", "shots": "#32ADE6"}
THEMES = (("auto", "Как в Windows"), ("dark", "Тёмная"), ("light", "Светлая"))


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
    """Поле клавиши: колпачки клавиш; нажал на него — оно ждёт клавишу или кнопку мыши.

    Сочетание фиксируется, когда отпущены все клавиши: так одиночный
    модификатор (правый Ctrl) тоже можно назначить. Esc без других клавиш
    отменяет запись, клик мимо — тоже.
    """

    changed = Signal(str)

    def __init__(self, value: str = "", parent=None) -> None:
        super().__init__(parent)
        self._value = value
        self._listening = False
        self._hover = False
        self._held: list[str] = []
        self._peak: list[str] = []
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(36)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFlat(True)
        self.clicked.connect(self._start)
        self._resize()

    def value(self) -> str:
        return self._value

    def set_value(self, value: str) -> None:
        self._value = value
        self._resize()
        self.changed.emit(value)

    def _resize(self) -> None:
        if self._listening:
            width = self.fontMetrics().horizontalAdvance(t("Нажмите сочетание…  Esc — отмена")) + 40
        elif self._value:
            width = ios.keycaps_width(hotkeys.display_parts(self._value)) + 12
        else:
            width = self.fontMetrics().horizontalAdvance(t("Назначить")) + 40
        self.setFixedWidth(int(max(110, width)))
        self.update()

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        th = ios.theme()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self._listening:
            accent = QColor(th.accent)
            fill = QColor(accent)
            fill.setAlpha(40)
            p.setBrush(fill)
            p.setPen(QPen(accent, 1.5))
            p.drawRoundedRect(r, 10, 10)
            p.setPen(QColor(th.link))
            p.setFont(ios.font(10))
            p.drawText(r, Qt.AlignCenter, t("Нажмите сочетание…  Esc — отмена"))
            return
        if not self._value:
            p.setPen(QPen(QColor(th.accent if self._hover or self.hasFocus() else th.faint), 1, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r, 10, 10)
            p.setPen(QColor(th.link))
            p.setFont(ios.font(10, QFont.DemiBold))
            p.drawText(r, Qt.AlignCenter, t("Назначить"))
            return
        if self._hover or self.hasFocus():
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(th.raised))
            p.drawRoundedRect(r, 10, 10)
        ios.draw_keycaps(p, r.adjusted(0, 0, -5, 0), hotkeys.display_parts(self._value))
        if not self.isEnabled():
            veil = QColor(th.card)
            veil.setAlpha(150)
            p.fillRect(self.rect(), veil)

    def _start(self) -> None:
        if self._listening:
            return
        self._listening = True
        self._held, self._peak = [], []
        self._resize()
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
        self._resize()

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
    """Ключ API в строке: скрытое поле, «Показать», «Проверить» и метка результата."""

    def __init__(self, name: str, checker, placeholder: str, parent=None) -> None:
        super().__init__(parent)
        th = ios.theme()
        self.name = name
        self._checker = checker
        self._thread: _CheckThread | None = None
        self.row: ios.Row | None = None              # строка, где показать текст ошибки

        line = QHBoxLayout(self)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(8)
        self.edit = QLineEdit()
        self.edit.setEchoMode(QLineEdit.Password)
        self.edit.setText(secrets.stored(name))
        if not self.edit.text() and secrets.is_from_environment(name):
            self.edit.setPlaceholderText(t("из переменной среды"))
        else:
            self.edit.setPlaceholderText(placeholder)
        self.edit.setFixedSize(190, 30)
        self.edit.setStyleSheet(
            f"QLineEdit {{ border: 1.5px solid transparent; border-radius: 9px; background: {th.raised};"
            f" padding: 0 10px; font-family: Consolas, monospace; font-size: 10pt; color: {th.ink}; }}"
            f"QLineEdit:focus {{ border-color: {th.accent}; }}")
        self.edit.textEdited.connect(lambda: self._say("", None))
        line.addWidget(self.edit)

        self.reveal = ios.pill_button(t("Показать"))
        self.reveal.setCheckable(True)
        self.reveal.toggled.connect(self._toggle_reveal)
        line.addWidget(self.reveal)
        self.check_button = ios.pill_button(t("Проверить"))
        self.check_button.clicked.connect(self._check)
        line.addWidget(self.check_button)
        self.status = ios.badge("", "")
        line.addWidget(self.status)

    def value(self) -> str:
        return self.edit.text().strip()

    def _toggle_reveal(self, shown: bool) -> None:
        self.edit.setEchoMode(QLineEdit.Normal if shown else QLineEdit.Password)
        self.reveal.setText(t("Скрыть") if shown else t("Показать"))

    def _check(self) -> None:
        key = self.value() or secrets.get(self.name)
        if not key:
            self._say(t("Сначала вставьте ключ."), False)
            return
        self.check_button.setEnabled(False)
        self._say(t("Проверяю…"), None)
        self._thread = _CheckThread(self._checker, key, self)
        self._thread.done.connect(self._checked)
        self._thread.start()

    def _checked(self, ok: bool, message: str) -> None:
        self.check_button.setEnabled(True)
        self._say(message, ok)

    def _say(self, message: str, ok: bool | None) -> None:
        th = ios.theme()
        if ok is None:
            ios.set_badge(self.status, message, "")
        else:
            ios.set_badge(self.status, t("Работает") if ok else t("Ошибка"), "ok" if ok else "bad")
        if self.row is not None:
            self.row.subtitle.setText(message if ok is not None else self.row.property("hint") or "")
            self.row.subtitle.setVisible(bool(self.row.subtitle.text()))
            color = th.bad[1] if ok is False else th.muted
            self.row.subtitle.setStyleSheet(f"color: {color}; background: transparent;")


class LogoMark(QWidget):
    def __init__(self, size: int = 46, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        logo.draw(p, QRectF(self.rect()))


def _combo(pairs, current, width: int = 220) -> QComboBox:
    combo = ios.ValueCombo()
    for value, text in pairs:
        combo.addItem(text, value)
    index = combo.findData(current)
    combo.setCurrentIndex(index if index >= 0 else 0)
    ios.flat(combo, width)
    return combo


def _line(text: str, placeholder: str = "", width: int = 240) -> QLineEdit:
    edit = QLineEdit(text or "")
    edit.setPlaceholderText(placeholder)
    ios.flat(edit, width)
    return edit


class Page(QScrollArea):
    """Раздел справа: крупный заголовок, строка пояснения, группы."""

    def __init__(self, title: str, lead: str) -> None:
        super().__init__()
        th = ios.theme()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        handle = "rgba(255,255,255,0.18)" if th.dark else "rgba(0,0,0,0.22)"
        self.setStyleSheet(f"QScrollArea {{ background: {th.bg}; border: 0; }}"
                           "QScrollBar:vertical { width: 10px; background: transparent; margin: 6px 2px; }"
                           f"QScrollBar::handle:vertical {{ background: {handle}; border-radius: 3px; min-height: 40px; }}"
                           "QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page,"
                           " QScrollBar::sub-page { height: 0; background: transparent; }")
        inner = QWidget()
        inner.setObjectName("pageInner")
        inner.setStyleSheet(f"#pageInner {{ background: {th.bg}; }}")
        outer = QHBoxLayout(inner)
        outer.setContentsMargins(40, 30, 40, 40)
        column = QWidget()
        column.setMaximumWidth(660)
        self.body = QVBoxLayout(column)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(26)
        head = QVBoxLayout()
        head.setSpacing(6)
        title_label = ios.label(title, 24, "ink", QFont.Bold, display=True)
        f = title_label.font()
        f.setLetterSpacing(QFont.PercentageSpacing, 98)
        title_label.setFont(f)
        head.addWidget(title_label)
        head.addWidget(ios.label(lead, 10.5, "muted", wrap=True))
        self.body.addLayout(head)
        outer.addStretch(1)
        outer.addWidget(column, 100)
        outer.addStretch(1)
        self.setWidget(inner)
        self.row_titles: list[str] = []

    def group(self, title: str = "", footer: str = "") -> ios.Group:
        g = ios.Group(title, footer)
        if title:
            self.row_titles.append(title)                # поиск находит раздел и по названию группы
        self.body.addWidget(g)
        return g

    def finish(self) -> None:
        self.body.addStretch(1)


class HeroCard(ios.Card):
    """Карточка приложения наверху «Основного» — как карточка Apple ID в настройках iPhone."""

    def __init__(self, key_ok: bool) -> None:
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(18, 16, 18, 16)
        row.setSpacing(16)
        row.addWidget(LogoMark(64))
        texts = QVBoxLayout()
        texts.setSpacing(3)
        texts.addStretch(1)
        texts.addWidget(ios.label(t("Шёпот"), 16, "ink", QFont.DemiBold, display=True))
        texts.addWidget(ios.label(t("Голос в текст в любом окне · версия {v}", v=__version__), 9.5, "muted"))
        texts.addStretch(1)
        row.addLayout(texts, 1)
        row.addWidget(ios.badge(t("Ключ OpenAI подключён") if key_ok else t("Нужен ключ OpenAI"),
                                "ok" if key_ok else "warn"), 0, Qt.AlignVCenter)


class SettingsDialog(QDialog):
    def __init__(self, cfg, parent=None, focus_keys: bool = False) -> None:
        super().__init__(parent)
        self.cfg = cfg
        ios.set_theme(cfg.get("ui_theme") or "dark")
        self.setMinimumSize(920, 620)
        screen = self.screen().availableGeometry() if self.screen() else None
        self.resize(1060, min(780, int(screen.height() * 0.88)) if screen else 780)
        self._root = QHBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(0)
        self._closing = False
        self._section = "general"
        self._fade: QPropertyAnimation | None = None
        self._build("general")
        if focus_keys:
            self.openai_key.edit.setFocus()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        ios.dark_title_bar(self, ios.theme().sidebar)

    # ---------------------------------------------------------------- каркас
    def _build(self, section: str) -> None:
        th = ios.theme()
        while self._root.count():
            item = self._root.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.setWindowTitle(t("Шёпот — настройки"))
        self.setStyleSheet(f"QDialog {{ background: {th.bg}; }}"
                           f"QToolTip {{ color: {th.ink}; background: {th.raised}; border: 1px solid {th.line};"
                           " border-radius: 6px; padding: 4px 8px; }")

        self.stack = QStackedWidget()
        self.stack.setStyleSheet(f"QStackedWidget {{ background: {th.bg}; }}")
        self.pages: dict[str, Page] = {}
        self.nav: dict[str, ios.NavButton] = {}
        sections = [
            ("general", t("Основное"), t("Язык, оформление, ключи доступа к моделям и то, как Шёпот ведёт себя на компьютере."), self._general),
            ("keys", t("Горячие клавиши"), t("Любая клавиша, сочетание, одиночный правый Ctrl или боковая кнопка мыши."), self._hotkeys),
            ("text", t("Текст"), t("Что модель делает с расшифровкой, прежде чем текст попадёт в окно."), self._text),
            ("engine", t("Распознавание"), t("Где ваш голос превращается в текст и какая модель его чистит."), self._engine),
            ("insert", t("Вставка"), t("Как готовый текст попадает в окно, где стоит курсор."), self._insert),
            ("claude", "Claude Code", t("Остров показывает, что делают сессии, и даёт ответить им, не открывая терминал."), self._claude),
            ("windows", t("Окна и проекты"), t("Прозрачные окна и быстрый переход к проекту."), self._windows),
            ("shots", t("Скриншоты"), t("Чтобы Ctrl+V вставлял путь к снимку там, где картинку не вставить."), self._shots),
        ]
        for key, title, lead, build in sections:
            page = Page(title, lead)
            build(page)
            page.finish()
            self.pages[key] = page
            self.stack.addWidget(page)

        self._root.addWidget(self._sidebar(sections))
        self._root.addWidget(self.stack, 1)
        self._sync()
        self._open(section if section in self.pages else "general", animate=False)
        if self.isVisible():
            ios.dark_title_bar(self, th.sidebar)

    def _sidebar(self, sections) -> QWidget:
        th = ios.theme()
        side = QFrame()
        side.setObjectName("side")
        side.setFixedWidth(280)
        side.setStyleSheet(f"#side {{ background: {th.sidebar}; border-right: 1px solid {th.line}; }}")
        col = QVBoxLayout(side)
        col.setContentsMargins(14, 18, 14, 16)
        col.setSpacing(12)

        head = QHBoxLayout()
        head.setContentsMargins(6, 0, 0, 4)
        head.setSpacing(10)
        head.addWidget(LogoMark(30))
        head.addWidget(ios.label(t("Настройки"), 15, "ink", QFont.Bold, display=True), 1)
        col.addLayout(head)

        self.search = QLineEdit()
        self.search.setPlaceholderText(t("Поиск"))
        self.search.setClearButtonEnabled(True)
        self.search.setFixedHeight(36)
        self.search.setStyleSheet(
            f"QLineEdit {{ border: 1.5px solid transparent; border-radius: 10px; background: {th.raised};"
            f" padding: 0 12px; font-family: '{ios.FONT}'; font-size: 10.5pt; color: {th.ink}; }}"
            f"QLineEdit:focus {{ border-color: {th.accent}; }}")
        self.search.textChanged.connect(self._filter)
        col.addWidget(self.search)

        nav = QVBoxLayout()
        nav.setSpacing(2)
        for key, title, _lead, _build in sections:
            button = ios.NavButton(title, COLORS[key], ICONS[key])
            button.clicked.connect(lambda _=False, k=key: self._open(k))
            self.nav[key] = button
            nav.addWidget(button)
            if key == "general":
                nav.addSpacing(8)                    # «Основное» отдельно, как первая группа в iOS
        self.nothing = ios.label(t("Ничего не нашлось"), 10, "muted")
        self.nothing.setContentsMargins(12, 10, 0, 0)
        self.nothing.hide()
        nav.addWidget(self.nothing)
        col.addLayout(nav)
        col.addStretch(1)

        hint = ios.label(t("Изменения сохраняются, когда вы закрываете окно."), 8.8, "faint", wrap=True)
        hint.setContentsMargins(6, 0, 6, 0)
        col.addWidget(hint)
        done = ios.pill_button(t("Готово"), primary=True, height=38)
        done.clicked.connect(self._accept)
        col.addWidget(done)
        return side

    def _open(self, key: str, animate: bool = True) -> None:
        for name, button in self.nav.items():
            button.setChecked(name == key)
        page = self.pages[key]
        changed = self.stack.currentWidget() is not page
        self.stack.setCurrentWidget(page)
        self._section = key
        if animate and changed:
            effect = QGraphicsOpacityEffect(page)
            page.setGraphicsEffect(effect)
            self._fade = QPropertyAnimation(effect, b"opacity", self)
            self._fade.setDuration(170)
            self._fade.setStartValue(0.0)
            self._fade.setEndValue(1.0)
            self._fade.setEasingCurve(QEasingCurve.OutCubic)
            self._fade.finished.connect(lambda p=page: p.setGraphicsEffect(None))
            self._fade.start()

    def _filter(self, text: str) -> None:
        query = text.strip().lower()
        shown = []
        for key, button in self.nav.items():
            page = self.pages[key]
            hit = not query or query in button.text().lower() or any(query in r.lower() for r in page.row_titles)
            button.setVisible(hit)
            if hit:
                shown.append(key)
        self.nothing.setVisible(not shown)
        if shown and self._section not in shown:
            self._open(shown[0])

    def _row(self, page: Page, group: ios.Group, title: str, trailing=None, subtitle: str = "",
             below=None) -> ios.Row:
        page.row_titles.append(title)
        return group.add(ios.Row(title, trailing, subtitle, below))

    def _switch(self, key: str) -> ios.Switch:
        return ios.Switch(bool(self.cfg.get(key)))

    # -------------------------------------------------------------- разделы
    def _general(self, page: Page) -> None:
        page.body.addWidget(HeroCard(bool(secrets.get(secrets.OPENAI))))

        g = page.group(t("Оформление"))
        choice = self.cfg.get("ui_language") or "auto"
        self.ui_language = ios.Segmented([(code, t(name) if code == "auto" else name)
                                          for code, name in i18n.LANGUAGES], choice)
        self.ui_language.changed.connect(self._language_changed)
        self._row(page, g, t("Язык"), self.ui_language)
        self.ui_theme = ios.Segmented([(code, t(name)) for code, name in THEMES], self.cfg.get("ui_theme") or "dark")
        self.ui_theme.changed.connect(self._theme_changed)
        self._row(page, g, t("Тема"), self.ui_theme)

        g = page.group(t("Ключи"), t("Ключи шифруются средствами Windows под вашей учётной записью и уходят "
                                     "только в OpenAI и Anthropic. Ключ OpenAI — на platform.openai.com/api-keys."))
        self.openai_key = KeyField(secrets.OPENAI, providers.check_openai, "sk-…")
        row = self._row(page, g, "OpenAI", self.openai_key, t("Распознавание и чистка текста"))
        row.setProperty("hint", t("Распознавание и чистка текста"))
        self.openai_key.row = row
        self.anthropic_key = KeyField(secrets.ANTHROPIC, providers.check_anthropic, "sk-ant-…")
        row = self._row(page, g, "Anthropic", self.anthropic_key, t("Нужен, только если чистить текст через Claude"))
        row.setProperty("hint", t("Нужен, только если чистить текст через Claude"))
        self.anthropic_key.row = row

        g = page.group(t("Запуск"))
        self.autostart = ios.Switch(startup.is_enabled())
        self._row(page, g, t("Запускать вместе с Windows"), self.autostart)
        self.hud = self._switch("hud")
        self._row(page, g, t("Показывать остров"), self.hud, t("Капсула внизу экрана: запись, обработка, результат"))
        self.play_sounds = self._switch("play_sounds")
        self._row(page, g, t("Звуки"), self.play_sounds)
        self.check_updates = self._switch("check_updates")
        self._row(page, g, t("Сообщать о новых версиях"), self.check_updates, t("Раз в сутки спрашивать GitHub о релизе"))

    def _hotkeys(self, page: Page) -> None:
        self.island = ios.IslandPreview("")
        page.body.addWidget(self.island)

        g = self.hotkey_group = page.group(t("Диктовка"))
        self.hotkey_mode = ios.Segmented([("toggle", t("Переключатель")), ("ptt", t("Удержание")),
                                          ("pair", t("Две клавиши"))], self.cfg.get("hotkey_mode"))
        self.hotkey_mode.changed.connect(lambda _: self._sync())
        self._row(page, g, t("Режим"), self.hotkey_mode)
        self.hotkey_capture = KeyCapture(self.cfg.get("hotkey"))
        self.hotkey_capture.changed.connect(lambda _: self._sync())
        self.hotkey_row = self._row(page, g, t("Начать диктовку"), self.hotkey_capture)
        self.stop_capture = KeyCapture(self.cfg.get("hotkey_stop") or "")
        self.stop_capture.changed.connect(lambda _: self._sync())
        self.stop_row = self._row(page, g, t("Закончить и вставить"), self.stop_capture)
        reset = ios.pill_button(t("Ctrl + Пробел"))
        reset.clicked.connect(self._reset_hotkey)
        self._row(page, g, t("Вернуть как было"), reset)

        self.hotkey_verdict = QLabel()
        self.hotkey_verdict.setWordWrap(True)
        self.hotkey_verdict.setFont(ios.font(9.5))
        page.body.addWidget(self.hotkey_verdict)

        g = page.group(t("Отмена"), t("Подходит любая клавиша или сочетание, одиночный правый Ctrl / Alt / Shift, "
                                      "а также средняя и боковые кнопки мыши. Сочетания перехватываются Шёпотом; "
                                      "одиночные модификаторы и кнопки мыши другие программы тоже видят."))
        self.cancel_with_esc = ios.Switch(bool(self.cfg.get("cancel_key")))
        self._row(page, g, t("Esc отменяет запись"), self.cancel_with_esc,
                  t("Выключите, если Esc нужен в программе, куда диктуете"))

    def _text(self, page: Page) -> None:
        g = self.text_group = page.group(t("Обработка"))
        self.mode = ios.Segmented(mode_labels(), self.cfg.get("mode"))
        self.mode.changed.connect(lambda _: self._sync())
        self._row(page, g, t("Режим"), self.mode)
        self.target_language = _combo(sorted(LANGUAGE_NAMES.items(), key=lambda p: p[1]),
                                      self.cfg.get("target_language"), 180)
        self.target_row = self._row(page, g, t("Переводить на"), self.target_language)
        self.tone = ios.Segmented(tone_labels(), self.cfg.get("tone"))
        self.tone_row = self._row(page, g, t("Тон"), self.tone)
        self.custom_instruction = _line(self.cfg.get("custom_instruction"), t("например: обращайся на «вы»"), 260)
        self.custom_row = self._row(page, g, t("Своё правило"), self.custom_instruction)

        learned = set((self.cfg.get("replacements") or {}).values())
        g = page.group(t("Словарь"), t("Фиолетовые слова Шёпот выучил сам: вы исправили их после вставки."))
        self.vocabulary = ios.ChipsEditor(list(self.cfg.get("vocabulary") or []), learned,
                                          t("Добавить"), t("Имя, бренд или термин, Enter"))
        self._row(page, g, t("Имена и термины"), below=self.vocabulary)
        self.learn_corrections = self._switch("learn_corrections")
        self._row(page, g, t("Учиться на моих исправлениях"), self.learn_corrections)

        g = self.hints_group = page.group(t("Подсказки модели"))
        self.latin_terms = self._switch("latin_terms")
        self._row(page, g, t("Английские слова латиницей"), self.latin_terms, t("deploy, pull request, Figma — а не «деплой»"))
        self.window_context = self._switch("window_context")
        self.window_context.toggled.connect(lambda _: self._sync())
        self._row(page, g, t("Учитывать текст окна"), self.window_context,
                  t("Имена из переписки пишутся правильно с первого раза"))
        self.live_transcribe = self._switch("live_transcribe")
        self._row(page, g, t("Распознавать по ходу речи"), self.live_transcribe,
                  t("Длинная диктовка готова почти сразу после остановки"))

    def _engine(self, page: Page) -> None:
        g = self.engine_group = page.group(t("Распознавание"))
        self.stt_backend = ios.Segmented([("openai", t("Облако")), ("local", t("На компьютере"))],
                                         self.cfg.get("stt_backend"))
        self.stt_backend.changed.connect(lambda _: self._sync())
        self._row(page, g, t("Где распознавать"), self.stt_backend)
        self.stt_model = _line(self.cfg.get("stt_model"), "gpt-4o-transcribe", 200)
        self.stt_model_row = self._row(page, g, t("Модель"), self.stt_model)
        self.local_model = _combo([(m, m) for m in LOCAL_MODELS], self.cfg.get("local_model"), 140)
        self.local_model_row = self._row(page, g, t("Модель"), self.local_model)
        self.local_device = _combo([("auto", t("Автоматически")), ("cuda", t("Видеокарта (CUDA)")),
                                    ("cpu", t("Процессор"))], self.cfg.get("local_device"), 180)
        self.local_device_row = self._row(page, g, t("Считать на"), self.local_device)
        self.source_language = _combo([("auto", t("Определять сам"))] + sorted(LANGUAGE_NAMES.items(), key=lambda p: p[1]),
                                      self.cfg.get("source_language"), 180)
        self._row(page, g, t("Язык речи"), self.source_language)

        g = page.group(t("Чистка текста"))
        self.llm_provider = ios.Segmented([("openai", "OpenAI"), ("anthropic", "Claude"), ("none", t("Без обработки"))],
                                          self.cfg.get("llm_provider"))
        self.llm_provider.changed.connect(lambda _: self._sync())
        self._row(page, g, t("Чем чистить"), self.llm_provider)
        self.llm_model = _line(self.cfg.get("llm_model"), "gpt-4o-mini", 200)
        self.llm_model_row = self._row(page, g, t("Модель OpenAI"), self.llm_model)
        self.anthropic_model = _line(self.cfg.get("anthropic_model"), "claude-opus-5", 200)
        self.anthropic_model_row = self._row(page, g, t("Модель Claude"), self.anthropic_model)

        g = page.group(t("Микрофон"))
        pairs = [(None, t("Устройство по умолчанию"))]
        try:
            pairs += [(index, name) for index, name in audio.list_input_devices()]
        except Exception:
            pass
        self.device = _combo(pairs, self.cfg.get("input_device"), 260)
        self._row(page, g, t("Устройство"), self.device)
        self.max_seconds = QSpinBox()
        self.max_seconds.setRange(5, 900)
        self.max_seconds.setSuffix(" " + t("с"))
        self.max_seconds.setValue(int(self.cfg.get("max_seconds")))
        ios.flat(self.max_seconds, 90)
        self._row(page, g, t("Длиннее не записывать"), self.max_seconds)

    def _insert(self, page: Page) -> None:
        g = self.insert_group = page.group(t("Способ"))
        self.insert_method = ios.Segmented([("paste", t("Буфер")), ("type", t("Набором")),
                                            ("clipboard", t("Только копировать"))], self.cfg.get("insert_method"))
        self.insert_method.changed.connect(lambda _: self._sync())
        self._row(page, g, t("Вставлять"), self.insert_method)
        self.restore_clipboard = self._switch("restore_clipboard")
        self.restore_clipboard.toggled.connect(lambda _: self._sync())
        self.restore_row = self._row(page, g, t("Возвращать прежний буфер"), self.restore_clipboard)
        self.restore_delay = QDoubleSpinBox()
        self.restore_delay.setRange(0.1, 5.0)
        self.restore_delay.setSingleStep(0.1)
        self.restore_delay.setDecimals(1)
        self.restore_delay.setSuffix(" " + t("с"))
        self.restore_delay.setValue(float(self.cfg.get("restore_delay")))
        ios.flat(self.restore_delay, 90)
        self.delay_row = self._row(page, g, t("Вернуть через"), self.restore_delay,
                                   t("Увеличьте, если медленное приложение вставляет старый буфер"))

        g = page.group(t("Правка выделенного"), t("В Windows Terminal результат встаёт в строку ввода. В редакторах "
                                                   "кода и других терминалах правка не включается: там Ctrl+C "
                                                   "прерывает процесс."))
        self.voice_edit = self._switch("voice_edit")
        self._row(page, g, t("Править выделенный текст"), self.voice_edit,
                  t("Выделите текст и нажмите клавишу диктовки: Деловой, Дружелюбный, Короче или своя просьба"))

    def _claude(self, page: Page) -> None:
        g = page.group(t("Остров"), t("Хуки дописываются в ~/.claude/settings.json, рядом кладётся резервная "
                                      "копия; чужие хуки не трогаются. Пока Шёпот выключен, Claude Code пишет "
                                      "об ошибке хука — тогда отключите."))
        self.claude_island = self._switch("claude_island")
        self._row(page, g, t("Показывать сессии в острове"), self.claude_island)
        self.claude_connect = ios.pill_button("")
        self.claude_connect.clicked.connect(self._toggle_claude_hooks)
        self.claude_status = self._row(page, g, t("Хуки"), self.claude_connect, " ")

        g = page.group(t("Ответ голосом"), t("Текст уходит в терминал сессии, которая ждёт ответа или только что "
                                             "закончила, и отправляется Enter. Окно переключать не нужно."))
        self.claude_reply = self._switch("claude_reply")
        self.claude_reply.toggled.connect(lambda _: self._sync())
        self._row(page, g, t("Отвечать сессии голосом"), self.claude_reply)
        self.claude_reply_capture = KeyCapture(self.cfg.get("claude_reply_hotkey") or "ctrl+alt+space")
        self.reply_row = self._row(page, g, t("Клавиша ответа"), self.claude_reply_capture)
        self._sync_claude_hooks()

    def _windows(self, page: Page) -> None:
        g = page.group(t("Окна-призраки"), t("Шаг 10 %. Alt + средняя кнопка возвращает 100 %. На 30 % и ниже "
                                             "клики проходят сквозь окно. Alt + колесо перестаёт доходить до программ."))
        self.ghost = self._switch("ghost")
        self._row(page, g, t("Alt + колесо — прозрачность"), self.ghost)

        g = page.group(t("Прыжок в проект"), t("Открывает VS Code и Windows Terminal с вкладками Claude Code, Codex "
                                               "и сервера и ставит окна рядом; уже открытые окна не дублирует. "
                                               "Win + P занят Windows, поэтому по умолчанию Win + J."))
        self.jump = self._switch("jump")
        self.jump.toggled.connect(lambda _: self._sync())
        self._row(page, g, t("Поиск проекта по клавише"), self.jump)
        self.jump_capture = KeyCapture(self.cfg.get("jump_hotkey") or "win+j")
        self.jump_key_row = self._row(page, g, t("Клавиша"), self.jump_capture)
        self.jump_root = _line(self.cfg.get("jump_root"), str(jump.default_root()), 280)
        self.jump_root_row = self._row(page, g, t("Папка проектов"), self.jump_root)

    def _shots(self, page: Page) -> None:
        g = page.group("", t("Win+Shift+S — и в буфере окажется путь к PNG вместо картинки. Тумблер есть и в "
                             "меню значка в трее, чтобы включать на ходу."))
        self.clipshot = self._switch("clipshot")
        self.clipshot.toggled.connect(lambda _: self._sync())
        self._row(page, g, t("Картинка из буфера → путь к файлу"), self.clipshot)
        self.clipshot_dir = _line(self.cfg.get("clipshot_dir"), str(clipshot.default_dir()), 280)
        self.clipshot_dir_row = self._row(page, g, t("Папка"), self.clipshot_dir)
        self.clipshot_keep_days = QSpinBox()
        self.clipshot_keep_days.setRange(0, 365)
        self.clipshot_keep_days.setSuffix(" " + t("дн."))
        self.clipshot_keep_days.setSpecialValueText(t("не удалять"))
        self.clipshot_keep_days.setValue(int(self.cfg.get("clipshot_keep_days")))
        ios.flat(self.clipshot_keep_days, 110)
        self.clipshot_keep_row = self._row(page, g, t("Хранить снимки"), self.clipshot_keep_days)

    # ----------------------------------------------------------- состояние
    def _sync(self) -> None:
        th = ios.theme()
        mode = self.hotkey_mode.value()
        self.hotkey_row.title.setText({"toggle": t("Старт и стоп"), "ptt": t("Удерживать"),
                                       "pair": t("Начать запись")}[mode])
        ios.show_row(self.stop_row, mode == "pair")
        kind, text = self._hotkey_problem()
        bg, fg = {"ok": th.ok, "warn": th.warn, "bad": th.bad}[kind]
        self.hotkey_verdict.setText(text)
        self.hotkey_verdict.setStyleSheet(f"QLabel {{ color: {fg}; background: {bg}; border-radius: 12px;"
                                          " padding: 11px 16px; }")
        key = hotkeys.display(self.hotkey_capture.value()) or "—"
        self.island.caption = (t("Держите {key}, пока говорите", key=key) if mode == "ptt"
                               else t("Так выглядит запись. Начать — {key}", key=key))

        text_mode = self.mode.value()
        provider = self.llm_provider.value()
        processing = text_mode != "raw" and provider != "none"
        ios.show_row(self.target_row, text_mode == "translate")
        ios.show_row(self.tone_row, processing)
        ios.show_row(self.custom_row, processing)
        self.text_group.set_footer({"raw": t("Текст вставляется ровно так, как распознан."),
                                    "translate": t("Говорите на любом языке — вставится на выбранном."),
                                    }.get(text_mode, t("Убирает «эээ», повторы и запинки, расставляет пунктуацию."))
                                   if provider != "none" or text_mode == "raw" else
                                   t("Чистка выключена в «Распознавание → Чистка текста»."))
        self.hints_group.set_footer(
            t("Текст из окна уходит модели вместе с диктовкой. Терминалы, редакторы кода и менеджеры паролей "
              "не читаются никогда.") if self.window_context.isChecked()
            else t("Контекст окна выключен: модель видит только то, что вы сказали."))

        local = self.stt_backend.value() == "local"
        ios.show_row(self.stt_model_row, not local)
        ios.show_row(self.local_model_row, local)
        ios.show_row(self.local_device_row, local)
        self.engine_group.set_footer(t("Без интернета, на видеокарте или процессоре. Нужен faster-whisper и запуск "
                                       "из исходников.") if local else "")
        ios.show_row(self.llm_model_row, provider == "openai")
        ios.show_row(self.anthropic_model_row, provider == "anthropic")

        method = self.insert_method.value()
        ios.show_row(self.restore_row, method == "paste")
        ios.show_row(self.delay_row, method == "paste" and self.restore_clipboard.isChecked())
        self.insert_group.set_footer({"type": t("Печатает посимвольно — медленнее, зато буфер обмена не трогается."),
                                      "clipboard": t("Текст только кладётся в буфер — вставляете сами Ctrl+V."),
                                      }.get(method, t("Самый быстрый способ: через буфер обмена и Ctrl+V.")))

        ios.show_row(self.reply_row, self.claude_reply.isChecked())
        ios.show_row(self.jump_key_row, self.jump.isChecked())
        ios.show_row(self.jump_root_row, self.jump.isChecked())
        ios.show_row(self.clipshot_dir_row, self.clipshot.isChecked())
        ios.show_row(self.clipshot_keep_row, self.clipshot.isChecked())

    def _language_changed(self, code: str) -> None:
        if not self._save(close=False, language=code):
            self.ui_language.set_value(self.cfg.get("ui_language") or "auto")
            return
        i18n.set_language(code)
        self._build(self._section)

    def _theme_changed(self, code: str) -> None:
        if not self._save(close=False, theme=code):
            self.ui_theme.set_value(self.cfg.get("ui_theme") or "dark")
            return
        ios.set_theme(code)
        self._build(self._section)

    def _reset_hotkey(self) -> None:
        self.hotkey_mode.set_value("toggle", animate=True)
        self.hotkey_capture.set_value("ctrl+space")

    def _hotkey_problem(self) -> tuple[str, str]:
        """Итог проверки привязок: (ok | warn | bad, текст)."""
        mode = self.hotkey_mode.value()
        main = self.hotkey_capture.value()
        if not main:
            return "bad", t("Назначьте клавишу: нажмите на поле, затем нужную клавишу.")
        verdict = hotkeys.assess(main)
        if verdict[0] == "bad":
            return verdict
        if mode == "pair":
            stop = self.stop_capture.value()
            if not stop:
                return "bad", t("Назначьте клавишу, которая заканчивает запись.")
            stop_verdict = hotkeys.assess(stop, other=main)
            if stop_verdict[0] == "bad" or verdict[0] == "ok":
                verdict = stop_verdict if stop_verdict[0] != "ok" else verdict
        for value in [main] + ([self.stop_capture.value()] if mode == "pair" else []):
            taken = hotkeys.try_register(value)
            if taken:
                return "bad", taken
        if mode == "ptt" and main in hotkeys.MOUSE_BUTTONS and verdict[0] == "ok":
            return "ok", t("Готово. Держите {key}, пока говорите.", key=hotkeys.display(main))
        return verdict

    def _jump_problem(self) -> str:
        if not self.jump.isChecked():
            return ""
        text = self.jump_capture.value()
        try:
            binding = hotkeys.parse_binding(text)
        except ValueError as exc:
            return t("не получилось разобрать сочетание: {error}", error=exc)
        if binding.raw:
            return t("для прыжка нужно сочетание с обычной клавишей, например Win + J")
        if binding.text == hotkeys.parse_binding(self.hotkey_capture.value()).text:
            return t("совпадает с клавишей диктовки")
        kind, message = hotkeys.assess(text)
        if kind == "bad":
            return message
        return hotkeys.try_register(text) or ""

    def _sync_claude_hooks(self) -> None:
        installed = claude_hooks.is_installed()
        self.claude_connect.setText(t("Отключить") if installed else t("Подключить"))
        self.claude_status.subtitle.setText(
            t("Подключены. Новые сессии видны сразу, открытые — после перезапуска.") if installed
            else t("Не подключены: остров не видит сессии Claude Code."))
        self.claude_status.subtitle.show()

    def _toggle_claude_hooks(self) -> None:
        try:
            if claude_hooks.is_installed():
                claude_hooks.uninstall()
            else:
                claude_hooks.install()
        except (OSError, ValueError) as exc:
            self.claude_status.subtitle.setText(t("Не получилось изменить настройки Claude Code: {error}", error=exc))
            return
        self._sync_claude_hooks()

    # -------------------------------------------------------------- сохранение
    def reject(self) -> None:
        self._accept()                              # крестик и Esc тоже сохраняют, как в iOS

    def closeEvent(self, event) -> None:
        if self._closing:
            event.accept()
            return
        event.ignore()
        self._accept()

    def _accept(self) -> None:
        if self._save(close=True):
            self._closing = True
            self.accept()

    def _save(self, close: bool, language: str | None = None, theme: str | None = None) -> bool:
        kind, problem = self._hotkey_problem()
        if kind == "bad":
            self._open("keys")
            QMessageBox.warning(self, t("Шёпот"), t("Горячая клавиша: {problem}", problem=problem))
            return False
        hotkey_mode = self.hotkey_mode.value()
        jump_problem = self._jump_problem()
        if jump_problem:
            self._open("windows")
            QMessageBox.warning(self, t("Шёпот"), t("Клавиша прыжка: {problem}", problem=jump_problem))
            return False

        vocabulary = self.vocabulary.terms()
        self.cfg.update({
            "ui_language": language or self.ui_language.value(),
            "ui_theme": theme or self.ui_theme.value(),
            "hotkey": self.hotkey_capture.value(),
            "hotkey_stop": self.stop_capture.value() if hotkey_mode == "pair" else "",
            "hotkey_mode": hotkey_mode,
            "cancel_key": "esc" if self.cancel_with_esc.isChecked() else "",
            "mode": self.mode.value(),
            "tone": self.tone.value(),
            "source_language": self.source_language.currentData(),
            "latin_terms": self.latin_terms.isChecked(),
            "learn_corrections": self.learn_corrections.isChecked(),
            "target_language": self.target_language.currentData(),
            "vocabulary": vocabulary,
            # удалили слово из словаря — забываем и выученные замены на него
            "replacements": {
                old: new for old, new in (self.cfg.get("replacements") or {}).items()
                if new.lower() in {term.lower() for term in vocabulary}
            },
            "custom_instruction": self.custom_instruction.text().strip(),
            "stt_backend": self.stt_backend.value(),
            "stt_model": self.stt_model.text().strip() or "gpt-4o-transcribe",
            "local_model": self.local_model.currentData(),
            "local_device": self.local_device.currentData(),
            "llm_provider": self.llm_provider.value(),
            "llm_model": self.llm_model.text().strip() or "gpt-4o-mini",
            "anthropic_model": self.anthropic_model.text().strip() or "claude-opus-5",
            "input_device": self.device.currentData(),
            "max_seconds": self.max_seconds.value(),
            "insert_method": self.insert_method.value(),
            "restore_clipboard": self.restore_clipboard.isChecked(),
            "restore_delay": self.restore_delay.value(),
            "clipshot": self.clipshot.isChecked(),
            "claude_island": self.claude_island.isChecked(),
            "claude_reply": self.claude_reply.isChecked(),
            "claude_reply_hotkey": self.claude_reply_capture.value() or "ctrl+alt+space",
            "ghost": self.ghost.isChecked(),
            "check_updates": self.check_updates.isChecked(),
            "jump": self.jump.isChecked(),
            "jump_hotkey": self.jump_capture.value() or "win+j",
            "jump_root": self.jump_root.text().strip(),
            "clipshot_dir": self.clipshot_dir.text().strip(),
            "clipshot_keep_days": self.clipshot_keep_days.value(),
            "voice_edit": self.voice_edit.isChecked(),
            "window_context": self.window_context.isChecked(),
            "live_transcribe": self.live_transcribe.isChecked(),
            "hud": self.hud.isChecked(),
            "play_sounds": self.play_sounds.isChecked(),
            "autostart": self.autostart.isChecked(),
        })
        self.cfg.save()

        for field in (self.openai_key, self.anthropic_key):
            typed = field.value()
            # пустое поле не стирает ключ, взятый из переменной среды
            if typed or secrets.stored(field.name):
                secrets.set_key(field.name, typed)
        providers.reset()

        if close:
            try:
                startup.set_enabled(self.autostart.isChecked())
            except OSError as exc:
                QMessageBox.warning(self, t("Шёпот"), t("Не удалось изменить автозапуск: {error}", error=exc))
        return True
