"""Где стоит остров и как его перетаскивают.

Остров берут левой кнопкой и тащат; отпустили — он доезжает на пружине:
* к ближайшему «магниту», если отпустили рядом или бросили в его сторону
  (бросок продолжает движение, как картинка-в-картинке на iPhone);
* иначе остаётся, где отпустили, внутри экрана; у вертикальной середины
  экрана примагничивается к центру.

Магниты: низ по центру (место по умолчанию), верх по центру и четыре угла.
Положение помнится для каждого монитора в долях его рабочей области.
Двойной щелчок по острову возвращает его вниз по центру.

У верхней половины экрана панели (правка, карточки Claude) раскрываются вниз.
"""

from __future__ import annotations

import ctypes
import time

from PySide6.QtCore import QPointF, QRect, Qt
from PySide6.QtGui import QCursor, QGuiApplication, QScreen

CAPSULE_H = 40.0
BOTTOM_GAP = 28                 # от нижнего края рабочей области (над панелью задач)
TOP_GAP = 14
EDGE = 18                       # поле от краёв экрана
CORNER_HALF_W = 125.0           # полуширина записи — по ней ставим угловые магниты
DRAG_START_PX = 4
MAGNET_PX = 110                 # отпустили ближе — остров едет к магниту
THROW_MAGNET_PX = 320           # бросок ловится магнитом с большего расстояния
THROW_MIN_SPEED = 900.0         # px/с: медленнее — это не бросок, а аккуратная установка
THROW_MAX_SPEED = 4000.0
CENTER_SNAP_PX = 28
THROW_S = 0.25                  # насколько далеко «долетает» бросок (секунды скорости)
STIFFNESS, DAMPING = 300.0, 24.0

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000


def no_activate(widget) -> None:
    """Щелчок по острову не должен забирать фокус у окна, куда идёт диктовка."""
    try:
        hwnd = int(widget.winId())
        user32 = ctypes.windll.user32
        style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        if not style & WS_EX_NOACTIVATE:
            user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE)
    except Exception:
        pass


class Place:
    """Сохранённое место острова: центр капсулы в долях рабочей области монитора."""

    def __init__(self) -> None:
        self.cfg = None

    def bind(self, cfg) -> None:
        self.cfg = cfg

    # ------------------------------------------------------------ экраны
    @staticmethod
    def screen_at(point: QPointF | None = None) -> QScreen:
        point = point if point is not None else QPointF(QCursor.pos())
        return QGuiApplication.screenAt(point.toPoint()) or QGuiApplication.primaryScreen()

    @staticmethod
    def default_center(area: QRect) -> QPointF:
        return QPointF(area.x() + area.width() / 2, area.y() + area.height() - BOTTOM_GAP - CAPSULE_H / 2)

    def magnets(self, area: QRect) -> list[QPointF]:
        top = area.y() + TOP_GAP + CAPSULE_H / 2
        bottom = self.default_center(area).y()
        left = area.x() + EDGE + CORNER_HALF_W
        right = area.x() + area.width() - EDGE - CORNER_HALF_W
        mid = area.x() + area.width() / 2
        return [QPointF(mid, bottom), QPointF(mid, top), QPointF(left, top), QPointF(right, top),
                QPointF(left, bottom), QPointF(right, bottom)]

    def _saved(self) -> dict:
        data = self.cfg.get("island_place") if self.cfg is not None else None
        return data if isinstance(data, dict) else {}

    # ------------------------------------------------------------ место
    def center(self, screen: QScreen | None = None) -> QPointF:
        """Центр капсулы на экране (по умолчанию — на мониторе под курсором)."""
        screen = screen or self.screen_at()
        area = screen.availableGeometry()
        saved = self._saved().get(screen.name())
        if not saved:
            return self.default_center(area)
        fx, fy = saved
        return self.clamp(QPointF(area.x() + fx * area.width(), area.y() + fy * area.height()), area)

    def grows_up(self, point: QPointF | None = None) -> bool:
        """Панели раскрываются вверх, если остров в нижней половине экрана."""
        point = point if point is not None else self.center()
        area = self.screen_at(point).availableGeometry()
        return point.y() >= area.y() + area.height() / 2

    @staticmethod
    def clamp(point: QPointF, area: QRect, half_w: float = 60.0) -> QPointF:
        x = min(max(point.x(), area.x() + EDGE + half_w), area.x() + area.width() - EDGE - half_w)
        y = min(max(point.y(), area.y() + TOP_GAP + CAPSULE_H / 2),
                area.y() + area.height() - BOTTOM_GAP + 10 - CAPSULE_H / 2)
        return QPointF(x, y)

    def nearest_magnet(self, point: QPointF, area: QRect) -> tuple[QPointF, float]:
        best, best_d = None, float("inf")
        for magnet in self.magnets(area):
            d = ((magnet.x() - point.x()) ** 2 + (magnet.y() - point.y()) ** 2) ** 0.5
            if d < best_d:
                best, best_d = magnet, d
        return best, best_d

    def resolve(self, drop: QPointF, velocity: QPointF) -> QPointF:
        """Куда доехать после отпускания.

        Бросок: точка «долёта» прижимается к экрану, и остров едет к ближайшему
        к ней магниту. Без броска: к магниту, только если отпустили рядом,
        иначе ровно туда, где отпустили.
        """
        area = self.screen_at(drop).availableGeometry()
        speed = (velocity.x() ** 2 + velocity.y() ** 2) ** 0.5
        if speed >= THROW_MIN_SPEED:
            velocity = velocity * (min(speed, THROW_MAX_SPEED) / speed)
            landing = self.clamp(drop + velocity * THROW_S, area)
            magnet, distance = self.nearest_magnet(landing, area)
            if distance <= THROW_MAGNET_PX:
                return magnet
            drop = landing
        magnet, distance = self.nearest_magnet(drop, area)
        if distance <= MAGNET_PX:
            return magnet
        point = self.clamp(drop, area)
        mid = area.x() + area.width() / 2
        if abs(point.x() - mid) <= CENTER_SNAP_PX:
            point.setX(mid)
        return point

    def save(self, point: QPointF) -> None:
        if self.cfg is None:
            return
        screen = self.screen_at(point)
        area = screen.availableGeometry()
        data = dict(self._saved())
        if (point - self.default_center(area)).manhattanLength() < 2:
            data.pop(screen.name(), None)          # место по умолчанию не храним
        else:
            data[screen.name()] = [round((point.x() - area.x()) / area.width(), 4),
                                   round((point.y() - area.y()) / area.height(), 4)]
        self.cfg.set("island_place", data)
        self.cfg.save()

    def reset(self, screen: QScreen | None = None) -> QPointF:
        screen = screen or self.screen_at()
        point = self.default_center(screen.availableGeometry())
        self.save(point)
        return point


