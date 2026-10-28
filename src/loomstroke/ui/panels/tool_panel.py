"""Tool selection and property panels for plugin-based tools."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

from PySide6.QtCore import QEvent, QObject, QPoint, QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QColor,
    QContextMenuEvent,
    QHideEvent,
    QIcon,
    QMouseEvent,
    QMoveEvent,
    QPainter,
    QPixmap,
    QResizeEvent,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from loomstroke.core.event_bus_extensions import get_extended_event_bus
from loomstroke.core.events import Events
from loomstroke.core.tool_manager import ToolManager
from loomstroke.host_api import HostAPI
from loomstroke.plugins.sdk.tool import (
    PropertySpec,
    PropertyType,
    ToolActionSpec,
    ToolDescriptor,
    ToolMetadata,
)
from loomstroke.ui.icon_loader import tint_pixmap_for_current_theme
from loomstroke.ui.panels.pen_property_presentation import PenPropertyPresentation
from loomstroke.ui.panels.tool_panel_overflow import ToolOverflowMenuController
from loomstroke.ui.panels.tool_panel_presets import ToolPresetController
from loomstroke.ui.theme import current_theme_name
from loomstroke.ui.widgets.foreground_background_swatch import ForegroundBackgroundSwatch

from ..translations import (
    TranslationBinder,
    translate_runtime_text,
    translate_with_fallback,
    translated_literal_domain,
)
from ..widget_event_subscriptions import WidgetEventSubscriptions

logger = logging.getLogger(__name__)

QtSvgRenderer: Any
try:  # pragma: no cover - optional dependency
    from PySide6.QtSvg import QSvgRenderer as QtSvgRenderer
except ImportError:  # pragma: no cover - optional dependency
    QtSvgRenderer = None


_ICON_SIZE = QSize(24, 24)
_TOOL_BUTTON_SIDE = _ICON_SIZE.width() + 8
_TOOL_BUTTON_SPACING = 10
_TOOL_BUTTON_SELECTED_BORDER_DARK = "#a78bfa"
_TOOL_BUTTON_SELECTED_BORDER_LIGHT = "#6256c7"
_FLYOUT_LONG_PRESS_MS = 350
_RESIZE_FINISH_DEBOUNCE_MS = 200
_POPUP_PROBE_ENV = "LOOMSTROKE_DEBUG_TOOLBAR_POPUP"
_POPUP_PROBE_KEY = "_loomstroke_toolbar_popup_probe"
_POPUP_PROBE_TRACKED_PROP = "_loomstroke_toolbar_popup_probe_tracked"
_POPUP_PROBE_RESTORE_PROP = "_loomstroke_toolbar_popup_probe_restore_pending"


def _popup_probe_mode() -> str:
    return os.getenv(_POPUP_PROBE_ENV, "").strip().lower()


def _popup_probe_hold_enabled() -> bool:
    return _popup_probe_mode() not in {"", "0", "false", "log"}


def _popup_probe_matches(widget: QWidget) -> bool:
    return widget.isWindow() and bool(widget.findChildren(QSlider))


def _popup_probe_is_on_screen(widget: QWidget) -> bool:
    geometry = widget.frameGeometry()
    if geometry.isEmpty():
        geometry = widget.geometry()
    if geometry.isEmpty():
        return False
    screens = QApplication.screens()
    if not screens:
        return True
    return any(geometry.intersects(screen.geometry()) for screen in screens)


def _restore_popup_probe_window(widget: QWidget) -> None:
    with suppress(RuntimeError):  # pragma: no cover - defensive against deleted widgets
        if not _popup_probe_matches(widget):
            return
        if not _popup_probe_hold_enabled():
            widget.setProperty(_POPUP_PROBE_RESTORE_PROP, False)
            return
        if not _popup_probe_is_on_screen(widget):
            widget.setProperty(_POPUP_PROBE_TRACKED_PROP, False)
            widget.setProperty(_POPUP_PROBE_RESTORE_PROP, False)
            return
        widget.setProperty(_POPUP_PROBE_RESTORE_PROP, False)
        widget.show()
        widget.raise_()


class _PopupProbe(QObject):
    def eventFilter(self, obj: Any, event: Any) -> bool:  # pragma: no cover - debug instrumentation
        if not isinstance(obj, QWidget):
            return False
        if event.type() == QEvent.Type.Show:
            self._handle_show(obj)
            return False
        if not bool(obj.property(_POPUP_PROBE_TRACKED_PROP)):
            return False
        if event.type() == QEvent.Type.Move:
            self._handle_move(obj)
            return False
        return self._handle_visibility_event(obj, event)

    def _handle_show(self, widget: QWidget) -> None:
        if not _popup_probe_matches(widget):
            return
        if not bool(widget.property(_POPUP_PROBE_TRACKED_PROP)):
            parent = widget.parentWidget()
            parent_name = parent.metaObject().className() if parent else "None"
            flags = int(widget.windowFlags())
            logger.info(
                "Toolbar popup probe: %s title='%s' name='%s' geom=%s flags=0x%x parent=%s",
                widget.metaObject().className(),
                widget.windowTitle(),
                widget.objectName(),
                widget.geometry(),
                flags,
                parent_name,
            )
        widget.setProperty(_POPUP_PROBE_TRACKED_PROP, True)
        widget.setProperty(_POPUP_PROBE_RESTORE_PROP, False)

    def _handle_move(self, widget: QWidget) -> None:
        if _popup_probe_is_on_screen(widget):
            return
        widget.setProperty(_POPUP_PROBE_TRACKED_PROP, False)
        widget.setProperty(_POPUP_PROBE_RESTORE_PROP, False)

    def _handle_visibility_event(self, widget: QWidget, event: QEvent) -> bool:
        if not _popup_probe_hold_enabled():
            return False
        if event.type() == QEvent.Type.Close:
            return self._handle_close(widget, event)
        if event.type() == QEvent.Type.Hide:
            self._handle_hide(widget)
        return False

    def _handle_close(self, widget: QWidget, event: QEvent) -> bool:
        if not _popup_probe_is_on_screen(widget):
            widget.setProperty(_POPUP_PROBE_TRACKED_PROP, False)
            widget.setProperty(_POPUP_PROBE_RESTORE_PROP, False)
            return False
        if hasattr(event, "ignore"):
            event.ignore()
        logger.info(
            "Toolbar popup probe: holding on-screen popup title='%s' name='%s'",
            widget.windowTitle(),
            widget.objectName(),
        )
        QTimer.singleShot(0, lambda w=widget: _restore_popup_probe_window(w))
        return True

    def _handle_hide(self, widget: QWidget) -> None:
        if not _popup_probe_is_on_screen(widget):
            widget.setProperty(_POPUP_PROBE_TRACKED_PROP, False)
            widget.setProperty(_POPUP_PROBE_RESTORE_PROP, False)
            return
        if bool(widget.property(_POPUP_PROBE_RESTORE_PROP)):
            return
        widget.setProperty(_POPUP_PROBE_RESTORE_PROP, True)
        logger.info(
            "Toolbar popup probe: restoring on-screen popup title='%s' name='%s'",
            widget.windowTitle(),
            widget.objectName(),
        )
        QTimer.singleShot(0, lambda w=widget: _restore_popup_probe_window(w))


def _ensure_popup_probe() -> None:
    if not _popup_probe_mode():
        return
    app = QApplication.instance()
    if app is None:
        return
    if getattr(app, _POPUP_PROBE_KEY, None) is not None:
        return
    probe = _PopupProbe()
    app.installEventFilter(probe)
    setattr(app, _POPUP_PROBE_KEY, probe)


class ToolFlyoutButton(QToolButton):
    """Button that represents a group of tools with a flyout menu."""

    tool_chosen = Signal(str)

    def __init__(self, group_id: str, group_label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._group_id = group_id
        self._group_label = group_label
        self._tool_ids: list[str] = []
        self._current_tool_id: str | None = None
        self._metadata: dict[str, ToolMetadata | None] = {}
        self._descriptors: dict[str, ToolDescriptor] = {}
        self._menu = QMenu(self)
        self.setMenu(self._menu)
        self.setCheckable(True)
        self.setAutoRaise(True)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.setIconSize(_ICON_SIZE)
        self.setMinimumSize(_TOOL_BUTTON_SIDE, _TOOL_BUTTON_SIDE)
        self.setStyleSheet(
            "QToolButton {"
            " border: none;"
            " background: transparent;"
            " padding: 0px;"
            "}"
            "QToolButton:hover { background: transparent; border: none; }"
            "QToolButton:checked { background: transparent; border: none; }"
            "QToolButton:pressed { background: transparent; border: none; }"
            "QToolButton::menu-indicator { image: none; width: 0px; }"
        )
        self.clicked.connect(self._handle_clicked)
        self._long_press_timer = QTimer(self)
        self._long_press_timer.setSingleShot(True)
        self._long_press_timer.timeout.connect(self._show_menu)

    def configure(
        self,
        *,
        label: str,
        tool_ids: list[str],
        metadata: dict[str, ToolMetadata | None],
        descriptors: dict[str, ToolDescriptor],
    ) -> None:
        self._group_label = label
        self._tool_ids = tool_ids
        self._metadata = metadata
        self._descriptors = descriptors
        if not self._current_tool_id or self._current_tool_id not in self._tool_ids:
            self._current_tool_id = self._tool_ids[0] if self._tool_ids else None
        self._rebuild_menu()
        self._update_visuals()

    def set_active_tool(self, tool_id: str | None) -> None:
        if tool_id in self._tool_ids:
            self._current_tool_id = tool_id
            self.setChecked(True)
        else:
            self.setChecked(False)
        self._update_visuals()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # pragma: no cover - event passthrough
        if (
            event.button() == Qt.MouseButton.LeftButton
            and len(self._tool_ids) > 1
            and self._menu.actions()
        ):
            self._long_press_timer.start(_FLYOUT_LONG_PRESS_MS)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # pragma: no cover - event passthrough
        self._long_press_timer.stop()
        super().mouseReleaseEvent(event)

    def contextMenuEvent(
        self, event: QContextMenuEvent
    ) -> None:  # pragma: no cover - event passthrough
        if self._menu.actions():
            self._show_menu()
        event.accept()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt naming
        super().changeEvent(event)
        if event.type() in (
            QEvent.Type.ApplicationPaletteChange,
            QEvent.Type.PaletteChange,
        ):
            self._rebuild_menu()
            self._update_visuals()

    def paintEvent(self, event: Any) -> None:  # noqa: N802
        super().paintEvent(event)
        has_flyout = len(self._tool_ids) > 1 and bool(self._menu.actions())
        if not self.isChecked() and not has_flyout:
            return
        painter = QPainter(self)
        try:
            if self.isChecked():
                self._paint_selected_border(painter)
            if has_flyout:
                self._paint_flyout_corner(painter)
        finally:
            painter.end()

    def _paint_selected_border(self, painter: QPainter) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = painter.pen()
        pen.setColor(
            QColor(
                _TOOL_BUTTON_SELECTED_BORDER_LIGHT
                if current_theme_name() == "light"
                else _TOOL_BUTTON_SELECTED_BORDER_DARK
            )
        )
        pen.setWidth(2)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -2, -2), 3, 3)

    def _paint_flyout_corner(self, painter: QPainter) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        pen = painter.pen()
        pen.setColor(QColor("#000000" if current_theme_name() == "light" else "#ffffff"))
        pen.setWidth(1)
        painter.setPen(pen)
        margin = 1
        length = 6
        right = self.width() - margin
        bottom = self.height() - margin
        painter.drawLine(right, bottom - length, right, bottom)
        painter.drawLine(right - length, bottom, right, bottom)

    def _handle_clicked(self) -> None:
        if self._current_tool_id:
            self.tool_chosen.emit(self._current_tool_id)

    def _rebuild_menu(self) -> None:
        self._menu.clear()
        if len(self._tool_ids) <= 1:
            self.setMenu(cast(QMenu, None))
            self.setPopupMode(QToolButton.ToolButtonPopupMode.DelayedPopup)
            return
        self.setMenu(self._menu)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.DelayedPopup)
        for tool_id in self._tool_ids:
            descriptor = self._descriptors.get(tool_id)
            metadata = self._metadata.get(tool_id)
            label = _display_name(metadata, descriptor)
            action: QAction | None = self._menu.addAction(label)
            if action is None:
                continue
            icon = _icon_from_metadata(metadata)
            if not icon.isNull():
                action.setIcon(icon)
            action.triggered.connect(lambda _checked=False, tid=tool_id: self.tool_chosen.emit(tid))

    def _update_visuals(self) -> None:
        descriptor = self._descriptors.get(self._current_tool_id or "")
        metadata = self._metadata.get(self._current_tool_id or "")
        icon = _icon_from_metadata(metadata)
        active_label = _display_name(metadata, descriptor)
        if icon.isNull():
            self.setIcon(QIcon())
            label = active_label or self._group_label
            self.setText(label[:2].upper())
            self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        else:
            self.setIcon(icon)
            self.setText("")
            self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)

        tooltip_lines: list[str] = []
        if descriptor:
            tooltip_lines.append(active_label)
            shortcuts = [
                shortcut
                for shortcut in (descriptor.shortcut, descriptor.secondary_shortcut)
                if shortcut
            ]
            if shortcuts:
                tooltip_lines.append(
                    translate_with_fallback(
                        "ToolFlyoutButton",
                        "Shortcuts: {shortcuts}",
                    ).format(shortcuts=" / ".join(shortcuts))
                )
        tooltip_lines.append(
            translate_with_fallback("ToolFlyoutButton", "Group: {label}").format(
                label=self._group_label
            )
        )
        self.setToolTip("\n".join(tooltip_lines))

    def _show_menu(self) -> None:
        if self._menu.actions():
            self.showMenu()

    def refresh_translations(self) -> None:
        self._rebuild_menu()
        self._update_visuals()


class ToolToolbar(QWidget):
    """Icon toolbar showing grouped tool flyouts."""

    tool_selected = Signal(str)
    orientation_changed = Signal(bool)  # True = horizontal
    content_changed = Signal()

    def __init__(
        self,
        host_api: HostAPI | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.host_api: HostAPI | None = None
        self.tool_manager: ToolManager | None = None
        self._buttons: dict[str, ToolFlyoutButton] = {}
        self._tool_to_group: dict[str, str] = {}
        self._metadata: dict[str, ToolMetadata | None] = {}
        self._descriptors: dict[str, ToolDescriptor] = {}
        self._layout_is_horizontal: bool | None = None
        self._forced_horizontal: bool | None = None
        self._event_bus = get_extended_event_bus()
        self._event_subscriptions = WidgetEventSubscriptions(self)
        self._event_handlers, self._event_tokens = (
            self._event_subscriptions.handlers,
            self._event_subscriptions.tokens,
        )
        self._placeholder_state = "no_tools"

        self._setup_ui()
        if host_api:
            self.set_host_api(host_api)
        self._sync_layout_for_size(self.size())
        self._subscribe_events()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(1)

        self.placeholder = QLabel("", self)
        self._set_placeholder_state("no_tools")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.placeholder)

        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._button_container = QWidget(self.scroll_area)
        self.scroll_area.setWidget(self._button_container)
        self.scroll_area.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._button_layout = QBoxLayout(QBoxLayout.Direction.TopToBottom, self._button_container)
        self._button_layout.setContentsMargins(0, 0, 0, 0)
        self._button_layout.setSpacing(_TOOL_BUTTON_SPACING)
        self._button_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._button_layout.addStretch(1)
        layout.addWidget(self.scroll_area, 1)
        self._toggle_placeholder(True)

    def set_host_api(self, host_api: HostAPI | None) -> None:
        manager = _resolve_tool_manager(host_api) if host_api else None
        if host_api is self.host_api and manager is self.tool_manager:
            return
        self.host_api = host_api
        self.set_tool_manager(manager)

    def set_tool_manager(self, manager: ToolManager | None) -> None:
        if manager is self.tool_manager:
            return
        if self.tool_manager:
            self._disconnect_manager()
        self.tool_manager = manager
        if manager:
            self._connect_manager()
            self._rebuild_toolbar()
        else:
            self._set_placeholder_state("no_manager")
            self._clear_buttons()
            self._toggle_placeholder(True)

    def _connect_manager(self) -> None:
        assert self.tool_manager
        self.tool_manager.tool_registered.connect(self._handle_tools_changed)
        self.tool_manager.tool_activated.connect(self._handle_tool_activated)
        self.tool_manager.tool_deactivated.connect(self._handle_tool_deactivated)

    def _disconnect_manager(self) -> None:
        assert self.tool_manager
        _safe_disconnect(self.tool_manager.tool_registered, self._handle_tools_changed)
        _safe_disconnect(self.tool_manager.tool_activated, self._handle_tool_activated)
        _safe_disconnect(self.tool_manager.tool_deactivated, self._handle_tool_deactivated)

    def _handle_tools_changed(self, _payload: dict[str, Any]) -> None:
        self._rebuild_toolbar()

    def _handle_tool_activated(self, tool_id: str) -> None:
        button = self._buttons.get(self._tool_to_group.get(tool_id, ""))
        if button:
            button.set_active_tool(tool_id)
        for group_id, candidate in self._buttons.items():
            if group_id != self._tool_to_group.get(tool_id, ""):
                candidate.set_active_tool(None)

    def _handle_tool_deactivated(self, _tool_id: str) -> None:
        for button in self._buttons.values():
            button.set_active_tool(None)

    def _handle_button_choice(self, tool_id: str) -> None:
        if self.tool_manager:
            self.tool_manager.activate_tool(tool_id)
        self.tool_selected.emit(tool_id)

    def _rebuild_toolbar(self) -> None:
        if not self.tool_manager:
            self._set_placeholder_state("no_manager")
            self._toggle_placeholder(True)
            self.content_changed.emit()
            return

        descriptors = self.tool_manager.get_registered_tools()
        if not descriptors:
            self._set_placeholder_state("no_plugins")
            self._clear_buttons()
            self._toggle_placeholder(True)
            self.content_changed.emit()
            return

        self._toggle_placeholder(False)
        self._clear_buttons()
        self._metadata = {}
        self._descriptors = {}
        grouped: dict[str, dict[str, Any]] = {}
        tool_orders: dict[str, int] = {}
        for descriptor in descriptors:
            metadata = self.tool_manager.get_tool_metadata(descriptor.id)
            self._metadata[descriptor.id] = metadata
            self._descriptors[descriptor.id] = descriptor
            group_id = (
                metadata.group_id
                if metadata and metadata.group_id
                else descriptor.category or descriptor.id
            )
            if metadata and metadata.group_label:
                group_label = translate_runtime_text(metadata.group_label)
            elif descriptor.category:
                group_label = descriptor.category.title()
            else:
                group_label = _display_name(metadata, descriptor)
            group_order = (
                metadata.group_order
                if metadata and metadata.group_order is not None
                else descriptor.order
            )
            record = grouped.setdefault(
                group_id,
                {"label": group_label, "order": group_order, "tool_ids": []},
            )
            record["tool_ids"].append(descriptor.id)
            tool_orders[descriptor.id] = descriptor.order

        for data in grouped.values():
            data["tool_ids"].sort(key=lambda tid: (tool_orders.get(tid, 0), tid))

        ordered_groups = sorted(
            grouped.items(),
            key=lambda item: (item[1]["order"], item[1]["label"].lower()),
        )

        for group_id, data in ordered_groups:
            button = ToolFlyoutButton(group_id, data["label"], self._button_container)
            button.configure(
                label=data["label"],
                tool_ids=data["tool_ids"],
                metadata=self._metadata,
                descriptors=self._descriptors,
            )
            button.tool_chosen.connect(self._handle_button_choice)
            self._buttons[group_id] = button
            for tool_id in data["tool_ids"]:
                self._tool_to_group[tool_id] = group_id
            self._button_layout.insertWidget(self._button_layout.count() - 1, button)

        active = self.tool_manager.get_active_tool()
        if active:
            self._handle_tool_activated(active.id)
        self.content_changed.emit()

    def _subscribe_events(self) -> None:
        self._event_subscriptions.subscribe(
            self._event_bus, Events.LANGUAGE_CHANGED, self._on_language_changed
        )

    def _on_language_changed(self, _payload: Any) -> None:
        self.refresh_translations()

    def refresh_translations(self) -> None:
        if self.tool_manager:
            self._rebuild_toolbar()
            return
        self._set_placeholder_state(self._placeholder_state)

    def _set_placeholder_state(self, state: str) -> None:
        self._placeholder_state = state
        if state == "no_manager":
            text = translate_with_fallback("ToolToolbar", "Tool manager not available")
        elif state == "no_plugins":
            text = translate_with_fallback("ToolToolbar", "No tool plugins registered")
        else:
            text = translate_with_fallback("ToolToolbar", "No tools available")
        self.placeholder.setText(text)

    def _clear_buttons(self) -> None:
        while self._button_layout.count() > 1:
            item = self._button_layout.takeAt(0)
            if item is None:
                continue
            if widget := item.widget():
                widget.deleteLater()
        self._buttons.clear()
        self._tool_to_group.clear()

    def _toggle_placeholder(self, visible: bool) -> None:
        self.placeholder.setVisible(visible)
        self.scroll_area.setVisible(not visible)

    def button_for_tool(self, tool_id: str) -> ToolFlyoutButton | None:
        """Return the flyout button responsible for the given tool id."""

        return self._buttons.get(self._tool_to_group.get(tool_id, ""))

    def is_horizontal(self) -> bool:
        """Return True if the toolbar is currently in horizontal orientation."""
        return bool(self._layout_is_horizontal)

    def set_orientation(self, horizontal: bool) -> None:
        """Switch the toolbar between horizontal and vertical orientation."""
        self._forced_horizontal = horizontal
        self._apply_orientation(horizontal)
        self.orientation_changed.emit(horizontal)

    def toggle_orientation(self) -> None:
        """Toggle between horizontal and vertical orientation."""
        self.set_orientation(not self.is_horizontal())

    def resizeEvent(self, event: Any) -> None:  # pragma: no cover - Qt event passthrough
        super().resizeEvent(event)
        if self._forced_horizontal is None:
            self._sync_layout_for_size(event.size())

    def _sync_layout_for_size(self, size: QSize) -> None:
        is_horizontal = False if size.height() <= 0 else size.width() > size.height()
        self._apply_orientation(is_horizontal)

    def _apply_orientation(self, is_horizontal: bool) -> None:
        if self._layout_is_horizontal == is_horizontal:
            return
        self._layout_is_horizontal = is_horizontal
        direction = (
            QBoxLayout.Direction.LeftToRight if is_horizontal else QBoxLayout.Direction.TopToBottom
        )
        self._button_layout.setDirection(direction)
        self._button_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        # Scroll bar policies: allow scrolling only along the layout direction
        if is_horizontal:
            self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        else:
            self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)


class ToolListPanel(ToolToolbar):
    """Backward-compatible alias exposing the legacy class name."""


class FloatingToolToolbarFrame(QWidget):
    """Movable non-docking frame for the tool toolbar.

    The toolbar behaves like a compact overlay rather than a QDockWidget:
    drag the small grip to move it, release near a main-window edge to
    snap. Top/bottom snaps use a horizontal layout; left/right snaps use
    a vertical layout.
    """

    _SNAP_MARGIN = 18
    _EDGE_PADDING = 8
    _HANDLE_SIZE = 12
    _SWATCH_GAP = 4

    def __init__(
        self,
        host_api: HostAPI | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("floatingToolToolbarFrame")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self._drag_offset: QPoint | None = None
        self._orientation_horizontal = False
        self._raise_scheduled = False
        self._underlay_refresh_scheduled = False
        self._parent_pos: QPoint | None = None
        self._syncing_parent_pos = False
        self._z_order_parent: QWidget | None = None
        self._z_order_sibling_ids: set[int] = set()
        self._closed_while_visible = False

        self.toolbar = ToolToolbar(host_api, self)
        self.toolbar.orientation_changed.connect(self._on_toolbar_orientation_changed)
        self.toolbar.content_changed.connect(self._resize_to_toolbar)

        self._drag_handle = QFrame(self)
        self._drag_handle.setObjectName("floatingToolToolbarGrip")
        self._drag_handle.setFixedHeight(self._HANDLE_SIZE)
        self._drag_handle.setCursor(Qt.CursorShape.SizeAllCursor)
        self._drag_handle.installEventFilter(self)

        self.color_swatch = ForegroundBackgroundSwatch(_TOOL_BUTTON_SIDE, self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        layout.addWidget(self._drag_handle)
        self._body_layout = QBoxLayout(QBoxLayout.Direction.TopToBottom)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(self._SWATCH_GAP)
        self._body_layout.addWidget(self.toolbar)
        self._body_layout.addWidget(self.color_swatch, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addLayout(self._body_layout)

        self.setStyleSheet(
            "QWidget#floatingToolToolbarFrame {"
            " background-color: palette(window);"
            " border: 1px solid palette(mid);"
            " border-radius: 4px;"
            "}"
            "QFrame#floatingToolToolbarGrip {"
            " background-color: palette(mid);"
            " border-radius: 2px;"
            "}"
        )
        self.set_orientation(False)
        self._install_z_order_tracking()

    def set_host_api(self, host_api: HostAPI | None) -> None:
        self.toolbar.set_host_api(host_api)
        self._resize_to_toolbar()

    def set_orientation(self, horizontal: bool) -> None:
        self._orientation_horizontal = horizontal
        self.toolbar.set_orientation(horizontal)
        self._resize_to_toolbar()
        if not self.isHidden():
            self._request_underlay_refresh()

    def show_at_default_position(self) -> None:
        self._resize_to_toolbar()
        parent = self.parentWidget()
        if parent is not None:
            target = QPoint(
                self._EDGE_PADDING,
                max(self._EDGE_PADDING, parent.rect().center().y() // 2),
            )
            self._move_to_parent_pos(target)
        self.show()
        self.raise_()

    def snapshot_state(self) -> dict[str, bool | int]:
        return {
            "x": int(self._current_parent_pos().x()),
            "y": int(self._current_parent_pos().y()),
            "horizontal": bool(self._orientation_horizontal),
            "visible": not self.isHidden() or self._closed_while_visible,
        }

    def restore_state(self, state: Mapping[str, object]) -> bool:
        try:
            x = int(cast(Any, state["x"]))
            y = int(cast(Any, state["y"]))
        except (KeyError, TypeError, ValueError):
            return False
        horizontal = bool(state.get("horizontal", self._orientation_horizontal))
        self.set_orientation(horizontal)
        self._resize_to_toolbar()
        self._move_to_parent_pos(self._clamp_pos(QPoint(x, y)))
        return True

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self._drag_handle:
            if event.type() == QEvent.Type.MouseButtonPress:
                mouse_event = cast(QMouseEvent, event)
                if mouse_event.button() == Qt.MouseButton.LeftButton:
                    self._drag_offset = (
                        mouse_event.globalPosition().toPoint() - self._current_global_pos()
                    )
                    self.raise_()
                    return True
            if event.type() == QEvent.Type.MouseMove and self._drag_offset is not None:
                mouse_event = cast(QMouseEvent, event)
                self._move_with_snap_preview(mouse_event.globalPosition().toPoint())
                return True
            if event.type() == QEvent.Type.MouseButtonRelease and self._drag_offset is not None:
                mouse_event = cast(QMouseEvent, event)
                self._finish_drag(mouse_event.globalPosition().toPoint())
                return True
        if self._should_restore_z_order(obj, event):
            self._schedule_raise_if_visible()
        return super().eventFilter(obj, event)

    def showEvent(self, event: QShowEvent) -> None:  # pragma: no cover - Qt event passthrough
        super().showEvent(event)
        self._closed_while_visible = False
        self._install_z_order_tracking()
        self._schedule_raise_if_visible()
        self._request_underlay_refresh()

    def closeEvent(self, event: QCloseEvent) -> None:
        # The frame has no user close control; Qt 6 QApplication.quit() closes
        # this Tool window before MainWindow persists layout state, so keep the
        # user's visibility choice instead of saving the shutdown-hidden state.
        self._closed_while_visible = not self.isHidden()
        super().closeEvent(event)

    def hideEvent(self, event: QHideEvent) -> None:  # pragma: no cover - Qt event passthrough
        super().hideEvent(event)
        self._request_underlay_refresh()

    def moveEvent(self, event: QMoveEvent) -> None:  # pragma: no cover - Qt event passthrough
        super().moveEvent(event)
        if not self._syncing_parent_pos:
            self._parent_pos = self._current_parent_pos()
        if not self.isHidden():
            self._request_underlay_refresh()

    def resizeEvent(self, event: QResizeEvent) -> None:  # pragma: no cover - Qt event passthrough
        super().resizeEvent(event)
        if not self.isHidden():
            self._request_underlay_refresh()

    def _install_z_order_tracking(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        if parent is not self._z_order_parent:
            parent.installEventFilter(self)
            self._z_order_parent = parent
        self._install_sibling_tracking(parent)

    def _install_sibling_tracking(self, parent: QWidget) -> None:
        for child in parent.children():
            if not isinstance(child, QWidget) or child is self:
                continue
            child_id = id(child)
            if child_id in self._z_order_sibling_ids:
                continue
            child.installEventFilter(self)
            self._z_order_sibling_ids.add(child_id)

    def _should_restore_z_order(self, obj: QObject, event: QEvent) -> bool:
        event_type = event.type()
        parent = self.parentWidget()
        if obj is parent:
            if event_type == QEvent.Type.ChildAdded:
                self._install_z_order_tracking()
                return True
            return event_type in {
                QEvent.Type.LayoutRequest,
                QEvent.Type.Resize,
                QEvent.Type.Show,
            }
        if isinstance(obj, QWidget) and obj is not self and obj.parentWidget() is parent:
            return event_type in {
                QEvent.Type.Show,
                QEvent.Type.Move,
                QEvent.Type.Resize,
                QEvent.Type.ZOrderChange,
            }
        return False

    def _schedule_raise_if_visible(self) -> None:
        if self._raise_scheduled or self.isHidden():
            return
        self._raise_scheduled = True
        QTimer.singleShot(0, self, self._raise_if_visible)

    def _raise_if_visible(self) -> None:
        self._raise_scheduled = False
        if self.isHidden():
            return
        self.raise_()
        self._request_underlay_refresh()

    def _current_global_pos(self) -> QPoint:
        return self.mapToGlobal(QPoint(0, 0))

    def _current_parent_pos(self) -> QPoint:
        if self._parent_pos is not None and self.isWindow():
            return QPoint(self._parent_pos)
        parent = self.parentWidget()
        if parent is None:
            return self.pos()
        return parent.mapFromGlobal(self._current_global_pos())

    def _move_to_parent_pos(self, pos: QPoint) -> None:
        self._parent_pos = QPoint(pos)
        parent = self.parentWidget()
        if self.isWindow() and parent is not None:
            self._syncing_parent_pos = True
            try:
                self.move(parent.mapToGlobal(pos))
            finally:
                self._syncing_parent_pos = False
            return
        self.move(pos)

    def sync_to_parent_position(self) -> None:
        if self._parent_pos is None or self.isHidden():
            return
        self._move_to_parent_pos(self._parent_pos)
        self.raise_()

    def _global_to_parent_pos(self, pos: QPoint) -> QPoint:
        parent = self.parentWidget()
        if parent is None:
            return pos
        return parent.mapFromGlobal(pos)

    def _request_underlay_refresh(self) -> None:
        self._request_underlay_refresh_now()
        if self._underlay_refresh_scheduled:
            return
        self._underlay_refresh_scheduled = True
        QTimer.singleShot(16, self, self._request_underlay_refresh_later)

    def _request_underlay_refresh_later(self) -> None:
        self._underlay_refresh_scheduled = False
        self._request_underlay_refresh_now()

    def _request_underlay_refresh_now(self) -> None:
        with suppress(RuntimeError):
            parent = self.parentWidget()
            if parent is None:
                return
            refresh = getattr(parent, "_request_native_canvas_refresh", None)
            if callable(refresh):
                refresh(force_full_recompose=False)

    def _move_with_snap_preview(self, global_pos: QPoint) -> None:
        assert self._drag_offset is not None
        target = self._global_to_parent_pos(global_pos - self._drag_offset)
        self._move_to_parent_pos(self._clamp_pos(target))

    def _finish_drag(self, global_pos: QPoint) -> None:
        assert self._drag_offset is not None
        target = self._global_to_parent_pos(global_pos - self._drag_offset)
        self._drag_offset = None
        snapped, edge = self._snap_pos(target)
        if edge in {"top", "bottom"}:
            self.set_orientation(True)
        elif edge in {"left", "right"}:
            self.set_orientation(False)
        self._move_to_parent_pos(self._clamp_pos(snapped))
        self.raise_()

    def _snap_pos(self, pos: QPoint) -> tuple[QPoint, str | None]:
        parent = self.parentWidget()
        if parent is None:
            return pos, None
        bounds = parent.rect().adjusted(
            self._EDGE_PADDING,
            self._EDGE_PADDING,
            -self._EDGE_PADDING,
            -self._EDGE_PADDING,
        )
        distances = {
            "left": abs(pos.x() - bounds.left()),
            "right": abs(bounds.right() - (pos.x() + self.width())),
            "top": abs(pos.y() - bounds.top()),
            "bottom": abs(bounds.bottom() - (pos.y() + self.height())),
        }
        edge, distance = min(distances.items(), key=lambda item: item[1])
        if distance > self._SNAP_MARGIN:
            return pos, None
        snapped = QPoint(pos)
        if edge == "left":
            snapped.setX(bounds.left())
        elif edge == "right":
            snapped.setX(bounds.right() - self.width())
        elif edge == "top":
            snapped.setY(bounds.top())
        elif edge == "bottom":
            snapped.setY(bounds.bottom() - self.height())
        return snapped, edge

    def _clamp_pos(self, pos: QPoint) -> QPoint:
        parent = self.parentWidget()
        if parent is None:
            return pos
        bounds = parent.rect().adjusted(
            self._EDGE_PADDING,
            self._EDGE_PADDING,
            -self._EDGE_PADDING,
            -self._EDGE_PADDING,
        )
        max_x = max(bounds.left(), bounds.right() - self.width())
        max_y = max(bounds.top(), bounds.bottom() - self.height())
        return QPoint(
            min(max(pos.x(), bounds.left()), max_x),
            min(max(pos.y(), bounds.top()), max_y),
        )

    def _on_toolbar_orientation_changed(self, horizontal: bool) -> None:
        self._orientation_horizontal = horizontal
        self._resize_to_toolbar()

    def _resize_to_toolbar(self) -> None:
        count = max(len(getattr(self.toolbar, "_buttons", {})), 1)
        spacing = _TOOL_BUTTON_SPACING
        content_length = count * _TOOL_BUTTON_SIDE + max(0, count - 1) * spacing
        swatch_extent = _TOOL_BUTTON_SIDE + self._SWATCH_GAP
        chrome = self._HANDLE_SIZE + 12
        parent = self.parentWidget()
        max_width = parent.width() - self._EDGE_PADDING * 2 if parent is not None else 640
        max_height = parent.height() - self._EDGE_PADDING * 2 if parent is not None else 480
        if self._orientation_horizontal:
            self._body_layout.setDirection(QBoxLayout.Direction.LeftToRight)
            width = min(max_width, content_length + swatch_extent + 14)
            height = min(max_height, _TOOL_BUTTON_SIDE + chrome)
        else:
            self._body_layout.setDirection(QBoxLayout.Direction.TopToBottom)
            width = min(max_width, _TOOL_BUTTON_SIDE + 14)
            height = min(max_height, content_length + swatch_extent + chrome)
        self.setFixedSize(max(width, _TOOL_BUTTON_SIDE), max(height, _TOOL_BUTTON_SIDE))
        toolbar_width = self.width() - 8
        toolbar_height = self.height() - self._HANDLE_SIZE - 10
        if self._orientation_horizontal:
            toolbar_width -= swatch_extent
        else:
            toolbar_height -= swatch_extent
        self.toolbar.setFixedSize(max(toolbar_width, 1), max(toolbar_height, 1))
        self.move(self._clamp_pos(self.pos()))


class ToolPropertiesPanel(QWidget):
    """Panel rendering presets and property controls for the active tool."""

    property_changed = Signal(str, object)
    _preset_is_horizontal: bool | None
    _content_layout: QBoxLayout
    actions_layout: QBoxLayout

    def __init__(
        self,
        host_api: HostAPI | None = None,
        parent: QWidget | None = None,
        *,
        layout_mode: str = "panel",
    ) -> None:
        super().__init__(parent)
        self._layout_mode = layout_mode if layout_mode in {"panel", "bar"} else "panel"
        self._bar_mode = self._layout_mode == "bar"
        self.host_api: HostAPI | None = None
        self.tool_manager: ToolManager | None = None
        self._current_tool_id: str | None = None
        self._property_bindings: dict[str, _PropertyBinding] = {}
        self._action_bindings: list[_ActionBinding] = []
        self._action_groups: list[_ActionGroupLayout] = []
        self._action_refresh_timer = QTimer(self)
        self._action_refresh_timer.setInterval(300)
        self._action_refresh_timer.timeout.connect(self._refresh_action_enabled_states)
        self._layout_is_horizontal: bool | None = None
        self._pending_layout_size: QSize | None = None
        self._reflow_timer = QTimer(self)
        self._reflow_timer.setSingleShot(True)
        self._reflow_timer.setInterval(150)
        self._reflow_timer.timeout.connect(self._apply_pending_reflow)
        self._overflow_timer = QTimer(self)
        self._overflow_timer.setSingleShot(True)
        self._overflow_timer.timeout.connect(self._update_overflow_menu)
        self._resize_finish_timer = QTimer(self)
        self._resize_finish_timer.setSingleShot(True)
        self._resize_finish_timer.setInterval(_RESIZE_FINISH_DEBOUNCE_MS)
        self._resize_finish_timer.timeout.connect(self._on_resize_finished)
        self._resize_in_progress, self._preset_is_horizontal = False, None
        self._content_container: QWidget | None = None
        self._overflow_button: QToolButton | None = None
        self._overflow_menu: QMenu | None = None
        self._overflow_action: QWidgetAction | None = None
        self._overflow_container: QWidget | None = None
        self._overflow_layout: QVBoxLayout | None = None
        self._overflowed_items: set[QWidget] = set()
        self._overflow_controller = ToolOverflowMenuController(self)
        self._bar_items: list[QWidget] = []
        self._bar_property_items: list[QWidget] = []
        self._bar_action_items: list[QWidget] = []
        self._event_bus = get_extended_event_bus()
        self._translation_binder = TranslationBinder(self.tr)
        self._event_subscriptions = WidgetEventSubscriptions(self)
        self._event_handlers, self._event_tokens = (
            self._event_subscriptions.handlers,
            self._event_subscriptions.tokens,
        )
        self._pending_display_tool_id: str | None = None
        self._display_debounce_timer = self._make_debounce_timer()

        self._setup_ui()
        self._preset_controller = ToolPresetController(
            parent=self,
            combo=self.preset_combo,
            tool_manager=lambda: self.tool_manager,
            current_tool_id=lambda: self._current_tool_id,
            collect_settings=self._collect_current_settings,
            translate_label=self._translate_tool_label,
            translate_panel_text=self._ts,
        )
        self._seed_tool_label_sources()
        if host_api:
            self.set_host_api(host_api)
        self._sync_content_layout(self._reflow_reference_size())
        self._subscribe_events()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt naming
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange and hasattr(self, "_preset_controller"):
            self.refresh_translations()

    def _make_debounce_timer(self) -> QTimer:
        t = QTimer(self)
        t.setSingleShot(True)
        t.setInterval(60)
        t.timeout.connect(self._apply_pending_display)
        return t

    def _setup_ui(self) -> None:
        self._pen_property_presentation = PenPropertyPresentation(self)
        _ensure_popup_probe()
        layout = self._setup_root_layout()
        self._setup_preset_panel(layout)
        content = self._setup_scroll_area(layout)
        if self._bar_mode:
            self._setup_overflow_control(layout)
        self._setup_content_layout(content)
        self._setup_property_sections(content)
        self._setup_action_area(content)

    def _setup_root_layout(self) -> QBoxLayout:
        if self._bar_mode:
            layout: QBoxLayout = QHBoxLayout(self)
            layout.setContentsMargins(4, 0, 4, 0)
            layout.setSpacing(5)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.setMinimumWidth(0)
        else:
            layout = QVBoxLayout(self)
            layout.setContentsMargins(6, 0, 6, 0)
            layout.setSpacing(1)
        return layout

    def _setup_preset_panel(self, layout: QBoxLayout) -> None:
        self._preset_container = QWidget(self)
        self._preset_layout = QGridLayout(self._preset_container)
        self._preset_layout.setContentsMargins(0, 0, 0, 0)
        self._preset_layout.setSpacing(5 if self._bar_mode else 1)
        preset_alignment = (
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            if self._bar_mode
            else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        self._preset_layout.setAlignment(preset_alignment)

        preset_text = self._ts("Preset:")
        self._preset_label = QLabel(preset_text, self._preset_container)
        self._translation_binder.register(self._preset_label.setText, preset_text)
        self.preset_combo = QComboBox(self._preset_container)
        self.preset_combo.currentTextChanged.connect(self._on_preset_selected)

        save_text = self._ts("Save")
        self.save_preset_button = QPushButton(save_text, self._preset_container)
        self._translation_binder.register(self.save_preset_button.setText, save_text)
        self._apply_compact_button(self.save_preset_button)
        self.save_preset_button.clicked.connect(self._on_save_preset)

        delete_text = self._ts("Delete")
        self.delete_preset_button = QPushButton(delete_text, self._preset_container)
        self._translation_binder.register(self.delete_preset_button.setText, delete_text)
        self._apply_compact_button(self.delete_preset_button)
        self.delete_preset_button.clicked.connect(self._on_delete_preset)

        if not self._bar_mode:
            layout.addWidget(self._preset_container)

    def _setup_scroll_area(self, layout: QBoxLayout) -> QWidget:
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        if self._bar_mode:
            self.scroll_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.scroll_area.setMinimumWidth(0)
            self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget(self.scroll_area)
        self.scroll_area.setWidget(content)
        self._content_container = content
        if self._bar_mode:
            content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            content.setMinimumWidth(0)

        scroll_alignment = (
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            if self._bar_mode
            else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        self.scroll_area.setAlignment(scroll_alignment)
        layout.addWidget(self.scroll_area, 1)
        return content

    def _setup_overflow_control(self, layout: QBoxLayout) -> None:
        self._overflow_button = QToolButton(self)
        self._overflow_button.setText("...")
        more_text = self._ts("More")
        self._overflow_button.setToolTip(more_text)
        self._translation_binder.register(self._overflow_button.setToolTip, more_text)
        self._overflow_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._overflow_button.setAutoRaise(True)
        self._overflow_menu = QMenu(self._overflow_button)
        self._overflow_button.setMenu(self._overflow_menu)
        self._overflow_button.setVisible(False)
        self._apply_compact_tool_button(self._overflow_button)

        self._overflow_container = QWidget(self._overflow_menu)
        self._overflow_layout = QVBoxLayout(self._overflow_container)
        self._overflow_layout.setContentsMargins(4, 4, 4, 4)
        self._overflow_layout.setSpacing(5)
        self._overflow_action = QWidgetAction(self._overflow_menu)
        self._overflow_action.setDefaultWidget(self._overflow_container)
        self._overflow_menu.addAction(self._overflow_action)
        layout.addWidget(self._overflow_button)

    def _setup_content_layout(self, content: QWidget) -> None:
        content_direction = (
            QBoxLayout.Direction.LeftToRight if self._bar_mode else QBoxLayout.Direction.TopToBottom
        )
        self._content_layout = QBoxLayout(content_direction, content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(5 if self._bar_mode else 1)
        content_alignment = (
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            if self._bar_mode
            else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        self._content_layout.setAlignment(content_alignment)
        if self._bar_mode:
            self._preset_container.setParent(content)
            self._content_layout.addWidget(self._preset_container)

    def _setup_property_sections(self, content: QWidget) -> None:
        if self._bar_mode:
            self.common_group = QWidget(content)
            self.common_form = QGridLayout(self.common_group)
            self.common_form.setContentsMargins(0, 0, 0, 0)
            self.common_form.setSpacing(5)
            self.common_form.setAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
        else:
            common_title = self._ts("Common Properties")
            self.common_group = QGroupBox(common_title, content)
            self._translation_binder.register(self.common_group.setTitle, common_title)
            self.common_form = QGridLayout()
            self.common_form.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            self.common_group.setLayout(self.common_form)
        if not self._bar_mode:
            self._content_layout.addWidget(self.common_group)

        if self._bar_mode:
            self.specific_group = QWidget(content)
            self.specific_form = QGridLayout(self.specific_group)
            self.specific_form.setContentsMargins(0, 0, 0, 0)
            self.specific_form.setSpacing(5)
            self.specific_form.setAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
        else:
            specific_title = self._ts("Tool Properties")
            self.specific_group = QGroupBox(specific_title, content)
            self._translation_binder.register(self.specific_group.setTitle, specific_title)
            self.specific_form = QGridLayout()
            self.specific_form.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            self.specific_group.setLayout(self.specific_form)
        if not self._bar_mode:
            self._content_layout.addWidget(self.specific_group)

    def _setup_action_area(self, content: QWidget) -> None:
        self.actions_container = QWidget(content)
        if self._bar_mode:
            self.actions_layout = QHBoxLayout(self.actions_container)
        else:
            self.actions_layout = QVBoxLayout(self.actions_container)
        self.actions_layout.setContentsMargins(0, 0, 0, 0)
        self.actions_layout.setSpacing(5 if self._bar_mode else 6)
        actions_alignment = (
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            if self._bar_mode
            else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        self.actions_layout.setAlignment(actions_alignment)
        if not self._bar_mode:
            self._content_layout.addWidget(self.actions_container)

        placeholder_text = self._ts("Select a tool to edit properties")
        self.placeholder = QLabel(placeholder_text, content)
        self._translation_binder.register(self.placeholder.setText, placeholder_text)
        placeholder_alignment = (
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            if self._bar_mode
            else Qt.AlignmentFlag.AlignCenter
        )
        self.placeholder.setAlignment(placeholder_alignment)
        self._content_layout.addWidget(self.placeholder)
        self._content_layout.addStretch(1)
        if self._bar_mode:
            self._bar_items = [self._preset_container, self.placeholder]

    def _subscribe_events(self) -> None:
        self._event_subscriptions.subscribe(
            self._event_bus, Events.LANGUAGE_CHANGED, self._on_language_changed
        )

    def _on_language_changed(self, _payload: Any) -> None:
        self.refresh_translations()

    def refresh_translations(self) -> None:
        self._translation_binder.set_translator(self.tr)
        self._translation_binder.retranslate()
        if self._current_tool_id and self.tool_manager:
            metadata = self.tool_manager.get_tool_metadata(self._current_tool_id)
            if metadata is not None:
                settings = self.tool_manager.get_tool_settings(self._current_tool_id)
                self._build_property_forms(metadata, settings)
                self._preset_controller.refresh(metadata)
                self._build_action_groups(metadata, settings)
                self._request_tool_reflow()

    def _ts(self, text: str) -> str:
        return translated_literal_domain("ToolPropertiesPanel", text, use_fallback=True)

    def _seed_tool_label_sources(self) -> None:
        for _text in (
            self._ts("Size"),
            self._ts("Opacity"),
            self._ts("Hardness"),
            self._ts("Basic Pen"),
            self._ts("Brushes"),
            self._ts("Text"),
            self._ts("Vertical Text"),
            self._ts("Path"),
            self._ts("Vector"),
            self._ts("Vector Pen (Precise)"),
            self._ts("Stamp Density"),
            self._ts("Spacing"),
            self._ts("Pressure Sensitive"),
            self._ts("Flow"),
            self._ts("Tone Range"),
            self._ts("Tone Gamma"),
            self._ts("Stabilization"),
            self._ts("Tilt Shading"),
            self._ts("Strength"),
            self._ts("Anti alias"),
            self._ts("Anti-alias"),
            self._ts("Tolerance"),
            self._ts("Sample"),
            self._ts("Contiguous"),
            self._ts("Mode"),
            self._ts("Operation"),
            self._ts("Feather"),
            self._ts("Pixel Perfect"),
            self._ts("Feather Radius"),
            self._ts("Mask Radius"),
            self._ts("Transform X"),
            self._ts("Transform Y"),
            self._ts("Scale X"),
            self._ts("Scale Y"),
            self._ts("Rotation"),
            self._ts("Color Tolerance"),
            self._ts("Gap Close (px)"),
            self._ts("Fill Target"),
            self._ts("Current Layer"),
            self._ts("All Layers"),
            self._ts("Merged"),
            self._ts("Active Layer"),
            self._ts("New Layer"),
            self._ts("Text Mode"),
            self._ts("Font Size (pt)"),
            self._ts("Paragraph Flow"),
            self._ts("Line Height"),
            self._ts("Tracking"),
            self._ts("Convert"),
            self._ts("Selection To Area"),
            self._ts("point"),
            self._ts("area"),
            self._ts("left"),
            self._ts("right"),
            self._ts("Rectangle"),
            self._ts("Ellipse"),
            self._ts("Lasso"),
            self._ts("Magic Wand"),
            self._ts("Vector"),
            self._ts("Replace"),
            self._ts("Add"),
            self._ts("Subtract"),
            self._ts("Intersect"),
            self._ts("Edit Mode"),
            self._ts("Auto Close"),
            self._ts("Shift Mode"),
            self._ts("Auto Close Tolerance (px)"),
            self._ts("Stroke Width"),
            self._ts("Stroke Color"),
            self._ts("Fill Enabled"),
            self._ts("Fill Color"),
            self._ts("Anchor Density"),
            self._ts("Handle Smoothness"),
            self._ts("anchor"),
            self._ts("shape"),
            self._ts("constraint"),
            self._ts("disable_snap"),
            self._ts("Clipboard"),
            self._ts("Copy"),
            self._ts("Cut"),
            self._ts("Paste"),
            self._ts("Mask Ops"),
            self._ts("Invert"),
            self._ts("Expand"),
            self._ts("Contract"),
            self._ts("Transform"),
            self._ts("Move"),
            self._ts("Scale"),
            self._ts("Rotate"),
            self._ts("Flip H"),
            self._ts("Flip V"),
            self._ts("Reset"),
            self._ts("Apply"),
            self._ts("Cancel"),
            self._ts("Enable Eyedropper"),
            self._ts("Match Reference"),
            self._ts("Palette Only"),
            self._ts("Sketch"),
            self._ts("Inking"),
            self._ts("Marker"),
        ):
            pass

    def _translate_tool_label(self, text: str) -> str:
        if not text:
            return ""
        source_text = getattr(text, "source_text", text)
        domain = getattr(text, "translation_domain", None)
        if isinstance(domain, str) and domain:
            translated = translate_with_fallback(domain, source_text)
            if translated != source_text:
                return translated
        return translate_with_fallback("ToolPropertiesPanel", source_text)

    def set_host_api(self, host_api: HostAPI | None) -> None:
        manager = _resolve_tool_manager(host_api) if host_api else None
        if host_api is self.host_api and manager is self.tool_manager:
            return
        self.host_api = host_api
        self.set_tool_manager(manager)

    def set_tool_manager(self, manager: ToolManager | None) -> None:
        if manager is self.tool_manager:
            return
        if self.tool_manager:
            self._disconnect_manager()
        self.tool_manager = manager
        if manager:
            self._connect_manager()
            active = manager.get_active_tool()
            if active:
                self._display_tool_immediate(active.id)
            else:
                self._display_placeholder()
        else:
            self._display_placeholder()

    def _connect_manager(self) -> None:
        assert self.tool_manager
        self.tool_manager.tool_activated.connect(self._display_tool)
        self.tool_manager.tool_setting_changed.connect(self._on_tool_setting_changed)
        self.tool_manager.tool_deactivated.connect(self._handle_tool_deactivated)

    def _disconnect_manager(self) -> None:
        assert self.tool_manager
        _safe_disconnect(self.tool_manager.tool_activated, self._display_tool)
        _safe_disconnect(self.tool_manager.tool_setting_changed, self._on_tool_setting_changed)
        _safe_disconnect(self.tool_manager.tool_deactivated, self._handle_tool_deactivated)

    def _display_placeholder(self) -> None:
        self._pen_property_presentation.clear()
        self._current_tool_id = None
        self._property_bindings.clear()
        self._action_bindings.clear()
        self._action_groups.clear()
        _clear_form_layout(self.common_form)
        _clear_form_layout(self.specific_form)
        _clear_layout(self.actions_layout)
        self.common_group.setVisible(False)
        self.specific_group.setVisible(False)
        self.actions_container.setVisible(False)
        self.placeholder.setVisible(True)
        self._action_refresh_timer.stop()
        self._preset_controller.clear()
        if self._bar_mode:
            self._clear_bar_widgets(self._bar_property_items)
            self._clear_bar_widgets(self._bar_action_items)
            self._update_bar_placeholder()
            self._rebuild_bar_items()
        self._request_tool_reflow()

    @Slot(str)
    def _display_tool(self, tool_id: str) -> None:
        """Schedule tool display with debounce to avoid costly widget
        rebuilds during rapid tool switching (e.g. shortcut spam)."""
        self._pending_display_tool_id = tool_id
        self._display_debounce_timer.start()

    def _display_tool_immediate(self, tool_id: str) -> None:
        if not self.tool_manager:
            return
        metadata = self.tool_manager.get_tool_metadata(tool_id)
        if metadata is None:
            self._display_placeholder()
            return
        self._current_tool_id = tool_id
        settings = self.tool_manager.get_tool_settings(tool_id)
        self._build_property_forms(metadata, settings)
        self._preset_controller.refresh(metadata)
        self._build_action_groups(metadata, settings)
        self._request_tool_reflow()

    def _apply_pending_display(self) -> None:
        tool_id = self._pending_display_tool_id
        self._pending_display_tool_id = None
        if tool_id is None:
            self._display_placeholder()
        elif self.tool_manager:
            self._display_tool_immediate(tool_id)

    def _request_tool_reflow(self) -> None:
        if self._bar_mode:
            # Defer overflow updates during tool rebuild to avoid popup flicker.
            self._resize_in_progress = True
            self._resize_finish_timer.start()
            self._set_overflow_visible(False)
            if self._overflow_menu is not None:
                self._overflow_menu.hide()
            if self._overflow_button is not None:
                self._overflow_button.setDown(False)
        self._pending_layout_size = self._reflow_reference_size()
        size = self._pending_layout_size
        if size.width() <= 0 or size.height() <= 0:
            self._schedule_reflow(restart=True)
            return
        self._reflow_timer.stop()
        QTimer.singleShot(0, self, self._apply_pending_reflow)

    def _build_property_forms(self, metadata: ToolMetadata, settings: dict[str, Any]) -> None:
        self._pen_property_presentation.clear()
        self._property_bindings.clear()
        if self._bar_mode:
            self._clear_bar_widgets(self._bar_property_items)
        _clear_form_layout(self.common_form)
        _clear_form_layout(self.specific_form)

        properties = self._property_specs_for_current_surface(metadata)
        grouped = self._pen_property_presentation.configure(metadata)
        common_specs = [spec for spec in properties if spec.is_common]
        specific_specs = [spec for spec in properties if not spec.is_common]

        if self._bar_mode:
            common_items = self._build_bar_property_items(common_specs, settings)
            specific_items = self._build_bar_property_items(specific_specs, settings)
            self._bar_property_items = common_items + specific_items
            if grouped:
                self._bar_property_items = self._pen_property_presentation.arrange_bar(
                    common_specs + specific_specs, self._bar_property_items
                )
            self.common_group.setVisible(False)
            self.specific_group.setVisible(False)
            self._update_bar_placeholder()
            self._rebuild_bar_items()
            return

        if grouped:
            populated = self._pen_property_presentation.populate_panel(properties, settings)
            self.common_group.setVisible(False)
            self.specific_group.setVisible(populated)
            self.placeholder.setVisible(not populated)
            return

        has_common = self._populate_property_form(self.common_form, common_specs, settings)
        from .tool_panel_custom_options import add_custom_tool_options  # noqa: PLC0415

        has_specific = self._populate_property_form(self.specific_form, specific_specs, settings)
        has_custom = add_custom_tool_options(self, metadata)

        self.common_group.setVisible(has_common)
        self.specific_group.setVisible(has_specific or has_custom)
        self.placeholder.setVisible(not (has_common or has_specific or has_custom))

    def _property_specs_for_current_surface(self, metadata: ToolMetadata) -> list[PropertySpec]:
        specs = list(metadata.properties)
        if not self._bar_mode:
            from .tool_panel_custom_options import custom_property_keys  # noqa: PLC0415

            custom_keys = custom_property_keys(self, metadata)
            return [spec for spec in specs if spec.key not in custom_keys]
        floating_keys = self._floating_toolbar_property_keys(metadata)
        if not floating_keys:
            return specs
        return [spec for spec in specs if spec.key not in floating_keys]

    def _floating_toolbar_property_keys(self, metadata: ToolMetadata) -> set[str]:
        """Return property keys rendered by the active tool's floating toolbar."""
        keys: set[str] = set()
        tool_id = self._current_tool_id or metadata.tool_id
        plugin = self._tool_plugin(tool_id) if self.tool_manager is not None else None
        spec_fn = getattr(plugin, "context_toolbar_spec", None) if plugin is not None else None
        if callable(spec_fn):
            try:
                spec = spec_fn()
            except Exception:
                logger.debug("context_toolbar_spec failed for %s", tool_id, exc_info=True)
                spec = None
            if (
                spec is not None
                and getattr(spec, "enabled", False)
                and getattr(spec, "build_widget_fn", None) is None
            ):
                property_keys = getattr(spec, "property_keys", None)
                if property_keys is not None:
                    keys = {key for key in property_keys if isinstance(key, str)}
                else:
                    keys = {spec.key for spec in metadata.properties if spec.is_common}
        return keys

    def _tool_plugin(self, tool_id: str) -> Any | None:
        tools = getattr(self.tool_manager, "_tools", None)
        if not isinstance(tools, dict):
            return None
        state = tools.get(tool_id)
        return getattr(state, "plugin", None) if state is not None else None

    def _build_bar_property_items(
        self, specs: list[PropertySpec], settings: dict[str, Any]
    ) -> list[QWidget]:
        items: list[QWidget] = []
        for spec in specs:
            widget, setter, reader = self._create_widget_for_spec(spec)
            value = settings.get(spec.key, spec.default_value)
            if value is not None:
                setter(value)
            label_text = self._translate_tool_label(spec.display_name)
            label = QLabel(label_text + ":", self._content_container or self)
            label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            item = self._create_bar_property_item(label, widget)
            self._property_bindings[spec.key] = _PropertyBinding(
                setter=setter,
                reader=reader,
                widget=widget,
            )
            items.append(item)
        return items

    def _refresh_presets(self, metadata: ToolMetadata) -> None:
        self._preset_controller.refresh(metadata)

    def _current_preset_name(self) -> str:
        return self._preset_controller.current_name()

    def _build_action_groups(self, metadata: ToolMetadata, settings: dict[str, Any]) -> None:
        self._action_bindings.clear()
        self._action_groups.clear()
        _clear_layout(self.actions_layout)

        if self._bar_mode:
            self._build_bar_action_groups(metadata, settings)
            return
        self._build_standalone_action_groups(metadata, settings)

    def _build_bar_action_groups(self, metadata: ToolMetadata, settings: dict[str, Any]) -> None:
        self._clear_bar_widgets(self._bar_action_items)
        if not metadata.action_groups:
            self._set_empty_action_groups(is_bar_mode=True)
            return

        action_items: list[QWidget] = []
        for group in metadata.action_groups:
            if group.label:
                label = QLabel(
                    self._translate_tool_label(group.label),
                    self._content_container or self,
                )
                label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                action_items.append(label)
            for action in group.actions:
                widget = self._create_action_widget(action, settings)
                action_items.append(widget)
                self._action_bindings.append(
                    _ActionBinding(
                        widget=widget,
                        enabled_when=action.enabled_when,
                    )
                )

        self._bar_action_items = action_items
        self.actions_container.setVisible(False)
        self._update_bar_placeholder()
        self._rebuild_bar_items()
        self._refresh_action_enabled_states()
        if any(binding.enabled_when for binding in self._action_bindings):
            self._action_refresh_timer.start()
        else:
            self._action_refresh_timer.stop()

    def _build_standalone_action_groups(
        self,
        metadata: ToolMetadata,
        settings: dict[str, Any],
    ) -> None:
        if not metadata.action_groups:
            self._set_empty_action_groups(is_bar_mode=False)
            return

        for group in metadata.action_groups:
            widgets: list[QWidget] = []
            title = self._translate_tool_label(group.label)
            container = QGroupBox(title, self.actions_container)
            box_layout = QGridLayout(container)
            box_layout.setContentsMargins(6, 6, 6, 6)
            box_layout.setSpacing(6)
            box_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

            if group.label:
                label_text = self._translate_tool_label(group.label)
                label = QLabel(label_text, container)
                label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                widgets.append(label)

            for action in group.actions:
                widget = self._create_action_widget(action, settings)
                widgets.append(widget)
                self._action_bindings.append(
                    _ActionBinding(
                        widget=widget,
                        enabled_when=action.enabled_when,
                    )
                )
            self._populate_action_group_layout(box_layout, widgets)
            self.actions_layout.addWidget(container)
            self._action_groups.append(
                _ActionGroupLayout(group=container, layout=box_layout, widgets=widgets)
            )

        self.actions_layout.addStretch(1)
        self.actions_container.setVisible(True)
        self._refresh_action_enabled_states()
        if any(binding.enabled_when for binding in self._action_bindings):
            self._action_refresh_timer.start()
        else:
            self._action_refresh_timer.stop()

    def _set_empty_action_groups(self, is_bar_mode: bool) -> None:
        self.actions_container.setVisible(False)
        self._action_refresh_timer.stop()
        if is_bar_mode:
            self._update_bar_placeholder()
            self._rebuild_bar_items()

    def _create_action_widget(self, action: ToolActionSpec, settings: dict[str, Any]) -> QWidget:
        parent = (
            self._content_container
            if self._bar_mode and self._content_container is not None
            else self.actions_container
        )
        label_text = self._translate_tool_label(action.label)
        if action.kind == "toggle":
            checkbox = QCheckBox(label_text, parent)
            if action.key in settings:
                checkbox.setChecked(bool(settings[action.key]))
            checkbox.toggled.connect(
                lambda checked, key=action.key: self._dispatch_action(
                    key, {"checked": bool(checked)}
                )
            )
            return checkbox
        button = QPushButton(label_text, parent)
        self._apply_compact_button(button)
        button.clicked.connect(
            lambda _checked=False, key=action.key: self._dispatch_action(key, None)
        )
        return button

    def _apply_compact_button(self, button: QPushButton) -> None:
        if not self._bar_mode:
            return
        button.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        button.setMinimumHeight(20)
        button.setMaximumHeight(24)
        if not isinstance(button, _ColorButton):
            button.setStyleSheet("QPushButton { padding: 2px 6px; }")

    def _apply_compact_tool_button(self, button: QToolButton) -> None:
        if not self._bar_mode:
            return
        button.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        button.setMinimumHeight(20)
        button.setMaximumHeight(24)
        button.setStyleSheet("QToolButton { padding: 2px 6px; }")

    def _clear_bar_widgets(self, widgets: list[QWidget]) -> None:
        if not widgets:
            return
        safe_parent = self._content_container or self
        for widget in widgets:
            if self._content_layout is not None:
                self._content_layout.removeWidget(widget)
            if self._overflow_layout is not None:
                self._overflow_layout.removeWidget(widget)
            if widget in self._overflowed_items:
                self._overflowed_items.discard(widget)
            widget.hide()
            if widget.parent() is not safe_parent:
                widget.setParent(safe_parent)
            widget.deleteLater()
        widgets.clear()

    def _clear_content_layout_widgets(self) -> None:
        if self._content_layout is None:
            return
        for index in range(self._content_layout.count() - 1, -1, -1):
            item = self._content_layout.itemAt(index)
            widget = item.widget() if item is not None else None
            if widget is not None:
                self._content_layout.removeWidget(widget)

    def _create_bar_property_item(self, label: QLabel, widget: QWidget) -> QWidget:
        parent = self._content_container or self
        container = QWidget(parent)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if label.parent() is not container:
            label.setParent(container)
        if widget.parent() is not container:
            widget.setParent(container)
        layout.addWidget(label)
        layout.addWidget(widget)
        return container

    def _update_bar_placeholder(self) -> None:
        if not self._bar_mode:
            return
        has_items = bool(self._bar_property_items) or bool(self._bar_action_items)
        self.placeholder.setVisible(not has_items)

    def _rebuild_bar_items(self) -> None:
        if not self._bar_mode or self._content_container is None:
            return
        has_items = bool(self._bar_property_items) or bool(self._bar_action_items)
        self.placeholder.setVisible(not has_items)
        items = [self._preset_container]
        items.extend(self._bar_property_items)
        items.extend(self._bar_action_items)
        if not has_items:
            items.append(self.placeholder)
        self._clear_content_layout_widgets()
        self._bar_items = items
        insert_index = max(0, self._content_layout.count() - 1)
        for item in items:
            if item.parent() is not self._content_container:
                item.setParent(self._content_container)
            self._content_layout.insertWidget(insert_index, item)
            insert_index += 1
        self._overflowed_items.clear()

    def _dispatch_action(self, action_key: str, payload: dict[str, Any] | None) -> None:
        if not self.tool_manager or not self._current_tool_id:
            return
        scheduler = getattr(self.tool_manager, "schedule_tool_action_async", None)
        if callable(scheduler):
            scheduler(self._current_tool_id, action_key, payload)
        else:
            self.tool_manager.dispatch_tool_action(self._current_tool_id, action_key, payload)
        self._refresh_action_enabled_states()

    def _refresh_action_enabled_states(self) -> None:
        if not self._action_bindings:
            return
        for binding in self._action_bindings:
            binding.widget.setEnabled(self._action_condition(binding.enabled_when))

    def _action_condition(self, enabled_when: str | None) -> bool:
        if not enabled_when:
            return True
        if enabled_when == "has_selection":
            return self._has_selection()
        if enabled_when == "clipboard_not_empty":
            return self._clipboard_has_payload()
        return True

    def _has_selection(self) -> bool:
        if not self.host_api:
            return False
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return False
        getter = getattr(canvas, "get_selection", None)
        if callable(getter):
            try:
                selection = getter()
            except Exception:
                selection = None
            if selection is not None:
                bounds = getattr(selection, "bounds", None)
                if bool(bounds):
                    return True
        bounds = self._read_selection_bounds(canvas)
        return self._selection_bounds_has_area(bounds) or self._has_vector_selection(canvas)

    def _read_selection_bounds(self, canvas: Any) -> Any | None:
        getter = getattr(canvas, "get_selection_bounds", None)
        if callable(getter):
            try:
                return getter()
            except Exception:
                return None
        return getattr(canvas, "_selection_bounds", None)

    def _selection_bounds_has_area(self, bounds: Any | None) -> bool:
        if bounds is None:
            return False
        if hasattr(bounds, "isEmpty"):
            return not bounds.isEmpty()
        if isinstance(bounds, (tuple, list)) and len(bounds) == 4:
            return bool(bounds[2] > 0 and bounds[3] > 0)
        return False

    def _has_vector_selection(self, canvas: Any) -> bool:
        selection = getattr(canvas, "_vector_selection", None)
        if not isinstance(selection, dict):
            return False
        bounds = selection.get("transform_bounds")
        if isinstance(bounds, (tuple, list)) and len(bounds) == 4:
            try:
                if float(bounds[2]) > float(bounds[0]) and float(bounds[3]) > float(bounds[1]):
                    return True
            except (TypeError, ValueError):
                pass
        if isinstance(selection.get("id"), str):
            return True
        ids = selection.get("ids")
        if isinstance(ids, list) and any(isinstance(vec_id, str) for vec_id in ids):
            return True
        items = selection.get("items")
        return isinstance(items, list) and any(
            isinstance(item, dict) and isinstance(item.get("id"), str) for item in items
        )

    def _clipboard_has_payload(self) -> bool:
        if not self.host_api:
            return False
        clipboard = getattr(self.host_api, "selection_clipboard", None)
        if clipboard is None:
            return False
        is_empty = getattr(clipboard, "is_empty", None)
        if callable(is_empty):
            return not bool(is_empty())
        return bool(getattr(clipboard, "payload", None))

    def _on_tool_setting_changed(self, tool_id: str, key: str, value: Any) -> None:
        if tool_id != self._current_tool_id:
            return
        binding = self._property_bindings.get(key)
        if not binding:
            return
        binding.setter(value)

    def _handle_tool_deactivated(self, tool_id: str) -> None:
        if tool_id == self._current_tool_id:
            # Use debounce: if a new tool is activated within the debounce
            # window, the placeholder rebuild is skipped entirely.
            self._pending_display_tool_id = None
            self._display_debounce_timer.start()

    def _create_widget_for_spec(
        self, spec: PropertySpec
    ) -> tuple[QWidget, Callable[[Any], None], Callable[[], Any]]:
        from loomstroke.ui.widgets.property_widget_factory import create_widget_for_spec

        widget, setter, getter = create_widget_for_spec(
            spec,
            parent=self,
            on_changed=self._on_property_changed,
            compact=self._bar_mode,
            translate_fn=self._translate_tool_label,
        )
        # Apply compact button styling for COLOR type in bar mode
        if spec.type == PropertyType.COLOR and self._bar_mode:
            self._apply_compact_button(cast(QPushButton, widget))
        return widget, setter, getter

    def _on_property_changed(self, key: str, value: Any) -> None:
        if not self.tool_manager or not self._current_tool_id:
            return
        self.tool_manager.set_tool_setting(self._current_tool_id, key, value)
        self.property_changed.emit(key, value)

    def _on_preset_selected(self, preset_name: str) -> None:
        self._preset_controller.apply_selected(preset_name)

    def _collect_current_settings(self) -> dict[str, Any]:
        if (
            self.tool_manager is not None
            and self._current_tool_id
            and hasattr(self.tool_manager, "get_tool_settings")
        ):
            return dict(self.tool_manager.get_tool_settings(self._current_tool_id))
        # Lightweight hosts without the snapshot API keep their legacy reader path.
        return {key: binding.reader() for key, binding in self._property_bindings.items()}

    def _on_save_preset(self) -> None:
        self._preset_controller.save()

    def _on_delete_preset(self) -> None:
        self._preset_controller.delete()

    def get_property_widget(self, key: str) -> QWidget | None:
        binding = self._property_bindings.get(key)
        return binding.widget if binding else None

    def sizeHint(self) -> QSize:
        size = super().sizeHint()
        if not self._bar_mode:
            return size
        height = size.height()
        if height <= 0:
            height = self.minimumHeight() if self.minimumHeight() > 0 else 28
        width = self.width()
        if width <= 0:
            width = 1
        return QSize(width, height)

    def minimumSizeHint(self) -> QSize:
        size = super().minimumSizeHint()
        if not self._bar_mode:
            return size
        height = size.height()
        if height <= 0:
            height = self.minimumHeight() if self.minimumHeight() > 0 else 28
        return QSize(0, height)

    def resizeEvent(self, event: Any) -> None:  # pragma: no cover - Qt event passthrough
        super().resizeEvent(event)
        self._schedule_reflow(self._reflow_reference_size(), restart=True)
        if self._bar_mode:
            self._resize_in_progress = True
            self._resize_finish_timer.start()

    def showEvent(self, event: Any) -> None:  # pragma: no cover - Qt event passthrough
        super().showEvent(event)
        self._schedule_reflow(self._reflow_reference_size(), restart=True)

    def _on_resize_finished(self) -> None:
        self._resize_in_progress = False
        self._schedule_overflow_update(0)

    def _schedule_overflow_update(self, delay_ms: int) -> None:
        if not self._bar_mode:
            return
        self._overflow_timer.stop()
        self._overflow_timer.setInterval(max(0, delay_ms))
        self._overflow_timer.start()

    def _maybe_update_overflow_menu(self) -> None:
        if not self._bar_mode or self._resize_in_progress:
            return
        self._schedule_overflow_update(0)

    def _sync_content_layout(self, size: QSize) -> None:
        if self._bar_mode:
            is_horizontal = True
        elif size.height() <= 0:
            is_horizontal = False
        else:
            is_horizontal = size.width() > size.height()
        if self._layout_is_horizontal != is_horizontal:
            self._layout_is_horizontal = is_horizontal
            direction = (
                QBoxLayout.Direction.LeftToRight
                if is_horizontal
                else QBoxLayout.Direction.TopToBottom
            )
            self._content_layout.setDirection(direction)
            content_alignment = (
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                if self._bar_mode
                else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
            )
            self._content_layout.setAlignment(content_alignment)
        self._sync_preset_layout()
        self._reflow_property_forms()
        self._reflow_action_groups()
        self._maybe_update_overflow_menu()

    def _sync_preset_layout(self) -> None:
        is_horizontal = bool(self._layout_is_horizontal)
        if self._preset_is_horizontal == is_horizontal:
            return
        self._preset_is_horizontal = is_horizontal
        _reset_grid_layout(self._preset_layout)
        if is_horizontal:
            self._preset_layout.addWidget(self._preset_label, 0, 0)
            self._preset_layout.addWidget(self.preset_combo, 0, 1)
            self._preset_layout.addWidget(self.save_preset_button, 0, 2)
            self._preset_layout.addWidget(self.delete_preset_button, 0, 3)
            self._preset_layout.setColumnStretch(1, 1)
        else:
            self._preset_layout.addWidget(self._preset_label, 0, 0)
            self._preset_layout.addWidget(self.preset_combo, 1, 0, 1, 2)
            self._preset_layout.addWidget(self.save_preset_button, 2, 0)
            self._preset_layout.addWidget(self.delete_preset_button, 2, 1)
            self._preset_layout.setColumnStretch(0, 1)

    def _bar_items_in_order(self) -> list[QWidget]:
        return self._overflow_controller.bar_items_in_order()

    def _update_overflow_menu(self) -> None:
        self._overflow_controller.update()

    def _can_update_overflow(self) -> bool:
        return self._overflow_controller.can_update()

    def _collect_overflow_candidates(self, items: list[QWidget]) -> list[QWidget]:
        return self._overflow_controller.collect_candidates(items)

    def _update_overflow_no_candidates(self, items: list[QWidget]) -> None:
        self._overflow_controller.update_no_candidates(items)

    def _update_overflow_with_candidates(
        self,
        items: list[QWidget],
        candidates: list[QWidget],
    ) -> None:
        self._overflow_controller.update_with_candidates(items, candidates)

    def _get_scroll_area_width(self) -> int:
        return self.scroll_area.viewport().width()

    def _set_overflow_visible(self, visible: bool) -> None:
        self._overflow_controller.set_overflow_visible(visible)

    def _bar_required_width(self, items: list[QWidget]) -> int:
        return self._overflow_controller.required_width(items)

    def _split_overflow_items(
        self, items: list[QWidget], available_width: int
    ) -> tuple[list[QWidget], list[QWidget]]:
        return self._overflow_controller.split_items(items, available_width)

    def _bar_item_width(self, item: QWidget) -> int:
        return self._overflow_controller.item_width(item)

    def _rebuild_overflow_layout(self, overflow_items: list[QWidget]) -> None:
        self._overflow_controller.rebuild_overflow_layout(overflow_items)

    def _rebuild_bar_layout(self, bar_items: list[QWidget]) -> None:
        self._overflow_controller.rebuild_bar_layout(bar_items)

    def _sync_overflow_menu_size(self, overflow_items: list[QWidget]) -> None:
        self._overflow_controller.sync_menu_size(overflow_items)

    def _reflow_reference_size(self) -> QSize:
        viewport_size = self.scroll_area.viewport().size()
        if viewport_size.width() > 0 and viewport_size.height() > 0:
            return viewport_size
        return self.size()

    def _schedule_reflow(self, size: QSize | None = None, *, restart: bool = True) -> None:
        if size is None or size.width() <= 0 or size.height() <= 0:
            size = self._reflow_reference_size()
        self._pending_layout_size = QSize(size)
        if restart or not self._reflow_timer.isActive():
            self._reflow_timer.start()

    def _apply_pending_reflow(self) -> None:
        size = self._pending_layout_size
        self._pending_layout_size = None
        if size is None or size.width() <= 0 or size.height() <= 0:
            size = self._reflow_reference_size()
        self._sync_content_layout(size)

    def _reflow_property_forms(self) -> None:
        if self._pen_property_presentation.popup_visible:
            return
        if not self.tool_manager or not self._current_tool_id:
            return
        metadata = self.tool_manager.get_tool_metadata(self._current_tool_id)
        if metadata is None:
            return
        settings = self.tool_manager.get_tool_settings(self._current_tool_id)
        self._build_property_forms(metadata, settings)

    def _reflow_action_groups(self) -> None:
        if not self._action_groups:
            return
        for group in self._action_groups:
            self._populate_action_group_layout(group.layout, group.widgets)

    def _populate_property_form(
        self,
        layout: QGridLayout,
        specs: list[PropertySpec],
        settings: dict[str, Any],
    ) -> bool:
        if not specs:
            return False
        items: list[
            tuple[PropertySpec, QLabel, QWidget, Callable[[Any], None], Callable[[], Any]]
        ] = []
        for spec in specs:
            widget, setter, reader = self._create_widget_for_spec(spec)
            value = settings.get(spec.key, spec.default_value)
            if value is not None:
                setter(value)
            label_text = self._translate_tool_label(spec.display_name)
            label = QLabel(label_text + ":", self)
            label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            items.append((spec, label, widget, setter, reader))
            self._property_bindings[spec.key] = _PropertyBinding(
                setter=setter,
                reader=reader,
                widget=widget,
            )

        available_width = self._available_width_for_layout(layout)
        columns = self._compute_form_columns(layout, items, available_width)
        columns = max(1, min(columns, len(items)))
        for index, (_spec, label, widget, _setter, _reader) in enumerate(items):
            row = index // columns
            column = index % columns
            label_column = column * 2
            field_column = label_column + 1
            layout.addWidget(label, row, label_column)
            layout.addWidget(widget, row, field_column)
        for column in range(columns):
            layout.setColumnStretch(column * 2 + 1, 1)
        return True

    def _populate_action_group_layout(
        self,
        layout: QGridLayout,
        widgets: list[QWidget],
    ) -> None:
        _reset_grid_layout(layout)
        if not widgets:
            return
        available_width = self._available_width_for_layout(layout)
        columns = self._compute_action_columns(layout, widgets, available_width)
        columns = max(1, min(columns, len(widgets)))
        for index, widget in enumerate(widgets):
            row = index // columns
            column = index % columns
            layout.addWidget(widget, row, column)

    def _available_width_for_layout(self, layout: QGridLayout) -> int:
        parent = layout.parentWidget()
        width = parent.width() if parent is not None else 0
        if width <= 0:
            width = self.scroll_area.viewport().width()
        if width <= 0:
            width = self.width()
        margins = layout.contentsMargins()
        return max(0, width - margins.left() - margins.right())

    def _compute_action_columns(
        self,
        layout: QGridLayout,
        widgets: list[QWidget],
        available_width: int,
    ) -> int:
        if self._bar_mode:
            return max(1, len(widgets))
        if not self._layout_is_horizontal:
            return 1
        if available_width <= 0:
            return 1
        spacing = _layout_spacing(layout)
        item_widths = [widget.minimumSizeHint().width() for widget in widgets]
        if not item_widths:
            return 1
        columns = len(item_widths)
        while columns > 1:
            column_widths = [0] * columns
            for index, width in enumerate(item_widths):
                column = index % columns
                column_widths[column] = max(column_widths[column], width)
            required = sum(column_widths) + spacing * (columns - 1)
            if required <= available_width:
                return columns
            columns -= 1
        return 1

    def _compute_form_columns(
        self,
        layout: QGridLayout,
        items: list[tuple[PropertySpec, QLabel, QWidget, Callable[[Any], None], Callable[[], Any]]],
        available_width: int,
    ) -> int:
        if self._bar_mode:
            return max(1, len(items))
        if not self._layout_is_horizontal:
            return 1
        if available_width <= 0:
            return 1
        spacing = _layout_spacing(layout)
        item_widths: list[int] = []
        for _spec, label, widget, _setter, _reader in items:
            label_width = label.minimumSizeHint().width()
            widget_width = widget.minimumSizeHint().width()
            item_widths.append(label_width + widget_width + spacing)
        if not item_widths:
            return 1
        columns = len(item_widths)
        while columns > 1:
            column_widths = [0] * columns
            for index, width in enumerate(item_widths):
                column = index % columns
                column_widths[column] = max(column_widths[column], width)
            required = sum(column_widths) + spacing * (columns - 1)
            if required <= available_width:
                return columns
            columns -= 1
        return 1


