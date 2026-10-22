"""Recover floating dock title bars without changing the saved layout format."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt, QTimer, Slot
from PySide6.QtGui import QGuiApplication, QScreen
from PySide6.QtWidgets import QDockWidget, QStyle

from ._dock_safe import is_valid


def visible_floating_position(
    frame: QRect, areas: Sequence[QRect], *, title_height: int = 30
) -> QPoint:
    """Move only unreachable title bars; negative monitor coordinates are valid."""
    available = [area for area in areas if area.isValid()]
    if not frame.isValid() or not available:
        return frame.topLeft()
    title = QRect(frame.x(), frame.y(), frame.width(), min(frame.height(), max(1, title_height)))
    for area in available:
        visible = title.intersected(area)
        if visible.height() == title.height() and visible.width() >= min(96, title.width()):
            return frame.topLeft()

    def score(area: QRect) -> tuple[int, int]:
        overlap = frame.intersected(area)
        delta = frame.center() - area.center()
        return overlap.width() * overlap.height(), -(delta.x() ** 2 + delta.y() ** 2)

    area = max(available, key=score)
    # An oversized panel keeps its size, with the title at the screen's top.
    x = min(max(frame.x(), area.left()), max(area.left(), area.right() + 1 - frame.width()))
    y = min(max(frame.y(), area.top()), max(area.top(), area.bottom() + 1 - frame.height()))
    return QPoint(x, y)


def recover_floating_dock(dock: QDockWidget) -> bool:
    if (
        not is_valid(dock)
        or not dock.isFloating()
        or not dock.isVisible()
        or dock.isMinimized()
        or dock.isMaximized()
        or dock.isFullScreen()
    ):
        return False
    frame = dock.frameGeometry()
    title_height = max(
        dock.geometry().top() - frame.top(),
        dock.style().pixelMetric(QStyle.PixelMetric.PM_TitleBarHeight),
        1,
    )
    position = visible_floating_position(
        frame,
        [screen.availableGeometry() for screen in QGuiApplication.screens()],
        title_height=title_height,
    )
    delta = position - frame.topLeft()
    if delta.isNull():
        return False
    # QWidget geometry excludes native decorations. Translate the current
    # position rather than treating the frame top-left as client geometry.
    dock.move(dock.pos() + delta)
    return True


class FloatingDockGeometryGuard(QObject):
    """Defer recovery until mouse/native dragging and Qt layout work settle."""

    def __init__(self, dock: QDockWidget) -> None:
        super().__init__(dock)
        self._dock = dock
        self._screen_ids: set[int] = set()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._recover)
        dock.installEventFilter(self)
        dock.topLevelChanged.connect(self.schedule)
        app = QGuiApplication.instance()
        if isinstance(app, QGuiApplication):
            app.screenAdded.connect(self._screen_added)
            app.screenRemoved.connect(self._screen_removed)
        for screen in QGuiApplication.screens():
            self._screen_added(screen)
        self.schedule()

    @Slot(QScreen)
    def _screen_added(self, screen: QScreen) -> None:
        if id(screen) not in self._screen_ids:
            self._screen_ids.add(id(screen))
            screen.availableGeometryChanged.connect(self.schedule)
            screen.geometryChanged.connect(self.schedule)
        self.schedule()

    @Slot(QScreen)
    def _screen_removed(self, screen: QScreen) -> None:
        self._screen_ids.discard(id(screen))
        self.schedule()

    @Slot()
    def schedule(self) -> None:
        self._timer.start()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self._dock and event.type() in {
            QEvent.Type.Show,
            QEvent.Type.Move,
            QEvent.Type.Resize,
            QEvent.Type.WindowStateChange,
        }:
            self.schedule()
        return False

    @Slot()
    def _recover(self) -> None:
        if not is_valid(self._dock) or not self._dock.isFloating() or not self._dock.isVisible():
            return
        if QGuiApplication.mouseButtons() != Qt.MouseButton.NoButton or self._dock.property(
            "sr_floating_dragging"
        ):
            self.schedule()
            return
        recover_floating_dock(self._dock)


def install_floating_dock_guard(dock: QDockWidget) -> FloatingDockGeometryGuard:
    guard = getattr(dock, "_floating_geometry_guard", None)
    if not isinstance(guard, FloatingDockGeometryGuard) or not is_valid(guard):
        guard = FloatingDockGeometryGuard(dock)
        dock._floating_geometry_guard = guard  # type: ignore[attr-defined]
    return guard


def install_floating_dock_guards(docks: Iterable[QDockWidget]) -> None:
    for dock in docks:
        if dock.isFloating():
            install_floating_dock_guard(dock).schedule()
