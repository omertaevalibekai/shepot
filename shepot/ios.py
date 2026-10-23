"""Виджеты в духе настроек iOS: переключатель, сегменты, группы-карточки, строки, «пилюли».

Светлая тема: серый фон окна, белые скруглённые группы, тонкие разделители
с отступом слева, подписи над группой и пояснения под ней.
"""

from __future__ import annotations

from PySide6.QtCore import (
    Property, QEasingCurve, QPointF, QPropertyAnimation, QRect, QRectF, QSize, Qt, Signal,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QButtonGroup, QComboBox, QFrame, QHBoxLayout, QLabel, QLayout, QLineEdit,
    QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget, QWidgetItem,
)

BG = "#F2F2F7"
SIDEBAR = "#F7F7FA"
CARD = "#FFFFFF"
INK = "#1C1C1E"
MUTED = "#6C6C70"
LINE = "#E5E5EA"
ACCENT = "#0A84FF"
ACCENT_TEXT = "#0A66D8"
GREEN = "#34C759"
FONT = "Segoe UI Variable Text"


def font(size: float, weight: QFont.Weight = QFont.Normal) -> QFont:
    f = QFont(FONT)
    if not f.exactMatch():
        f = QFont("Segoe UI")
    f.setPointSizeF(size)
    f.setWeight(weight)
    return f


def label(text: str, size: float = 10.5, color: str = INK, weight: QFont.Weight = QFont.Normal,
          wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    lab.setFont(font(size, weight))
    lab.setStyleSheet(f"color: {color}; background: transparent;")
    lab.setWordWrap(wrap)
    return lab


# --------------------------------------------------------------- переключатель

class Switch(QAbstractButton):
    """Зелёный переключатель iOS 51×31 с пружинкой."""

    def __init__(self, checked: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(51, 31)
        self.setFocusPolicy(Qt.StrongFocus)
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"pos", self)
        self._anim.setDuration(220)
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
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        t = max(0.0, min(1.0, self._pos))
        off, on = QColor("#E3E3E8"), QColor(GREEN)
        track = QColor(int(off.red() + (on.red() - off.red()) * t),
                       int(off.green() + (on.green() - off.green()) * t),
                       int(off.blue() + (on.blue() - off.blue()) * t))
        if not self.isEnabled():
            track.setAlpha(110)
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, 51, 31), 15.5, 15.5)
        x = 2 + 20 * self._pos
        p.setBrush(QColor(0, 0, 0, 30))
        p.drawEllipse(QRectF(x, 3.5, 27, 27))
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QRectF(x, 2, 27, 27))
        if self.hasFocus():
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(ACCENT), 2))
            p.drawRoundedRect(QRectF(1, 1, 49, 29), 14.5, 14.5)


# ------------------------------------------------------------------- сегменты