class ToolPanel(QWidget):
    """Compatibility wrapper hosting the list and property panels."""

    tool_selected = Signal(str)
    property_changed = Signal(str, object)

    def __init__(
        self,
        host_api: HostAPI | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.list_panel = ToolListPanel(host_api, self)
        self.properties_panel = ToolPropertiesPanel(host_api, self)

        self.list_panel.tool_selected.connect(self.tool_selected.emit)
        self.properties_panel.property_changed.connect(self.property_changed.emit)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Orientation.Vertical, self)
        splitter.addWidget(self.list_panel)
        splitter.addWidget(self.properties_panel)
        layout.addWidget(splitter)

    def set_host_api(self, host: HostAPI | None) -> None:
        self.list_panel.set_host_api(host)
        self.properties_panel.set_host_api(host)


# Helpers ----------------------------------------------------------------------


def _resolve_tool_manager(host_api: HostAPI | None) -> ToolManager | None:
    if not host_api:
        return None
    manager = getattr(host_api, "_tool_manager", None)
    if isinstance(manager, ToolManager):
        return manager
    manager = getattr(host_api, "tool_manager", None)
    if isinstance(manager, ToolManager):
        return manager
    return None


def _display_name(metadata: ToolMetadata | None, descriptor: ToolDescriptor | None) -> str:
    if metadata and metadata.display_name:
        return translate_runtime_text(metadata.display_name)
    if descriptor:
        return translate_runtime_text(descriptor.display_name)
    return ""


