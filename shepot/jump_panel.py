"""Окно «Прыжок в проект» в стиле острова: слева список, справа карточка проекта.

Карточка: ветка, незакоммиченные файлы, неотправленные коммиты, последний
коммит, открыт ли уже проект и работает ли сервер; ниже — что откроется
(галочки и схема экрана) и кнопка.

Стрелки — выбор, Enter — открыть (или переключиться на уже открытое),
Shift+Enter — только терминал, Alt+1…5 — галочки, Ctrl+P — закрепить,
Ctrl+E — папка в Проводнике, Esc или щелчок мимо — закрыть.
"""

from __future__ import annotations

import os
import threading

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetricsF, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QLineEdit, QWidget

from . import jump
from .i18n import plural, t

PAD = 16                      # поле под тень
LIST_W, CARD_W = 470, 320
PANEL_W = LIST_W + CARD_W
SEARCH_H = 58
ROW_H = 50
GROUP_H = 26
LIST_H = 404
FOOT_H = 36
RADIUS = 18.0

INK = QColor(10, 10, 11)
INK_2 = QColor(23, 23, 26)
LINE = QColor(255, 255, 255, 24)
WHITE = QColor(255, 255, 255)
DIM = QColor(255, 255, 255, 125)
ORANGE = QColor(255, 159, 10)
GREEN = QColor(48, 209, 88)
BLUE = QColor(100, 168, 255)
TILE_COLORS = [QColor(c) for c in ("#E8590C", "#1C7ED6", "#2F9E44", "#7048E8", "#C2255C",
                                   "#0C8599", "#E67700", "#5C7CFA")]
PART_COLORS = {"claude": QColor("#3A2A20"), "codex": QColor("#20302A"), "code": QColor("#1F5FBF"),
               "dev": QColor("#33363D"), "web": QColor("#8A3A12")}

STYLE = "QLineEdit { color: #ffffff; background: transparent; border: none; selection-background-color: #8a4b12; }"


def _font(size: float, weight=QFont.Normal, family: str = "Segoe UI") -> QFont:
    font = QFont(family)
    font.setPointSizeF(size)
    font.setWeight(weight)
    QFontMetricsF(font).horizontalAdvance("Проект shepot 12")    # подбор шрифта заранее
    return font


def _plural(n: int, one: str, few: str, many: str, en_one: str, en_many: str) -> str:
    return f"{n} {plural(n, (one, few, many), (en_one, en_many))}"


