"""Виджеты в духе настроек iOS — в светлой и тёмной теме.

Цвета берутся из текущей темы (theme()) в момент построения и отрисовки,
поэтому окно настроек пересобирается при смене темы, как и при смене языка.
Палитры повторяют системные цвета iOS: сгруппированный фон, карточки,
разделители, вторичный текст, системный синий и зелёный.
"""

from __future__ import annotations

import ctypes
import math
import winreg
from dataclasses import dataclass

from PySide6.QtCore import (
    Property, QEasingCurve, QPointF, QPropertyAnimation, QRect, QRectF, QSize, Qt, QTimer,
    QVariantAnimation, Signal,
)
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QComboBox, QFrame, QHBoxLayout, QLabel, QLayout, QLineEdit, QPushButton,
    QSizePolicy, QToolButton, QVBoxLayout, QWidget, QWidgetItem,
)

FONT = "Segoe UI Variable Text"
DISPLAY = "Segoe UI Variable Display"


@dataclass(frozen=True)
class Theme:
    dark: bool
    bg: str            # фон раздела справа
    sidebar: str
    card: str
    raised: str        # поля, клавиши, нейтральные кнопки
    raised_hover: str
    ink: str
    muted: str
    faint: str
    line: str
    accent: str
    link: str
    switch_off: str
    switch_on: str
    seg_track: str
    seg_thumb: str
    chip: str
    chip_learned: str
    chip_learned_ink: str
    nav_hover: str
    shadow: int        # непрозрачность мягкой тени карточек
    ok: tuple[str, str]
    warn: tuple[str, str]
    bad: tuple[str, str]


LIGHT = Theme(
    dark=False, bg="#F2F2F7", sidebar="#F7F7FA", card="#FFFFFF", raised="#F2F2F7", raised_hover="#E8E8ED",
    ink="#1C1C1E", muted="#6C6C70", faint="#A1A1A6", line="#E5E5EA", accent="#0A84FF", link="#0A66D8",
    switch_off="#E3E3E8", switch_on="#34C759", seg_track="#EEEEF0", seg_thumb="#FFFFFF",
    chip="#F2F2F7", chip_learned="#F1ECFF", chip_learned_ink="#5B3FC4", nav_hover="#EBEBF0", shadow=10,
    ok=("#E3F7E8", "#1B7A35"), warn=("#FFF1DC", "#9A5B00"), bad=("#FDE7E7", "#B42318"),
)
DARK = Theme(
    dark=True, bg="#0E0E10", sidebar="#161618", card="#1C1C1E", raised="#2C2C2E", raised_hover="#3A3A3C",
    ink="#F5F5F7", muted="#98989F", faint="#636366", line="#2C2C2F", accent="#0A84FF", link="#64A8FF",
    switch_off="#39393D", switch_on="#30D158", seg_track="#2C2C2E", seg_thumb="#636366",
    chip="#2C2C2E", chip_learned="#2D2650", chip_learned_ink="#C3B3FF", nav_hover="#232326", shadow=0,
    ok=("#0F2A19", "#5BD68A"), warn=("#33240A", "#FFB340"), bad=("#3A1515", "#FF7B72"),
)

_theme = DARK


def windows_prefers_dark() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def set_theme(choice: str) -> Theme:
    """choice: auto | light | dark."""
    global _theme
    dark = windows_prefers_dark() if choice not in ("light", "dark") else choice == "dark"
    _theme = DARK if dark else LIGHT
    return _theme


def theme() -> Theme:
    return _theme


def font(size: float, weight: QFont.Weight = QFont.Normal, display: bool = False) -> QFont:
    f = QFont(DISPLAY if display else FONT)
    if not f.exactMatch():
        f = QFont("Segoe UI")
    f.setPointSizeF(size)
    f.setWeight(weight)
    return f


def label(text: str, size: float = 10.5, role: str = "ink", weight: QFont.Weight = QFont.Normal,
          wrap: bool = False, display: bool = False) -> QLabel:
    """role — имя цвета темы (ink, muted, faint, link…) или цвет как есть."""
    lab = QLabel(text)
    lab.setFont(font(size, weight, display))
    color = getattr(_theme, role, role)
    lab.setStyleSheet(f"color: {color}; background: transparent;")
    lab.setWordWrap(wrap)
    return lab