def _icon_from_metadata(metadata: ToolMetadata | None) -> QIcon:
    if metadata is None:
        return QIcon()
    icon_source = (metadata.icon_svg or "").strip()
    if icon_source:
        icon = _icon_from_source(icon_source)
        if not icon.isNull():
            return icon
    fallback_label = metadata.display_name or metadata.tool_id
    return _fallback_icon(fallback_label)


def _icon_from_source(source: str) -> QIcon:
    if source.startswith("<"):
        return _icon_from_svg_data(source)
    path = Path(source)
    if path.is_file():
        if path.suffix.lower() == ".svg":
            return _icon_from_svg_file(path)
        return _icon_from_raster_file(path)
    return QIcon()


def _icon_from_svg_data(source: str) -> QIcon:
    if QtSvgRenderer is None:
        return QIcon()
    try:
        renderer = QtSvgRenderer(bytearray(source, "utf-8"))
    except Exception:
        logger.debug("Failed to load SVG icon data", exc_info=True)
        return QIcon()
    if not renderer.isValid():
        return QIcon()
    return _render_svg_renderer(renderer)


def _icon_from_svg_file(path: Path) -> QIcon:
    if QtSvgRenderer is None:
        return QIcon()
    try:
        renderer = QtSvgRenderer(str(path))
    except Exception:
        logger.debug("Failed to load SVG icon file %s", path, exc_info=True)
        return QIcon()
    if not renderer.isValid():
        return QIcon()
    return _render_svg_renderer(renderer)