class JumpPanel(QWidget):
    chosen = Signal(object, dict, bool)      # проект, что открыть, только терминал
    optionsChanged = Signal(str, dict)       # путь проекта, галочки
    pinToggled = Signal(str)
    statusReady = Signal(str, object)        # из фонового потока

    def __init__(self) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet(STYLE)
        self.setMouseTracking(True)
        self.resize(PANEL_W + PAD * 2, SEARCH_H + LIST_H + FOOT_H + PAD * 2)

        self.f_body = _font(10.5)
        self.f_bold = _font(10.5, QFont.DemiBold)
        self.f_title = _font(12, QFont.DemiBold)
        self.f_small = _font(8.5)
        self.f_label = _font(7.5, QFont.DemiBold)
        self.f_mono = _font(8.5, family="Consolas")

        self.field = QLineEdit(self)
        self.field.setFont(_font(13))
        self.field.setPlaceholderText(t("Проект…"))
        self.field.setGeometry(PAD + 46, PAD + 11, LIST_W - 150, SEARCH_H - 22)
        self.field.textChanged.connect(self._filter)
        self.field.installEventFilter(self)

        self.root_label = ""
        self._projects: list[jump.Project] = []
        self._shown: list[jump.Project] = []
        self._rows: list[tuple[str, object]] = []      # ("group", текст) | ("row", индекс в _shown)
        self._colors: dict[str, QColor] = {}
        self._status: dict[str, jump.Status] = {}
        self._options: dict[str, dict] = {}
        self._pinned: set[str] = set()
        self._sel = 0
        self._scroll = 0
        self._hits: list[tuple[QRectF, str, object]] = []   # области щелчка
        self._was_active = False
        self._generation = 0
        self.statusReady.connect(self._on_status)

    # ------------------------------------------------------------------ снаружи
    def open(self, projects: list[jump.Project], saved_options: dict, pinned: list, root_label: str) -> None:
        self._projects = projects
        self._pinned = set(pinned)
        self.root_label = root_label
        self._options = {p.key: p.options(saved_options.get(p.key)) for p in projects}
        for i, p in enumerate(sorted(projects, key=lambda p: p.path.name.lower())):
            self._colors[p.key] = TILE_COLORS[i % len(TILE_COLORS)]
        self.field.blockSignals(True)
        self.field.clear()
        self.field.blockSignals(False)
        self._filter("")
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        self.move(area.center().x() - self.width() // 2, area.top() + int(area.height() * 0.14))
        self._was_active = False
        self.show()
        self.raise_()
        self.activateWindow()
        self.field.setFocus()
        self._load_status(projects)

    def close_panel(self) -> None:
        self.hide()

    def _load_status(self, projects: list[jump.Project]) -> None:
        """Окна и порты — сразу, git — в фоне, по проекту за раз, начиная сверху."""
        self._generation += 1
        generation = self._generation
        windows = jump.top_windows()
        for p in projects:
            status = self._status.get(p.key) or jump.Status()
            status.editor, status.terminal = jump.find_open(p, windows)
            status.running = bool(p.url) and jump.port_open(p.url)
            self._status[p.key] = status
        git = jump.tools().get("git")

        def work() -> None:
            for p in projects:
                if generation != self._generation:
                    return
                self.statusReady.emit(p.key, jump.git_status(p.path, git))

        threading.Thread(target=work, name="jump-git", daemon=True).start()

    def _on_status(self, key: str, git: jump.Status) -> None:
        status = self._status.get(key) or jump.Status()
        for name in ("dirty", "ahead", "behind", "upstream", "last", "last_ts"):
            setattr(status, name, getattr(git, name))
        self._status[key] = status
        if self.isVisible():
            self.update()

    # ------------------------------------------------------------------ список
    def _filter(self, text: str) -> None:
        found = jump.search(self._projects, text)
        if text.strip():
            self._shown = found
            self._rows = [("row", i) for i in range(len(found))]
        else:
            pinned = [p for p in found if p.key in self._pinned]
            rest = [p for p in found if p.key not in self._pinned]
            self._shown = pinned + rest
            self._rows = []
            if pinned:
                self._rows.append(("group", t("Закреплённые")))
                self._rows += [("row", i) for i in range(len(pinned))]
            self._rows.append(("group", t("Недавние") if pinned else t("Проекты")))
            self._rows += [("row", len(pinned) + i) for i in range(len(rest))]
        self._sel, self._scroll = 0, 0
        self.update()

    def _row_y(self, row_index: int) -> float:
        y = 0.0
        for kind, _ in self._rows[:row_index]:
            y += GROUP_H if kind == "group" else ROW_H
        return y

    def _move(self, step: int) -> None:
        if not self._shown:
            return
        self._sel = max(0, min(len(self._shown) - 1, self._sel + step))
        position = next(i for i, (k, v) in enumerate(self._rows) if k == "row" and v == self._sel)
        top = self._row_y(position)
        if position and self._rows[position - 1][0] == "group":
            top -= GROUP_H
        bottom = self._row_y(position) + ROW_H
        if top < self._scroll:
            self._scroll = top
        elif bottom > self._scroll + LIST_H - 12:
            self._scroll = bottom - LIST_H + 12
        self.update()

    def current(self) -> jump.Project | None:
        return self._shown[self._sel] if self._shown else None

    def _choose(self, terminal_only: bool) -> None:
        project = self.current()
        if project is None:
            return
        self.hide()
        self.chosen.emit(project, dict(self._options[project.key]), terminal_only)

    def _toggle(self, part: str) -> None:
        project = self.current()
        if project is None or part not in project.available:
            return
        opts = self._options[project.key]
        opts[part] = not opts[part]
        self.optionsChanged.emit(project.key, dict(opts))
        self.update()

    def _pin(self) -> None:
        project = self.current()
        if project is None:
            return
        if project.key in self._pinned:
            self._pinned.discard(project.key)
        else:
            self._pinned.add(project.key)
        self.pinToggled.emit(project.key)
        key = project.key
        self._filter(self.field.text())
        self._sel = next((i for i, p in enumerate(self._shown) if p.key == key), 0)
        self._move(0)

    def _explore(self) -> None:
        project = self.current()
        if project is not None:
            self.hide()
            os.startfile(str(project.path))

    # ------------------------------------------------------------------ события
    def eventFilter(self, obj, event) -> bool:
        if obj is self.field and event.type() == QEvent.KeyPress:
            key, mods = event.key(), event.modifiers()
            if mods & Qt.AltModifier and Qt.Key_1 <= key <= Qt.Key_5:
                self._toggle(jump.PARTS[key - Qt.Key_1])
                return True
            if mods & Qt.ControlModifier and key == Qt.Key_P:
                self._pin()
                return True
            if mods & Qt.ControlModifier and key == Qt.Key_E:
                self._explore()
                return True
            moves = {Qt.Key_Down: 1, Qt.Key_Up: -1, Qt.Key_PageDown: 7, Qt.Key_PageUp: -7}
            if key in moves:
                self._move(moves[key])
                return True
            if key in (Qt.Key_Return, Qt.Key_Enter):
                self._choose(bool(mods & Qt.ShiftModifier))
                return True
            if key == Qt.Key_Escape:
                self.close_panel()
                return True
        return super().eventFilter(obj, event)

    def _hit(self, pos: QPointF):
        for rect, kind, value in self._hits:
            if rect.contains(pos):
                return kind, value
        return None, None

    def mouseMoveEvent(self, event) -> None:
        kind, value = self._hit(event.position())
        if kind == "row" and value != self._sel:
            self._sel = value
            self.update()
        self.setCursor(Qt.PointingHandCursor if kind else Qt.ArrowCursor)

    def mousePressEvent(self, event) -> None:
        kind, value = self._hit(event.position())
        shift = bool(event.modifiers() & Qt.ShiftModifier)
        if kind == "row":
            self._sel = value
            self._choose(shift)
        elif kind == "part":
            self._toggle(value)
            self.field.setFocus()
        elif kind == "go":
            self._choose(shift)

    def wheelEvent(self, event) -> None:
        if event.position().x() < PAD + LIST_W:
            total = self._row_y(len(self._rows))
            self._scroll = max(0.0, min(max(0.0, total - LIST_H + 12), self._scroll - event.angleDelta().y() / 2))
            self.update()

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.ActivationChange:
            if self.isActiveWindow():
                self._was_active = True
            elif self._was_active and self.isVisible():
                self.close_panel()          # щёлкнули мимо
        super().changeEvent(event)

    # ------------------------------------------------------------------ рисование
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        self._hits = []
        rect = QRectF(PAD, PAD, PANEL_W, self.height() - PAD * 2)
        p.setPen(Qt.NoPen)
        for step in range(8, 0, -1):
            shadow = QPainterPath()
            shadow.addRoundedRect(rect.adjusted(-step, -step + 4, step, step + 4), RADIUS + step, RADIUS + step)
            p.fillPath(shadow, QColor(0, 0, 0, 10))
        shape = QPainterPath()
        shape.addRoundedRect(rect, RADIUS, RADIUS)
        p.fillPath(shape, INK)
        p.setPen(QPen(LINE, 1))
        p.drawPath(shape)

        # строка поиска: лампа, поле, папка справа
        center = QPointF(PAD + 24, PAD + SEARCH_H / 2)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 159, 10, 55))
        p.drawEllipse(center, 9, 9)
        p.setBrush(ORANGE)
        p.drawEllipse(center, 4, 4)
        p.setFont(self.f_mono)
        p.setPen(DIM)
        p.drawText(QRectF(PAD, PAD, PANEL_W - 20, SEARCH_H), Qt.AlignVCenter | Qt.AlignRight,
                   f"{self.root_label} · {len(self._projects)}")
        p.setPen(QPen(LINE, 1))
        top = PAD + SEARCH_H
        p.drawLine(QPointF(rect.left(), top), QPointF(rect.right(), top))
        split = PAD + LIST_W
        bottom = rect.bottom() - FOOT_H
        p.drawLine(QPointF(split, top), QPointF(split, bottom))
        p.drawLine(QPointF(rect.left(), bottom), QPointF(rect.right(), bottom))

        self._paint_list(p, QRectF(PAD, top, LIST_W, bottom - top))
        self._paint_card(p, QRectF(split, top, CARD_W, bottom - top))
        self._paint_foot(p, QRectF(PAD + 18, bottom, PANEL_W - 36, FOOT_H))

    def _paint_list(self, p: QPainter, area: QRectF) -> None:
        p.save()
        p.setClipRect(area)
        if not self._shown:
            p.setFont(self.f_body)
            p.setPen(DIM)
            p.drawText(area.adjusted(0, 20, 0, 0), Qt.AlignTop | Qt.AlignHCenter, t("Нет такого проекта"))
        query = self.field.text()
        y = area.top() + 6 - self._scroll
        for kind, value in self._rows:
            h = GROUP_H if kind == "group" else ROW_H
            if y + h < area.top() or y > area.bottom():
                y += h
                continue
            if kind == "group":
                p.setFont(self.f_label)
                p.setPen(DIM)
                p.drawText(QRectF(area.left() + 20, y + 6, 300, GROUP_H - 6), Qt.AlignVCenter | Qt.AlignLeft,
                           value.upper())
            else:
                row = QRectF(area.left() + 8, y, area.width() - 16, ROW_H - 4)
                if value == self._sel:
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor(255, 159, 10, 38))
                    p.drawRoundedRect(row, 10, 10)
                self._paint_row(p, row, self._shown[value], query)
                self._hits.append((row.intersected(area), "row", value))
            y += h
        p.restore()

    def _paint_row(self, p: QPainter, row: QRectF, project: jump.Project, query: str) -> None:
        tile = QRectF(row.left() + 10, row.center().y() - 15, 30, 30)
        p.setPen(Qt.NoPen)
        p.setBrush(self._colors.get(project.key, TILE_COLORS[0]))
        p.drawRoundedRect(tile, 8, 8)
        p.setFont(self.f_bold)
        p.setPen(WHITE)
        p.drawText(tile, Qt.AlignCenter, project.name[:1].upper())

        # справа — состояние: открыт, сервер, изменения
        status = self._status.get(project.key) or jump.Status()
        pills = []
        if status.is_open:
            pills.append((t("открыт"), GREEN))
        if status.running and project.url:
            port = project.url.rsplit(":", 1)[-1].strip("/")
            pills.append((":" + port, BLUE))
        if status.dirty:
            pills.append((f"●{status.dirty}", ORANGE))
        p.setFont(self.f_small)
        m = QFontMetricsF(self.f_small)
        x = row.right() - 10
        for text, color in reversed(pills):
            w = m.horizontalAdvance(text) + 16
            pill = QRectF(x - w, row.center().y() - 10, w, 20)
            p.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 110), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(pill, 10, 10)
            p.setPen(color)
            p.drawText(pill, Qt.AlignCenter, text)
            x -= w + 6

        left = tile.right() + 12
        width = x - 8 - left
        name_rect = QRectF(left, row.top() + 6, width, 20)
        metrics = QFontMetricsF(self.f_bold)
        name = metrics.elidedText(project.name, Qt.ElideRight, width)
        span = jump.match_span(name, query) if query.strip() else None
        p.setFont(self.f_bold)
        pieces = [(name, WHITE)]
        if span:
            a, b = span
            pieces = [(name[:a], WHITE), (name[a:b], ORANGE), (name[b:], WHITE)]
        cx = name_rect.left()
        for text, color in pieces:
            p.setPen(color)
            p.drawText(QRectF(cx, name_rect.top(), name_rect.right() - cx, 20), Qt.AlignVCenter | Qt.AlignLeft, text)
            cx += metrics.horizontalAdvance(text)
        meta = " · ".join(x for x in (project.branch, jump.ago(status.last_ts or project.touched)) if x)
        p.setFont(self.f_mono)
        p.setPen(DIM)
        p.drawText(QRectF(left, row.top() + 26, width, 16), Qt.AlignVCenter | Qt.AlignLeft,
                   QFontMetricsF(self.f_mono).elidedText(meta, Qt.ElideRight, width))

    def _paint_card(self, p: QPainter, area: QRectF) -> None:
        project = self.current()
        if project is None:
            return
        status = self._status.get(project.key) or jump.Status()
        opts = self._options[project.key]
        x, w = area.left() + 18, area.width() - 36
        y = area.top() + 16

        tile = QRectF(x, y, 38, 38)
        p.setPen(Qt.NoPen)
        p.setBrush(self._colors.get(project.key, TILE_COLORS[0]))
        p.drawRoundedRect(tile, 9, 9)
        p.setFont(self.f_title)
        p.setPen(WHITE)
        p.drawText(tile, Qt.AlignCenter, project.name[:1].upper())
        p.drawText(QRectF(x + 50, y, w - 50, 22), Qt.AlignVCenter | Qt.AlignLeft,
                   QFontMetricsF(self.f_title).elidedText(project.name, Qt.ElideRight, w - 50))
        p.setFont(self.f_mono)
        p.setPen(DIM)
        where = project.path.parent.name + "\\" + project.path.name
        p.drawText(QRectF(x + 50, y + 21, w - 50, 16), Qt.AlignVCenter | Qt.AlignLeft,
                   QFontMetricsF(self.f_mono).elidedText(where, Qt.ElideMiddle, w - 50))
        y += 54

        lines: list[tuple[str, str, QColor]] = []
        if project.branch:
            lines.append((t("Ветка"), project.branch, WHITE))
            lines.append((t("Изменения"), _plural(status.dirty, "файл не закоммичен", "файла не закоммичены",
                                                "файлов не закоммичено", "uncommitted file",
                                                "uncommitted files") if status.dirty else t("чисто"),
                          ORANGE if status.dirty else WHITE))
            if not status.upstream:
                lines.append(("GitHub", t("ветка не отправлена"), ORANGE))
            elif status.ahead:
                lines.append(("GitHub", _plural(status.ahead, "коммит не отправлен", "коммита не отправлены",
                                                 "коммитов не отправлено", "unpushed commit",
                                                 "unpushed commits"), ORANGE))
            else:
                lines.append(("GitHub", t("всё отправлено"), WHITE))
            if status.last:
                lines.append((t("Последний"), f"{status.last} · {jump.ago(status.last_ts)}", WHITE))
        else:
            lines.append(("Git", t("не репозиторий"), DIM))
        if status.is_open:
            what = ", ".join(n for n, h in (("VS Code", status.editor), (t("терминал"), status.terminal)) if h)
            lines.append((t("Сейчас"), t("уже открыт: {what}", what=what), GREEN))
        if status.running:
            lines.append((t("Сервер"), t("работает {url}", url=project.url.replace("http://", "")), BLUE))
        for label, value, color in lines:
            p.setFont(self.f_small)
            p.setPen(DIM)
            p.drawText(QRectF(x, y, 78, 20), Qt.AlignVCenter | Qt.AlignLeft, label)
            p.setPen(color)
            p.drawText(QRectF(x + 80, y, w - 80, 20), Qt.AlignVCenter | Qt.AlignLeft,
                       QFontMetricsF(self.f_small).elidedText(value, Qt.ElideRight, w - 80))
            y += 21
        y += 10

        p.setFont(self.f_label)
        p.setPen(DIM)
        p.drawText(QRectF(x, y, w, 16), Qt.AlignVCenter | Qt.AlignLeft, t("ЧТО ОТКРОЕТСЯ"))
        y += 22
        self._paint_layout(p, QRectF(x, y, w, 76), project, opts, status)
        y += 88

        # галочки
        p.setFont(self.f_small)
        m = QFontMetricsF(self.f_small)
        cx, cy = x, y
        for index, part in enumerate(jump.PARTS):
            if part not in project.available:
                continue
            label = t(jump.PART_LABELS[part])
            key = str(index + 1)
            chip_w = m.horizontalAdvance(label) + m.horizontalAdvance(key) + 38
            if cx + chip_w > x + w:
                cx, cy = x, cy + 30
            chip = QRectF(cx, cy, chip_w, 24)
            on = opts.get(part)
            p.setPen(QPen(QColor(255, 159, 10, 150) if on else LINE, 1))
            p.setBrush(QColor(255, 159, 10, 30) if on else Qt.NoBrush)
            p.drawRoundedRect(chip, 7, 7)
            box = QRectF(chip.left() + 8, chip.center().y() - 5, 10, 10)
            p.setPen(QPen(ORANGE if on else DIM, 1.4))
            p.setBrush(ORANGE if on else Qt.NoBrush)
            p.drawRoundedRect(box, 3, 3)
            p.setPen(WHITE)
            p.drawText(QRectF(box.right() + 6, chip.top(), 200, chip.height()), Qt.AlignVCenter | Qt.AlignLeft, label)
            p.setPen(QColor(255, 255, 255, 80))
            p.drawText(chip.adjusted(0, 0, -8, 0), Qt.AlignVCenter | Qt.AlignRight, key)
            self._hits.append((chip, "part", part))
            cx += chip_w + 6

        go = QRectF(x, area.bottom() - 52, w, 36)
        p.setPen(Qt.NoPen)
        p.setBrush(ORANGE)
        p.drawRoundedRect(go, 10, 10)
        p.setFont(self.f_bold)
        p.setPen(QColor(26, 16, 0))
        label = t("Переключиться и расставить") if status.is_open else t("Открыть")
        p.drawText(go.adjusted(14, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, label)
        p.setFont(self.f_mono)
        p.drawText(go.adjusted(0, 0, -14, 0), Qt.AlignVCenter | Qt.AlignRight, "Enter")
        self._hits.append((go, "go", None))

    def _paint_layout(self, p: QPainter, box: QRectF, project: jump.Project, opts: dict,
                      status: jump.Status) -> None:
        """Схема экрана: редактор слева, терминал с вкладками справа."""
        p.setPen(QPen(LINE, 1))
        p.setBrush(INK_2)
        p.drawRoundedRect(box, 8, 8)
        tabs = [t(jump.PART_LABELS[k]) for k in ("claude", "codex", "dev") if opts.get(k)]
        inner = box.adjusted(5, 5, -5, -5)
        blocks = []
        if opts.get("code") and tabs:
            split = inner.left() + inner.width() * jump.EDITOR_RATIO
            blocks = [(QRectF(inner.left(), inner.top(), split - inner.left() - 3, inner.height()), "code", "VS Code"),
                      (QRectF(split + 3, inner.top(), inner.right() - split - 3, inner.height()), "claude",
                       " · ".join(tabs))]
        elif opts.get("code"):
            blocks = [(inner, "code", "VS Code")]
        elif tabs:
            blocks = [(inner, "claude", " · ".join(tabs))]
        p.setFont(self.f_mono)
        for rect, part, label in blocks:
            p.setPen(Qt.NoPen)
            p.setBrush(PART_COLORS["code"] if part == "code" else PART_COLORS["claude"])
            p.drawRoundedRect(rect, 5, 5)
            p.setPen(WHITE)
            p.drawText(rect.adjusted(4, 0, -4, 0), Qt.AlignCenter | Qt.TextWordWrap,
                       QFontMetricsF(self.f_mono).elidedText(label, Qt.ElideRight, rect.width() - 8))
        if not blocks:
            p.setPen(DIM)
            p.drawText(box, Qt.AlignCenter, t("ничего не отмечено"))
        if opts.get("web") and project.url:
            badge = QRectF(box.right() - 74, box.bottom() - 22, 68, 17)
            p.setPen(Qt.NoPen)
            p.setBrush(PART_COLORS["web"])
            p.drawRoundedRect(badge, 5, 5)
            p.setPen(WHITE)
            p.drawText(badge, Qt.AlignCenter, "+ " + t("браузер"))

    def _paint_foot(self, p: QPainter, area: QRectF) -> None:
        p.setFont(self.f_small)
        p.setPen(DIM)
        p.drawText(area, Qt.AlignVCenter | Qt.AlignLeft,
                   t("Enter — открыть    Shift+Enter — только терминал    Alt+1…5 — что открыть    "
                     "Ctrl+P — закрепить    Ctrl+E — папка    Esc"))