class Segmented(QFrame):
    """Сегменты iOS: серая дорожка, выбранный — белая «таблетка»."""

    changed = Signal(object)

    def __init__(self, options, value=None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("seg")
        self.setStyleSheet(
            "#seg { background: #EEEEF0; border-radius: 9px; }"
            "#seg QPushButton { border: 0; border-radius: 7px; padding: 0 12px; height: 28px;"
            f" color: #3A3A3F; background: transparent; font-family: '{FONT}'; font-size: 10pt; }}"
            "#seg QPushButton:checked { background: #FFFFFF; color: #1C1C1E; font-weight: 600;"
            " border: 1px solid rgba(0,0,0,0.06); }"
            "#seg QPushButton:hover:!checked { color: #1C1C1E; }"
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(2, 2, 2, 2)
        row.setSpacing(2)
        self._group = QButtonGroup(self)
        self._buttons = {}
        bold = QFontMetrics(font(10, QFont.DemiBold))
        for key, text in options:
            button = QPushButton(text)
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setFixedHeight(28)
            # выбранный сегмент жирный: ширину берём по жирному начертанию, иначе текст обрежется
            button.setMinimumWidth(int(bold.horizontalAdvance(text) * 1.1) + 30)
            self._group.addButton(button)
            self._buttons[key] = button
            row.addWidget(button)
            button.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
        self.set_value(value if value in self._buttons else next(iter(self._buttons)))

    def value(self):
        for key, button in self._buttons.items():
            if button.isChecked():
                return key
        return None

    def set_value(self, key) -> None:
        if key in self._buttons:
            self._buttons[key].setChecked(True)


# ------------------------------------------------------------- группы и строки

class Row(QWidget):
    """Строка группы: заголовок, подзаголовок и элемент справа."""

    def __init__(self, title: str, trailing: QWidget | None = None, subtitle: str = "",
                 below: QWidget | None = None, parent=None) -> None:
        super().__init__(parent)
        self.title_text = title
        self.top_line = False
        self.setAttribute(Qt.WA_StyledBackground, False)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 0, 0, 0)
        outer.setSpacing(0)
        line = QHBoxLayout()
        line.setContentsMargins(0, 9, 16, 9)
        line.setSpacing(12)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        self.title = label(title, 11)
        self.title.setWordWrap(True)
        texts.addWidget(self.title)
        self.subtitle = label(subtitle, 9, MUTED, wrap=True)
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
        self.setMinimumHeight(48)

    def paintEvent(self, _event) -> None:
        if self.top_line:
            p = QPainter(self)
            p.setPen(QPen(QColor(LINE), 1))
            p.drawLine(16, 0, self.width(), 0)


class Group(QWidget):
    """Подпись сверху, белая карточка со строками и пояснение снизу."""

    def __init__(self, title: str = "", footer: str = "", parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        self.title = label(title.upper(), 8.8, MUTED, QFont.Medium)
        self.title.setContentsMargins(16, 0, 0, 0)
        self.title.setVisible(bool(title))
        layout.addWidget(self.title)
        self.card = QFrame()
        self.card.setObjectName("card")
        self.card.setStyleSheet(f"#card {{ background: {CARD}; border-radius: 12px; }}")
        self.rows_layout = QVBoxLayout(self.card)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(0)
        layout.addWidget(self.card)
        self.footer = label(footer, 9, MUTED, wrap=True)
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

FLAT_INPUT = (
    "QLineEdit, QSpinBox, QDoubleSpinBox {{ border: 0; background: transparent; color: {muted};"
    " font-family: '{font}'; font-size: 10.5pt; padding: 4px 0; selection-background-color: #CFE3FF; }}"
    "QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ color: {ink}; }}"
    "QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button,"
    " QDoubleSpinBox::down-button {{ width: 0; border: 0; }}"
).format(muted=MUTED, ink=INK, font=FONT)

COMBO = (
    "QComboBox {{ border: 0; background: transparent; color: {muted}; padding: 4px 4px 4px 8px;"
    " font-family: '{font}'; font-size: 10.5pt; }}"
    "QComboBox:hover {{ color: {ink}; }}"
    "QComboBox::drop-down {{ border: 0; width: 18px; }}"
    "QComboBox::down-arrow {{ image: none; width: 0; }}"
    "QComboBox QAbstractItemView {{ border: 1px solid #DADAE0; border-radius: 8px; background: #FFFFFF;"
    " selection-background-color: #E8F1FF; selection-color: {ink}; padding: 4px; outline: 0; }}"
).format(muted=MUTED, ink=INK, font=FONT)


class ValueCombo(QComboBox):
    """Выбор значения, как строка iOS: серое значение справа и шеврон."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(COMBO)
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
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        p.setFont(font(10.5))
        p.setPen(QColor(INK if self._hover else MUTED))
        text = p.fontMetrics().elidedText(self.currentText(), Qt.ElideRight, int(r.width() - 24))
        p.drawText(r.adjusted(0, 0, -20, 0), Qt.AlignVCenter | Qt.AlignRight, text)
        pen = QPen(QColor("#B0B0B6"), 2)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        x, y = r.right() - 9, r.center().y()
        p.drawPolyline([QPointF(x - 3, y - 5), QPointF(x + 2, y), QPointF(x - 3, y + 5)])


def flat(widget: QWidget, width: int = 220, align_right: bool = True) -> QWidget:
    if not widget.inherits("QComboBox"):
        widget.setStyleSheet(FLAT_INPUT)
    widget.setMinimumWidth(width)
    widget.setMaximumWidth(width)
    if hasattr(widget, "setAlignment") and align_right:
        widget.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    return widget


def pill_button(text: str, primary: bool = False) -> QPushButton:
    button = QPushButton(text)
    button.setCursor(Qt.PointingHandCursor)
    button.setFixedHeight(30)
    bg, fg, hover = (ACCENT, "#FFFFFF", "#0074E8") if primary else ("#F2F2F7", ACCENT_TEXT, "#E8E8ED")
    button.setStyleSheet(
        f"QPushButton {{ border: 0; border-radius: 15px; padding: 0 14px; background: {bg}; color: {fg};"
        f" font-family: '{FONT}'; font-size: 10pt; font-weight: 600; }}"
        f"QPushButton:hover {{ background: {hover}; }}"
        "QPushButton:disabled { color: #A0A0A6; }"
    )
    return button


BADGES = {"ok": ("#E3F7E8", "#1B7A35"), "warn": ("#FFF1DC", "#9A5B00"),
          "bad": ("#FDE7E7", "#B42318"), "info": ("#EAF2FF", ACCENT_TEXT), "": ("#F2F2F7", MUTED)}


def badge(text: str, kind: str = "ok") -> QLabel:
    lab = QLabel(text)
    set_badge(lab, text, kind)
    lab.setFont(font(9, QFont.DemiBold))
    lab.setFixedHeight(24)
    return lab


def set_badge(lab: QLabel, text: str, kind: str) -> None:
    bg, fg = BADGES.get(kind, BADGES[""])
    lab.setText(text)
    lab.setVisible(bool(text))
    lab.setStyleSheet(f"QLabel {{ background: {bg}; color: {fg}; border-radius: 12px; padding: 0 10px; }}")


# ------------------------------------------------------------- «пилюли» словаря

class FlowLayout(QLayout):
    """Элементы в строку с переносом — как теги."""

    def __init__(self, parent=None, spacing: int = 7) -> None:
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
        self.text = text
        bg, fg = ("#F1ECFF", "#5B3FC4") if learned else ("#F2F2F7", INK)
        self.setObjectName("chip")
        self.setStyleSheet(f"#chip {{ background: {bg}; border-radius: 15px; }}")
        self.setFixedHeight(30)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 0, 6, 0)
        row.setSpacing(4)
        row.addWidget(label(text, 10, fg))
        close = QToolButton()
        close.setText("×")
        close.setCursor(Qt.PointingHandCursor)
        close.setStyleSheet(f"QToolButton {{ border: 0; color: {MUTED}; font-size: 13pt; padding: 0 2px; }}"
                            f"QToolButton:hover {{ color: {INK}; }}")
        close.clicked.connect(lambda: self.removed.emit(self.text))
        row.addWidget(close)


class ChipsEditor(QWidget):
    """Словарь «пилюлями»: × удаляет, «+ Добавить» превращается в поле ввода."""

    changed = Signal()

    def __init__(self, terms: list[str], learned: set[str], add_text: str, placeholder: str,
                 parent=None) -> None:
        super().__init__(parent)
        self._learned = {t.lower() for t in learned}
        self._terms: list[str] = []
        self._flow = FlowLayout(self)
        self._add = QPushButton("+  " + add_text)
        self._add.setCursor(Qt.PointingHandCursor)
        self._add.setFixedHeight(30)
        self._add.setStyleSheet(
            f"QPushButton {{ border: 1px dashed #A8A8AE; border-radius: 15px; padding: 0 12px;"
            f" background: transparent; color: {ACCENT_TEXT}; font-family: '{FONT}'; font-size: 10pt; }}"
            "QPushButton:hover { border-color: #0A84FF; }")
        self._add.clicked.connect(self._start_add)
        self._field = QLineEdit()
        self._field.setPlaceholderText(placeholder)
        self._field.setFixedHeight(30)
        self._field.setMinimumWidth(180)
        self._field.setStyleSheet(
            f"QLineEdit {{ border: 1.5px solid {ACCENT}; border-radius: 15px; padding: 0 12px;"
            f" background: #FFFFFF; font-family: '{FONT}'; font-size: 10pt; }}")
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

class NavButton(QAbstractButton):
    """Пункт сайдбара: цветной квадрат со значком и название; выбранный — синий."""

    def __init__(self, text: str, color: str, icon_path: str, parent=None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setText(text)
        self.color = QColor(color)
        self.icon_path = icon_path
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(44)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._hover = False

    def enterEvent(self, _event) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, _event) -> None:
        self._hover = False
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        on = self.isChecked()
        if on or self._hover:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(ACCENT) if on else QColor(0, 0, 0, 14))
            p.drawRoundedRect(r, 10, 10)
        box = QRectF(10, (r.height() - 29) / 2, 29, 29)
        p.setPen(Qt.NoPen)
        p.setBrush(self.color)
        p.drawRoundedRect(box, 7, 7)
        if on:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255, 150), 1.5))
            p.drawRoundedRect(box.adjusted(0.75, 0.75, -0.75, -0.75), 6.5, 6.5)
        draw_icon(p, self.icon_path, box.adjusted(4.5, 4.5, -4.5, -4.5))
        p.setPen(QColor("#FFFFFF") if on else QColor(INK))
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