def dark_title_bar(widget: QWidget, color: str) -> None:
    """Рамка окна Windows 11 в тон сайдбару: тёмная в тёмной теме, светлая в светлой."""
    try:
        hwnd = int(widget.winId())
        dwm = ctypes.windll.dwmapi

        def colorref(value: str) -> ctypes.c_uint:
            c = QColor(value)
            return ctypes.c_uint(c.red() | (c.green() << 8) | (c.blue() << 16))

        on = ctypes.c_int(1 if _theme.dark else 0)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), 4)                   # USE_IMMERSIVE_DARK_MODE
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(colorref(color)), 4)      # CAPTION_COLOR
        dwm.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(colorref(_theme.ink)), 4)  # TEXT_COLOR
    except Exception:
        pass


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(int(a.red() + (b.red() - a.red()) * t), int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t), int(a.alpha() + (b.alpha() - a.alpha()) * t))


# --------------------------------------------------------------- переключатель

class Switch(QAbstractButton):
    """Переключатель iOS 51×31: дорожка перетекает в зелёный, ручка — с пружинкой."""

    def __init__(self, checked: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(51, 31)
        self.setFocusPolicy(Qt.StrongFocus)
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"pos", self)
        self._anim.setDuration(260)
        self._anim.setEasingCurve(QEasingCurve.OutBack)
        self.toggled.connect(self._animate)

    def _get_pos(self) -> float:
        return self._pos

    def _set_pos(self, value: float) -> None:
        self._pos = value
        self.update()

    pos = Property(float, _get_pos, _set_pos)

    def _animate(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def sizeHint(self) -> QSize:
        return QSize(51, 31)

    def paintEvent(self, _event) -> None:
        th = _theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        t = max(0.0, min(1.0, self._pos))
        track = _mix(QColor(th.switch_off), QColor(th.switch_on), t)
        if not self.isEnabled():
            track.setAlpha(110)
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, 51, 31), 15.5, 15.5)
        x = 2 + 20 * self._pos
        p.setBrush(QColor(0, 0, 0, 60 if th.dark else 34))
        p.drawEllipse(QRectF(x, 3.2, 27, 27))
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QRectF(x, 2, 27, 27))
        if self.hasFocus():
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(th.accent), 2))
            p.drawRoundedRect(QRectF(1, 1, 49, 29), 14.5, 14.5)


# ------------------------------------------------------------------- сегменты

class Segmented(QWidget):
    """Сегменты iOS: «таблетка» выбранного плавно переезжает к нажатому."""

    changed = Signal(object)
    PAD, H = 2, 32

    def __init__(self, options, value=None, parent=None) -> None:
        super().__init__(parent)
        self._options = list(options)
        self._keys = [k for k, _ in self._options]
        self._index = self._keys.index(value) if value in self._keys else 0
        self._font = font(10)
        self._bold = font(10, QFont.DemiBold)
        bold = QFontMetricsF(self._bold)
        self._widths = [bold.horizontalAdvance(text) + 30 for _, text in self._options]
        self._thumb = self._slot(self._index)
        self._hover = -1
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(240)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._moved)
        self.setFixedSize(int(sum(self._widths) + self.PAD * 2), self.H)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)

    def _slot(self, index: int) -> QRectF:
        x = self.PAD + sum(self._widths[:index])
        return QRectF(x, self.PAD, self._widths[index], self.H - self.PAD * 2)

    def _moved(self, rect) -> None:
        self._thumb = rect
        self.update()

    def value(self):
        return self._keys[self._index]

    def set_value(self, key, animate: bool = False) -> None:
        if key not in self._keys:
            return
        self._index = self._keys.index(key)
        target = self._slot(self._index)
        if animate:
            self._anim.stop()
            self._anim.setStartValue(self._thumb)
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self._thumb = target
        self.update()

    def _at(self, x: float) -> int:
        edge = self.PAD
        for i, w in enumerate(self._widths):
            edge += w
            if x < edge:
                return i
        return len(self._widths) - 1

    def mousePressEvent(self, event) -> None:
        self._pick(self._at(event.position().x()))

    def mouseMoveEvent(self, event) -> None:
        hover = self._at(event.position().x())
        if hover != self._hover:
            self._hover = hover
            self.update()

    def leaveEvent(self, _event) -> None:
        self._hover = -1
        self.update()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Left, Qt.Key_Right):
            step = -1 if event.key() == Qt.Key_Left else 1
            self._pick(max(0, min(len(self._keys) - 1, self._index + step)))
        else:
            super().keyPressEvent(event)

    def _pick(self, index: int) -> None:
        if index == self._index:
            return
        self.set_value(self._keys[index], animate=True)
        self.changed.emit(self._keys[index])

    def paintEvent(self, _event) -> None:
        th = _theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(th.seg_track))
        p.drawRoundedRect(QRectF(self.rect()), 9, 9)
        # тонкие разделители между невыбранными сегментами, как в iOS
        p.setPen(QPen(QColor(th.faint if th.dark else "#D1D1D6"), 1))
        for i in range(1, len(self._widths)):
            if i in (self._index, self._index + 1):
                continue
            x = self.PAD + sum(self._widths[:i])
            p.drawLine(QPointF(x, 9), QPointF(x, self.H - 9))
        thumb = self._thumb
        p.setPen(Qt.NoPen)
        if not th.dark:
            p.setBrush(QColor(0, 0, 0, 22))
            p.drawRoundedRect(thumb.translated(0, 1), 7, 7)
        p.setBrush(QColor(th.seg_thumb))
        p.drawRoundedRect(thumb, 7, 7)
        for i, (_key, text) in enumerate(self._options):
            slot = self._slot(i)
            selected = i == self._index
            p.setFont(self._bold if selected else self._font)
            color = QColor(th.ink)
            if not selected:
                color.setAlpha(255 if i == self._hover else (200 if th.dark else 190))
            p.setPen(color)
            p.drawText(slot, Qt.AlignCenter, text)
        if self.hasFocus():
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(th.accent), 1.5))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75), 9, 9)