def _render_svg_renderer(renderer: Any) -> QIcon:
    pixmap = QPixmap(_ICON_SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        renderer.render(painter)
    finally:
        painter.end()
    tint_pixmap_for_current_theme(pixmap)
    return QIcon(pixmap)


def _icon_from_raster_file(path: Path) -> QIcon:
    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        return QIcon()
    pixmap = pixmap.scaled(
        _ICON_SIZE,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    return QIcon(pixmap)


def _fallback_icon(label: str) -> QIcon:
    text = (label or "").strip()
    if not text:
        return QIcon()
    glyph = _fallback_glyph(text)
    pixmap = QPixmap(_ICON_SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    rect = pixmap.rect().adjusted(1, 1, -1, -1)
    painter.setBrush(_fallback_color(text))
    painter.setPen(Qt.GlobalColor.transparent)
    painter.drawRoundedRect(rect, 4, 4)
    painter.setPen(QColor(255, 255, 255))
    font = painter.font()
    font.setBold(True)
    font.setPixelSize(max(10, _ICON_SIZE.height() // 3))
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, glyph)
    painter.end()
    return QIcon(pixmap)


def _fallback_glyph(label: str) -> str:
    parts = [part for part in label.replace("_", " ").split() if part]
    if not parts:
        return label[:2].upper()
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[1][0]).upper()


def _fallback_color(label: str) -> QColor:
    seed = 0
    for ch in label:
        seed = (seed * 31 + ord(ch)) & 0xFFFFFFFF
    r = 80 + (seed & 0x7F)
    g = 80 + ((seed >> 7) & 0x7F)
    b = 80 + ((seed >> 14) & 0x7F)
    return QColor(r, g, b)


def _set_checkbox(widget: QCheckBox, value: Any) -> None:
    widget.blockSignals(True)
    widget.setChecked(bool(value))
    widget.blockSignals(False)


def _clear_form_layout(layout: QFormLayout | QGridLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item is None:
            continue
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
    if isinstance(layout, QGridLayout):
        for column in range(layout.columnCount()):
            layout.setColumnStretch(column, 0)
        for row in range(layout.rowCount()):
            layout.setRowStretch(row, 0)


def _clear_layout(layout: QBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item is None:
            continue
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.deleteLater()


def _layout_spacing(layout: QGridLayout) -> int:
    spacing = layout.horizontalSpacing()
    if spacing < 0:
        spacing = layout.spacing()
    if spacing < 0:
        spacing = 6
    return spacing


def _reset_grid_layout(layout: QGridLayout) -> None:
    parent = layout.parentWidget()
    while layout.count():
        item = layout.takeAt(0)
        if item is None:
            continue
        widget = item.widget()
        if widget is not None and parent is not None:
            widget.setParent(parent)
    for column in range(layout.columnCount()):
        layout.setColumnStretch(column, 0)
    for row in range(layout.rowCount()):
        layout.setRowStretch(row, 0)


class _ColorButton(QPushButton):
    color_changed = Signal(tuple)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = QColor(0, 0, 0)
        self.clicked.connect(self._choose_color)
        self._update_style()

    def color(self) -> tuple[float, float, float, float]:
        return (
            self._color.redF(),
            self._color.greenF(),
            self._color.blueF(),
            self._color.alphaF(),
        )

    def set_color(self, color_value: Any) -> None:
        if isinstance(color_value, tuple) and len(color_value) in {3, 4}:
            r_raw, g_raw, b_raw = (float(c) for c in color_value[:3])
            a_raw = float(color_value[3]) if len(color_value) == 4 else 1.0
            if max(r_raw, g_raw, b_raw, a_raw) > 1.0:
                r_raw /= 255.0
                g_raw /= 255.0
                b_raw /= 255.0
                if a_raw > 1.0:
                    a_raw /= 255.0
            r = max(0.0, min(1.0, r_raw))
            g = max(0.0, min(1.0, g_raw))
            b = max(0.0, min(1.0, b_raw))
            a = max(0.0, min(1.0, a_raw))
            self._color = QColor.fromRgbF(r, g, b, a)
        elif isinstance(color_value, QColor):
            self._color = QColor(color_value)
        self._update_style()

    def _choose_color(self) -> None:
        from loomstroke.ui.widgets.color_picker_popup import ColorPickerPopup

        color = ColorPickerPopup.pick_color(self._color, self.window())
        if color is not None and color.isValid():
            self._color = color
            self._update_style()
            self.color_changed.emit(self.color())

    def _update_style(self) -> None:
        text_color = "#000" if self._color.lightness() > 128 else "#fff"
        self.setText(self._color.name())
        self.setStyleSheet(f"background-color: {self._color.name()}; color: {text_color};")


class _ActionBinding:
    def __init__(self, *, widget: QWidget, enabled_when: str | None) -> None:
        self.widget = widget
        self.enabled_when = enabled_when


class _ActionGroupLayout:
    def __init__(
        self,
        *,
        group: QWidget,
        layout: QGridLayout,
        widgets: list[QWidget],
    ) -> None:
        self.group = group
        self.layout = layout
        self.widgets = widgets


class _PropertyBinding:
    def __init__(
        self,
        *,
        setter: Callable[[Any], None],
        reader: Callable[[], Any],
        widget: QWidget,
    ) -> None:
        self.setter = setter
        self.reader = reader
        self.widget = widget


def _safe_disconnect(signal: Any, slot: Callable[..., None]) -> None:
    with suppress(RuntimeError, TypeError):  # pragma: no cover - defensive
        signal.disconnect(slot)