PLACE = Place()


class Dragger:
    """Перетаскивание и доводка на пружине для окна-острова.

    capsule_rect() — где капсула внутри окна (локальные координаты),
    place_window(center) — поставить окно так, чтобы центр капсулы был в center.
    Владелец зовёт press/move/release/double_click из событий мыши и tick(dt)
    из своего таймера кадров.
    """

    def __init__(self, widget, capsule_rect, place_window, on_moved=None) -> None:
        self.widget = widget
        self.capsule_rect = capsule_rect
        self.place_window = place_window
        self.on_moved = on_moved
        self.dragging = False
        self._press = None              # (глобальная точка нажатия, центр капсулы тогда)
        self._samples: list[tuple[float, QPointF]] = []
        self._pos: QPointF | None = None
        self._vel = QPointF()
        self._target: QPointF | None = None

    @property
    def busy(self) -> bool:
        return self.dragging or self._target is not None

    def current_center(self) -> QPointF:
        rect = self.capsule_rect()
        return QPointF(self.widget.mapToGlobal(rect.center().toPoint()))

    def press(self, event) -> bool:
        if event.button() != Qt.LeftButton or not self.capsule_rect().contains(event.position()):
            return False
        self._press = (event.globalPosition(), self.current_center())
        self._samples = [(time.monotonic(), event.globalPosition())]
        self._target = None
        return True

    def move(self, event) -> bool:
        if self._press is None:
            self.widget.setCursor(Qt.OpenHandCursor if self.capsule_rect().contains(event.position())
                                  else Qt.ArrowCursor)
            return False
        start, center = self._press
        delta = event.globalPosition() - start
        if not self.dragging and delta.manhattanLength() < DRAG_START_PX:
            return True
        self.dragging = True
        self.widget.setCursor(Qt.ClosedHandCursor)
        self._pos = center + delta
        self._samples = (self._samples + [(time.monotonic(), event.globalPosition())])[-5:]
        self.place_window(self._pos)
        if self.on_moved:
            self.on_moved()
        return True

    def release(self, event) -> bool:
        """True — это было перетаскивание (щелчок не обрабатывать)."""
        if self._press is None:
            return False
        was_drag = self.dragging
        self._press = None
        self.dragging = False
        self.widget.setCursor(Qt.OpenHandCursor)
        if not was_drag or self._pos is None:
            return False
        velocity = QPointF()
        if len(self._samples) >= 2:
            (t0, p0), (t1, p1) = self._samples[0], self._samples[-1]
            if t1 - t0 > 0.005 and time.monotonic() - t1 < 0.08:      # остановился перед отпусканием — не бросок
                velocity = (p1 - p0) / (t1 - t0)
        self._vel = velocity
        self._target = PLACE.resolve(self._pos, velocity)
        PLACE.save(self._target)
        return True

    def double_click(self, event) -> bool:
        if not self.capsule_rect().contains(event.position()):
            return False
        self._pos = self.current_center()
        self._vel = QPointF()
        self._target = PLACE.reset(PLACE.screen_at(self._pos))
        return True

    def tick(self, dt: float) -> None:
        if self._target is None or self._pos is None or self.dragging:
            return
        acc = (self._target - self._pos) * STIFFNESS - self._vel * DAMPING
        self._vel += acc * dt
        self._pos += self._vel * dt
        if (self._target - self._pos).manhattanLength() < 0.6 and self._vel.manhattanLength() < 8:
            self._pos, self._target = self._target, None
        self.place_window(self._pos)
        if self.on_moved:
            self.on_moved()