# ------------------------------------------------------------- группы и строки

class Row(QWidget):
    """Строка группы: заголовок, подзаголовок и элемент справа (или под строкой)."""

    def __init__(self, title: str, trailing: QWidget | None = None, subtitle: str = "",
                 below: QWidget | None = None, parent=None) -> None:
        super().__init__(parent)
        self.title_text = title
        self.top_line = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 0, 0, 0)
        outer.setSpacing(0)
        line = QHBoxLayout()
        line.setContentsMargins(0, 10, 16, 10)
        line.setSpacing(14)
        texts = QVBoxLayout()
        texts.setSpacing(3)
        self.title = label(title, 10.8)
        self.title.setWordWrap(True)
        texts.addWidget(self.title)
        self.subtitle = label(subtitle, 9, "muted", wrap=True)
        self.subtitle.setVisible(bool(subtitle))
        texts.addWidget(self.subtitle)
        line.addLayout(texts, 1)
        if trailing is not None:
            line.addWidget(trailing, 0, Qt.AlignVCenter | Qt.AlignRight)
        outer.addLayout(line)
        if below is not None:
            holder = QVBoxLayout()
            holder.setContentsMargins(0, 0, 16, 14)
            holder.addWidget(below)
            outer.addLayout(holder)
        self.setMinimumHeight(50)

    def paintEvent(self, _event) -> None:
        if self.top_line:
            p = QPainter(self)
            p.setPen(QPen(QColor(_theme.line), 1))
            p.drawLine(16, 0, self.width(), 0)


class Card(QFrame):
    """Карточка группы: скругление 12, в светлой теме — мягкая тень, в тёмной — волосяной контур."""

    def paintEvent(self, _event) -> None:
        th = _theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(Qt.NoPen)
        if th.shadow:
            p.setBrush(QColor(0, 0, 0, th.shadow))
            p.drawRoundedRect(r.translated(0, 1), 12, 12)
        p.setBrush(QColor(th.card))
        if th.dark:
            p.setPen(QPen(QColor(255, 255, 255, 12), 1))
        p.drawRoundedRect(r, 12, 12)


