"""Окно статистики: карточка, которую не стыдно показать.

Вся карточка рисуется одним QPainter: так её можно скопировать картинкой
(«Скопировать картинкой») и вставить в чат или пост.
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from . import stats as stats_mod

BG = QColor(247, 248, 251)
CARD = QColor(255, 255, 255)
INK = QColor(24, 28, 38)
MUTED = QColor(118, 124, 140)
LINE = QColor(232, 235, 242)
BLUE_TOP = QColor(46, 124, 255)
BLUE_BOTTOM = QColor(24, 60, 140)
SOFT_BLUE = QColor(46, 124, 255, 46)
GREEN = QColor(36, 170, 96)
ORANGE = QColor(255, 149, 64)

APP_NAMES = {
    "telegram": "Telegram", "chrome": "Chrome", "msedge": "Edge", "firefox": "Firefox",
    "winword": "Word", "outlook": "Outlook", "olk": "Outlook", "excel": "Excel",
    "slack": "Slack", "discord": "Discord", "code": "VS Code", "cursor": "Cursor",
    "notepad": "Блокнот", "windowsterminal": "Терминал", "explorer": "Проводник",
    "whatsapp": "WhatsApp", "notion": "Notion", "obsidian": "Obsidian", "claude": "Claude",
    "chatgpt": "ChatGPT", "zoom": "Zoom", "teams": "Teams", "ms-teams": "Teams",
}


def app_title(exe: str) -> str:
    stem = exe.lower().removesuffix(".exe")
    return APP_NAMES.get(stem, stem[:1].upper() + stem[1:])


def plural(n: int, forms: tuple[str, str, str]) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return forms[1]
    return forms[2]


def grouped(n: int) -> str:
    return f"{n:,}".replace(",", " ")          # 12 480 — узкий неразрывный пробел


WORDS = ("слово", "слова", "слов")
DAYS = ("день", "дня", "дней")


class StatsCard(QWidget):
    W, H = 600, 624

    def __init__(self, summary: dict, parent=None) -> None:
        super().__init__(parent)
        self.summary = summary
        self.setFixedSize(self.W, self.H)

    # --------------------------------------------------------------- шрифты
    @staticmethod
    def _font(size: float, weight: QFont.Weight = QFont.Normal) -> QFont:
        font = QFont("Segoe UI Variable Display")
        if not font.exactMatch():
            font = QFont("Segoe UI")
        font.setPointSizeF(size)
        font.setWeight(weight)
        return font

    def _text(self, p: QPainter, rect: QRectF, text: str, size: float, color: QColor,
              weight: QFont.Weight = QFont.Normal, flags=Qt.AlignLeft | Qt.AlignVCenter) -> None:
        p.setFont(self._font(size, weight))
        p.setPen(color)
        p.drawText(rect, flags, text)

    # ---------------------------------------------------------------- рисунок
    def paintEvent(self, _event) -> None:
        s = self.summary
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), BG)

        # шапка: градиент как у иконки и главная цифра
        hero = QRectF(20, 20, self.W - 40, 150)
        grad = QLinearGradient(hero.topLeft(), hero.bottomRight())
        grad.setColorAt(0, BLUE_TOP)
        grad.setColorAt(1, BLUE_BOTTOM)
        path = QPainterPath()
        path.addRoundedRect(hero, 22, 22)
        p.fillPath(path, grad)
        self._draw_waves(p, hero)
        white = QColor(255, 255, 255)
        self._text(p, QRectF(hero.left() + 28, hero.top() + 22, 400, 22),
                   "Надиктовано с Шёпотом", 10.5, QColor(255, 255, 255, 200))
        words = s["words"]
        self._text(p, QRectF(hero.left() + 26, hero.top() + 46, 520, 60),
                   f"{grouped(words)} {plural(words, WORDS)}", 34, white, QFont.DemiBold)
        week = s["words_week"]
        self._text(p, QRectF(hero.left() + 28, hero.top() + 108, 520, 22),
                   f"за неделю {grouped(week)} · диктовок {s['dictations']} · правок {s['edits']}",
                   10.5, QColor(255, 255, 255, 210))

        # три плитки
        tiles = [
            ("Сэкономлено", "≈ " + stats_mod.format_duration(s["saved_seconds"]),
             f"против набора {stats_mod.TYPING_WPM} слов/мин", GREEN),
            ("Серия", f"{s['streak']} {plural(s['streak'], DAYS)}", "подряд с диктовкой", ORANGE),
            ("Сегодня", f"{grouped(s['words_today'])} {plural(s['words_today'], WORDS)}",
             self._wait_text(s["avg_wait"]), BLUE_TOP),
        ]
        gap, top = 12, hero.bottom() + 14
        tile_w = (self.W - 40 - gap * 2) / 3
        for i, (label, value, note, accent) in enumerate(tiles):
            rect = QRectF(20 + i * (tile_w + gap), top, tile_w, 96)
            self._card(p, rect)
            p.setPen(Qt.NoPen)
            p.setBrush(accent)
            p.drawEllipse(QPointF(rect.left() + 20, rect.top() + 22), 4, 4)
            self._text(p, QRectF(rect.left() + 32, rect.top() + 12, tile_w - 40, 20), label, 9.5, MUTED)
            self._text(p, QRectF(rect.left() + 16, rect.top() + 34, tile_w - 24, 32), value, 16,
                       INK, QFont.DemiBold)
            self._text(p, QRectF(rect.left() + 16, rect.top() + 66, tile_w - 24, 20), note, 8.5, MUTED)

        # столбики за 30 дней
        chart = QRectF(20, top + 110, self.W - 40, 150)
        self._card(p, chart)
        self._text(p, QRectF(chart.left() + 18, chart.top() + 10, 300, 22), "Слов в день, 30 дней",
                   10, INK, QFont.DemiBold)
        self._draw_bars(p, QRectF(chart.left() + 18, chart.top() + 40, chart.width() - 36, 92))

        # приложения
        apps = QRectF(20, chart.bottom() + 12, self.W - 40, self.H - chart.bottom() - 32)
        self._card(p, apps)
        self._text(p, QRectF(apps.left() + 18, apps.top() + 10, 300, 22), "Куда диктуете",
                   10, INK, QFont.DemiBold)
        self._draw_apps(p, QRectF(apps.left() + 18, apps.top() + 38, apps.width() - 36,
                                  apps.height() - 48))
        p.end()

    @staticmethod
    def _wait_text(avg_wait: float | None) -> str:
        if avg_wait is None:
            return "ждать текст: пока не знаем"
        return f"ждать текст: {avg_wait:.1f} с".replace(".", ",")

    @staticmethod
    def _card(p: QPainter, rect: QRectF) -> None:
        p.setPen(QPen(LINE, 1))
        p.setBrush(CARD)
        p.drawRoundedRect(rect, 16, 16)

    @staticmethod
    def _draw_waves(p: QPainter, hero: QRectF) -> None:
        """Звуковая волна справа в шапке — микрофон Шёпота в виде полос."""
        heights = (18, 34, 56, 80, 52, 92, 64, 38, 70, 44, 26, 14)
        x = hero.right() - 28 - len(heights) * 12
        mid = hero.center().y()
        p.setPen(Qt.NoPen)
        for i, h in enumerate(heights):
            p.setBrush(QColor(255, 255, 255, 40 + i * 6))
            p.drawRoundedRect(QRectF(x + i * 12, mid - h / 2, 6, h), 3, 3)

    def _draw_bars(self, p: QPainter, area: QRectF) -> None:
        series = self.summary["series"]
        peak = max((w for _d, w in series), default=0)
        n = len(series)
        step = area.width() / max(1, n)
        bar_w = max(3.0, step * 0.62)
        p.setPen(QPen(LINE, 1, Qt.DashLine))
        p.drawLine(QPointF(area.left(), area.bottom()), QPointF(area.right(), area.bottom()))
        for i, (day, words) in enumerate(series):
            x = area.left() + i * step + (step - bar_w) / 2
            h = 0 if peak == 0 else max(3.0, area.height() * words / peak) if words else 0
            rect = QRectF(x, area.bottom() - h, bar_w, h)
            if words:
                grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
                last = i == n - 1
                grad.setColorAt(0, ORANGE if last else BLUE_TOP)
                grad.setColorAt(1, ORANGE.darker(115) if last else SOFT_BLUE)
                p.setPen(Qt.NoPen)
                p.setBrush(grad)
                p.drawRoundedRect(rect, bar_w / 2, bar_w / 2)
            else:
                p.setPen(Qt.NoPen)
                p.setBrush(LINE)
                p.drawEllipse(QPointF(x + bar_w / 2, area.bottom() - 2), 1.6, 1.6)
        if peak:
            self._text(p, QRectF(area.right() - 160, area.top() - 30, 160, 20),
                       f"максимум {grouped(peak)}", 8.5, MUTED, flags=Qt.AlignRight | Qt.AlignVCenter)
        first = series[0][0] if series else date.today()
        self._text(p, QRectF(area.left(), area.bottom() + 2, 120, 16), first.strftime("%d.%m"), 8, MUTED)
        self._text(p, QRectF(area.right() - 120, area.bottom() + 2, 120, 16), "сегодня", 8, MUTED,
                   flags=Qt.AlignRight | Qt.AlignVCenter)

    def _draw_apps(self, p: QPainter, area: QRectF) -> None:
        apps = self.summary["apps"][:4]
        if not apps:
            self._text(p, area, "Появится после первых диктовок", 9.5, MUTED,
                       flags=Qt.AlignLeft | Qt.AlignTop)
            return
        peak = apps[0][1] or 1
        row = min(26.0, area.height() / len(apps))
        for i, (exe, words) in enumerate(apps):
            y = area.top() + i * row
            self._text(p, QRectF(area.left(), y, 110, row), app_title(exe), 9.5, INK)
            track = QRectF(area.left() + 118, y + row / 2 - 4, area.width() - 118 - 90, 8)
            p.setPen(Qt.NoPen)
            p.setBrush(LINE)
            p.drawRoundedRect(track, 4, 4)
            p.setBrush(BLUE_TOP)
            p.drawRoundedRect(QRectF(track.left(), track.top(), max(8.0, track.width() * words / peak),
                                     track.height()), 4, 4)
            self._text(p, QRectF(track.right() + 8, y, 82, row), grouped(words), 9, MUTED,
                       flags=Qt.AlignRight | Qt.AlignVCenter)


class StatsDialog(QDialog):
    def __init__(self, stats, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Шёпот — статистика")
        self.setStyleSheet("QDialog { background: #F7F8FB; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 14)
        self.card = StatsCard(stats.summary())
        layout.addWidget(self.card)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(20, 0, 20, 0)
        self.note = QLabel("")
        self.note.setStyleSheet("color: #767C8C;")
        copy_button = QPushButton("Скопировать картинкой")
        copy_button.clicked.connect(self._copy_image)
        close_button = QPushButton("Закрыть")
        close_button.clicked.connect(self.accept)
        buttons.addWidget(self.note)
        buttons.addStretch(1)
        buttons.addWidget(copy_button)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

    def _copy_image(self) -> None:
        QGuiApplication.clipboard().setPixmap(self.card.grab())
        self.note.setText("Картинка в буфере, вставьте Ctrl+V")