class Group(QWidget):
    """Подпись сверху, карточка со строками и пояснение снизу."""

    def __init__(self, title: str = "", footer: str = "", parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.title = label(title.upper(), 8.6, "muted", QFont.DemiBold)
        self.title.setContentsMargins(16, 0, 0, 0)
        f = self.title.font()
        f.setLetterSpacing(QFont.AbsoluteSpacing, 0.6)
        self.title.setFont(f)
        self.title.setVisible(bool(title))
        layout.addWidget(self.title)
        self.card = Card()
        self.rows_layout = QVBoxLayout(self.card)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(0)
        layout.addWidget(self.card)
        self.footer = label(footer, 9, "muted", wrap=True)
        self.footer.setContentsMargins(16, 0, 16, 0)
        self.footer.setVisible(bool(footer))
        layout.addWidget(self.footer)
        self.rows: list[Row] = []

    def add(self, row: Row) -> Row:
        self.rows.append(row)
        self.rows_layout.addWidget(row)
        self.relayout()
        return row

    def set_footer(self, text: str) -> None:
        self.footer.setText(text)
        self.footer.setVisible(bool(text))

    def relayout(self) -> None:
        """Разделитель — над каждой видимой строкой, кроме первой."""
        first = True
        for row in self.rows:
            if row.isHidden():
                continue
            row.top_line = not first
            row.update()
            first = False


def show_row(row: Row, visible: bool) -> None:
    row.setVisible(visible)
    group = row.parentWidget().parentWidget() if row.parentWidget() else None
    if isinstance(group, Group):
        group.relayout()


# --------------------------------------------------------- поля «значением справа»

def flat_style() -> str:
    th = _theme
    return (
        f"QLineEdit, QSpinBox, QDoubleSpinBox {{ border: 0; background: transparent; color: {th.muted};"
        f" font-family: '{FONT}'; font-size: 10.5pt; padding: 4px 0; selection-background-color: {th.accent};"
        f" selection-color: #FFFFFF; }}"
        f"QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ color: {th.ink}; }}"
        "QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button,"
        " QDoubleSpinBox::down-button { width: 0; border: 0; }"
    )


def popup_style() -> str:
    th = _theme
    return (
        "QComboBox { border: 0; background: transparent; }"
        f"QComboBox QAbstractItemView {{ border: 1px solid {th.line}; border-radius: 10px;"
        f" background: {'#2C2C2E' if th.dark else th.card}; color: {th.ink};"
        f" selection-background-color: {th.accent}; selection-color: #FFFFFF; padding: 4px; outline: 0;"
        f" font-family: '{FONT}'; font-size: 10.5pt; }}"
    )


class ValueCombo(QComboBox):
    """Выбор значения, как строка iOS: значение справа и двойной шеврон всплывающего меню."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(popup_style())
        self._hover = False

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        th = _theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        p.setFont(font(10.5))
        p.setPen(QColor(th.ink if self._hover else th.muted))
        text = p.fontMetrics().elidedText(self.currentText(), Qt.ElideRight, int(r.width() - 24))
        p.drawText(r.adjusted(0, 0, -20, 0), Qt.AlignVCenter | Qt.AlignRight, text)
        pen = QPen(QColor(th.faint), 1.8)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        x, y = r.right() - 8, r.center().y()
        p.drawPolyline([QPointF(x - 3.5, y - 2), QPointF(x, y - 5.5), QPointF(x + 3.5, y - 2)])
        p.drawPolyline([QPointF(x - 3.5, y + 2), QPointF(x, y + 5.5), QPointF(x + 3.5, y + 2)])


def flat(widget: QWidget, width: int = 220, align_right: bool = True) -> QWidget:
    if not widget.inherits("QComboBox"):
        widget.setStyleSheet(flat_style())
    widget.setMinimumWidth(width)
    widget.setMaximumWidth(width)
    if hasattr(widget, "setAlignment") and align_right:
        widget.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    return widget


def pill_button(text: str, primary: bool = False, height: int = 30) -> QPushButton:
    th = _theme
    button = QPushButton(text)
    button.setCursor(Qt.PointingHandCursor)
    button.setFixedHeight(height)
    if primary:
        bg, fg, hover, press = th.accent, "#FFFFFF", "#2C95FF", "#0071E3"
    else:
        bg, fg, hover, press = th.raised, th.link, th.raised_hover, th.raised_hover
    button.setStyleSheet(
        f"QPushButton {{ border: 0; border-radius: {height // 2}px; padding: 0 15px; background: {bg};"
        f" color: {fg}; font-family: '{FONT}'; font-size: 10pt; font-weight: 600; }}"
        f"QPushButton:hover {{ background: {hover}; }}"
        f"QPushButton:pressed {{ background: {press}; }}"
        f"QPushButton:checked {{ background: {th.raised_hover}; }}"
        f"QPushButton:disabled {{ color: {th.faint}; }}"
    )
    return button


def badge(text: str, kind: str = "ok") -> QLabel:
    lab = QLabel(text)
    set_badge(lab, text, kind)
    lab.setFont(font(9, QFont.DemiBold))
    lab.setFixedHeight(24)
    return lab


def set_badge(lab: QLabel, text: str, kind: str) -> None:
    th = _theme
    colors = {"ok": th.ok, "warn": th.warn, "bad": th.bad, "": (th.raised, th.muted)}
    bg, fg = colors.get(kind, colors[""])
    lab.setText(text)
    lab.setVisible(bool(text))
    lab.setStyleSheet(f"QLabel {{ background: {bg}; color: {fg}; border-radius: 12px; padding: 0 10px; }}")


# ----------------------------------------------------------------- клавиши

def keycaps_width(parts: list[str]) -> float:
    m = QFontMetricsF(font(9.6, QFont.DemiBold))
    widths = [max(28.0, m.horizontalAdvance(part) + 18) for part in parts]
    return sum(widths) + 6 * max(0, len(widths) - 1)


def draw_keycaps(p: QPainter, rect: QRectF, parts: list[str], align=Qt.AlignRight) -> None:
    """Названия клавиш — отдельными «колпачками», как на клавиатуре."""
    th = _theme
    f = font(9.6, QFont.DemiBold)
    p.setFont(f)
    m = QFontMetricsF(f)
    widths = [max(28.0, m.horizontalAdvance(part) + 18) for part in parts]
    gap, h = 6.0, 26.0
    total = sum(widths) + gap * (len(widths) - 1)
    x = rect.right() - total if align == Qt.AlignRight else rect.left() + (rect.width() - total) / 2
    y = rect.center().y() - h / 2
    for part, w in zip(parts, widths):
        cap = QRectF(x, y, w, h)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 110 if th.dark else 34))
        p.drawRoundedRect(cap.translated(0, 1.5), 6, 6)
        p.setBrush(QColor("#3A3A3D" if th.dark else "#FFFFFF"))
        p.setPen(QPen(QColor(255, 255, 255, 16) if th.dark else QColor("#D6D6DB"), 0.8))
        p.drawRoundedRect(cap, 6, 6)
        p.setPen(QColor(th.ink))
        p.drawText(cap, Qt.AlignCenter, part)
        x += w + gap


# ------------------------------------------------------------- «пилюли» словаря

class FlowLayout(QLayout):
    """Элементы в строку с переносом — как теги."""

    def __init__(self, parent=None, spacing: int = 8) -> None:
        super().__init__(parent)
        self._items: list = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def insertWidget(self, index: int, widget: QWidget) -> None:
        self.addChildWidget(widget)
        self._items.insert(index, QWidgetItem(widget))
        self.invalidate()

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._place(QRect(0, 0, width, 0), dry=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._place(rect, dry=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _place(self, rect: QRect, dry: bool) -> int:
        x, y, line_h = rect.x(), rect.y(), 0
        for item in self._items:
            if item.widget() is not None and item.widget().isHidden():
                continue
            hint = item.sizeHint()
            if x + hint.width() > rect.right() + 1 and line_h > 0:
                x, y, line_h = rect.x(), y + line_h + self._spacing, 0
            if not dry:
                item.setGeometry(QRect(QPointF(x, y).toPoint(), hint))
            x += hint.width() + self._spacing
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y()


class Chip(QFrame):
    removed = Signal(str)

    def __init__(self, text: str, learned: bool = False, parent=None) -> None:
        super().__init__(parent)
        th = _theme
        self.text = text
        bg, fg = (th.chip_learned, th.chip_learned_ink) if learned else (th.chip, th.ink)
        self.setObjectName("chip")
        self.setStyleSheet(f"#chip {{ background: {bg}; border-radius: 15px; }}")
        self.setFixedHeight(30)
        row = QHBoxLayout(self)
        row.setContentsMargins(13, 0, 6, 0)
        row.setSpacing(3)
        row.addWidget(label(text, 10, fg))
        close = QToolButton()
        close.setText("×")
        close.setCursor(Qt.PointingHandCursor)
        close.setToolTip(text)
        close.setStyleSheet(f"QToolButton {{ border: 0; color: {th.faint}; font-size: 13pt; padding: 0 3px; }}"
                            f"QToolButton:hover {{ color: {th.ink}; }}")
        close.clicked.connect(lambda: self.removed.emit(self.text))
        row.addWidget(close)


class ChipsEditor(QWidget):
    """Словарь «пилюлями»: × удаляет, «+ Добавить» превращается в поле ввода."""

    changed = Signal()

    def __init__(self, terms: list[str], learned: set[str], add_text: str, placeholder: str,
                 parent=None) -> None:
        super().__init__(parent)
        th = _theme
        self._learned = {t.lower() for t in learned}
        self._terms: list[str] = []
        self._flow = FlowLayout(self)
        self._add = QPushButton("+  " + add_text)
        self._add.setCursor(Qt.PointingHandCursor)
        self._add.setFixedHeight(30)
        self._add.setStyleSheet(
            f"QPushButton {{ border: 1px dashed {th.faint}; border-radius: 15px; padding: 0 13px;"
            f" background: transparent; color: {th.link}; font-family: '{FONT}'; font-size: 10pt; }}"
            f"QPushButton:hover {{ border-color: {th.accent}; }}")
        self._add.clicked.connect(self._start_add)
        self._field = QLineEdit()
        self._field.setPlaceholderText(placeholder)
        self._field.setFixedHeight(30)
        self._field.setMinimumWidth(200)
        self._field.setStyleSheet(
            f"QLineEdit {{ border: 1.5px solid {th.accent}; border-radius: 15px; padding: 0 13px;"
            f" background: {th.raised}; color: {th.ink}; font-family: '{FONT}'; font-size: 10pt; }}")
        self._field.hide()
        self._field.returnPressed.connect(self._commit)
        self._field.editingFinished.connect(self._commit)
        self._flow.addWidget(self._add)
        self._flow.addWidget(self._field)
        for term in terms:
            self._insert(term)

    def terms(self) -> list[str]:
        return list(self._terms)

    def _insert(self, term: str) -> None:
        term = term.strip()
        if not term or term.lower() in {t.lower() for t in self._terms}:
            return
        chip = Chip(term, term.lower() in self._learned)
        chip.removed.connect(self._remove)
        self._flow.insertWidget(len(self._terms), chip)
        self._terms.append(term)
        self.updateGeometry()

    def _remove(self, term: str) -> None:
        for i in range(self._flow.count()):
            widget = self._flow.itemAt(i).widget()
            if isinstance(widget, Chip) and widget.text == term:
                self._flow.takeAt(i)
                widget.deleteLater()
                break
        self._terms = [t for t in self._terms if t != term]
        self.updateGeometry()
        self.changed.emit()

    def _start_add(self) -> None:
        self._add.hide()
        self._field.show()
        self._field.setFocus()

    def _commit(self) -> None:
        if self._field.isHidden():
            return
        for part in self._field.text().replace(";", ",").split(","):
            self._insert(part)
        self._field.clear()
        self._field.hide()
        self._add.show()
        self.updateGeometry()
        self.changed.emit()

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._flow.heightForWidth(width)

    def sizeHint(self) -> QSize:
        return QSize(400, self._flow.heightForWidth(max(200, self.width())))


# ---------------------------------------------------------------- сайдбар

def icon_square(p: QPainter, box: QRectF, color: QColor, radius: float) -> None:
    """Цветной квадрат значка с лёгким градиентом сверху вниз, как у системных значков iOS."""
    grad = QLinearGradient(box.topLeft(), box.bottomLeft())
    grad.setColorAt(0, color.lighter(118))
    grad.setColorAt(1, color)
    path = QPainterPath()
    path.addRoundedRect(box, radius, radius)
    p.fillPath(path, grad)


class NavButton(QAbstractButton):
    """Пункт сайдбара: цветной значок и название; выбранный — системным синим."""

    def __init__(self, text: str, color: str, icon_path: str, parent=None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setText(text)
        self.color = QColor(color)
        self.icon_path = icon_path
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(42)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._hover = False

    def enterEvent(self, _event) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, _event) -> None:
        self._hover = False
        self.update()

    def paintEvent(self, _event) -> None:
        th = _theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        on = self.isChecked()
        if on or self._hover:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(th.accent) if on else QColor(th.nav_hover))
            p.drawRoundedRect(r, 10, 10)
        box = QRectF(10, (r.height() - 28) / 2, 28, 28)
        icon_square(p, box, self.color, 7)
        draw_icon(p, self.icon_path, box.adjusted(4.5, 4.5, -4.5, -4.5))
        p.setPen(QColor("#FFFFFF") if on else QColor(th.ink))
        p.setFont(font(10.5, QFont.DemiBold if on else QFont.Normal))
        p.drawText(r.adjusted(50, 0, -8, 0), Qt.AlignVCenter | Qt.AlignLeft, self.text())


def draw_icon(p: QPainter, spec: str, box: QRectF) -> None:
    """Значок из простых команд в квадрате 24×24: M x y L x y … ; Z; C cx cy r (окружность)."""
    p.save()
    k = box.width() / 24.0
    pen = QPen(QColor("#FFFFFF"), 2.3 * k)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    for part in spec.split(";"):
        tokens = part.split()
        if not tokens:
            continue
        if tokens[0] == "C":
            cx, cy, rad = (float(v) for v in tokens[1:4])
            p.drawEllipse(QPointF(box.left() + cx * k, box.top() + cy * k), rad * k, rad * k)
            continue
        path = QPainterPath()
        i = 0
        while i < len(tokens):
            cmd = tokens[i]
            if cmd in ("M", "L"):
                pt = QPointF(box.left() + float(tokens[i + 1]) * k, box.top() + float(tokens[i + 2]) * k)
                path.moveTo(pt) if cmd == "M" else path.lineTo(pt)
                i += 3
            elif cmd == "Z":
                path.closeSubpath()
                i += 1
            else:
                i += 1
        p.drawPath(path)
    p.restore()


# ------------------------------------------------------------ живой остров

class IslandPreview(QWidget):
    """Чёрная капсула записи с живыми столбиками — как остров Шёпота на экране."""

    HEIGHTS = (8, 14, 20, 12, 18, 22, 10, 16, 21, 9, 15, 19, 11, 7)

    def __init__(self, caption: str, parent=None) -> None:
        super().__init__(parent)
        self.caption = caption
        self.setFixedHeight(150)
        self._t = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)

    def _tick(self) -> None:
        if self.isVisible():
            self._t += 0.033
            self.update()

    def paintEvent(self, _event) -> None:
        th = _theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        grad = QLinearGradient(r.topLeft(), r.bottomLeft())
        if th.dark:
            grad.setColorAt(0, QColor("#1D2230"))
            grad.setColorAt(1, QColor("#15171D"))
        else:
            grad.setColorAt(0, QColor("#DCE6F7"))
            grad.setColorAt(1, QColor("#EEF1F8"))
        path = QPainterPath()
        path.addRoundedRect(r, 14, 14)
        p.fillPath(path, grad)
        if th.dark:
            p.setPen(QPen(QColor(255, 255, 255, 12), 1))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)

        cap = QRectF(0, 0, 236, 46)
        cap.moveCenter(QPointF(r.center().x(), r.top() + 58))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 80))
        p.drawRoundedRect(cap.translated(0, 6), 23, 23)
        p.setBrush(QColor("#0B0B0F"))
        p.setPen(QPen(QColor(255, 255, 255, 22), 1))
        p.drawRoundedRect(cap, 23, 23)
        lamp = QPointF(cap.left() + 22, cap.center().y())
        glow = 0.5 + 0.5 * math.sin(self._t * 3.0)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 159, 10, int(50 + 60 * glow)))
        p.drawEllipse(lamp, 9, 9)
        p.setBrush(QColor("#FF9F0A"))
        p.drawEllipse(lamp, 4.5, 4.5)
        p.setFont(QFont("Consolas", 10))
        p.setPen(QColor("#FF9F0A"))
        seconds = int(self._t) % 60
        p.drawText(QRectF(cap.left() + 36, cap.top(), 50, cap.height()), Qt.AlignVCenter, f"0:{seconds:02d}")
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#FFFFFF"))
        for i, h in enumerate(self.HEIGHTS):
            k = 0.35 + 0.65 * abs(math.sin(self._t * (2.2 + (i % 5) * 0.4) + i * 0.7))
            height = max(3.0, h * k)
            x = cap.right() - 22 - (len(self.HEIGHTS) - 1 - i) * 5.4
            p.drawRoundedRect(QRectF(x, cap.center().y() - height / 2, 2.6, height), 1.3, 1.3)
        p.setFont(font(9.5))
        p.setPen(QColor(th.muted if th.dark else "#3A3A3F"))
        p.drawText(QRectF(r.left(), cap.bottom() + 16, r.width(), 22), Qt.AlignHCenter | Qt.AlignTop, self.caption)
