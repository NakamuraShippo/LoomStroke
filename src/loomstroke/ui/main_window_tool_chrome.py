"""Toolbar and floating tool chrome coordination for MainWindow."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QSize, Qt, QTimer
from PySide6.QtWidgets import QToolBar

from ._dock_safe import is_valid
from .panels.tool_panel import ToolPropertiesPanel


class MainWindowToolChromeCoordinator:
    """Coordinate fixed toolbars and floating tool toolbar state."""

    def __init__(
        self,
        window: Any,
        *,
        floating_toolbar_state_key: str,
    ) -> None:
        self._window = window
        self._floating_toolbar_state_key = floating_toolbar_state_key

    def initialize_state(self) -> None:
        window = self._window
        window._toolbars = {}
        window._toolbar_actions = {}
        window._tool_list_panel = None
        window._floating_tool_toolbar = None
        window._tool_properties_panel = None

    def sync_floating_chrome_positions(self) -> None:
        window = self._window
        frame = getattr(window, "_floating_tool_toolbar", None)
        sync_to_parent = getattr(frame, "sync_to_parent_position", None)
        if callable(sync_to_parent):
            sync_to_parent()
        manager = getattr(window, "_context_toolbar_manager", None)
        update_position = getattr(manager, "update_position", None)
        if callable(update_position):
            update_position()

    def setup_toolbars(self) -> None:
        """Set up toolbars."""
        window = self._window
        toolbar_icon_size = QSize(48, 48)
        self._setup_properties_toolbar(toolbar_icon_size)
        self._setup_main_toolbar(toolbar_icon_size)
        self._setup_tools_toolbar(toolbar_icon_size)
        if window._window_menu is not None:
            window._build_window_menu()

    def _setup_properties_toolbar(self, _toolbar_icon_size: QSize) -> None:
        window = self._window
        properties_title = "Tool Properties"
        properties_toolbar = QToolBar(window._ts(properties_title), window)
        properties_toolbar.setObjectName("tool_properties_bar")
        properties_toolbar.setMovable(False)
        properties_toolbar.setFloatable(False)
        properties_toolbar.setAllowedAreas(Qt.ToolBarArea.TopToolBarArea)
        properties_toolbar.setContentsMargins(0, 0, 0, 0)
        properties_toolbar.setStyleSheet("QToolBar { padding: 0px; }")
        properties_toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        properties_toolbar.setFixedHeight(28)
        window._translation_binder.register(properties_toolbar.setWindowTitle, properties_title)
        tool_properties_widget = ToolPropertiesPanel(window.host, layout_mode="bar")
        tool_properties_widget.setFixedHeight(28)
        window._tool_properties_panel = tool_properties_widget
        if hasattr(tool_properties_widget, "set_host_api"):
            tool_properties_widget.set_host_api(window.host)
        properties_toolbar.addWidget(tool_properties_widget)
        window.addToolBar(Qt.ToolBarArea.TopToolBarArea, properties_toolbar)
        window.addToolBarBreak(Qt.ToolBarArea.TopToolBarArea)
        window._toolbars["properties"] = properties_toolbar

    def _setup_main_toolbar(self, toolbar_icon_size: QSize) -> None:
        window = self._window
        main_title = "Main"
        main_toolbar = QToolBar(window._ts(main_title), window)
        main_toolbar.setObjectName("main_toolbar")
        main_toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        main_toolbar.setIconSize(toolbar_icon_size)
        window._translation_binder.register(main_toolbar.setWindowTitle, main_title)
        window.addToolBar(main_toolbar)
        window._toolbars["main"] = main_toolbar

    def _setup_tools_toolbar(self, toolbar_icon_size: QSize) -> None:
        window = self._window
        tools_title = "Tools"
        tool_toolbar = QToolBar(window._ts(tools_title), window)
        tool_toolbar.setObjectName("tool_toolbar")
        tool_toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        tool_toolbar.setIconSize(toolbar_icon_size)
        window._translation_binder.register(tool_toolbar.setWindowTitle, tools_title)
        window.addToolBar(tool_toolbar)
        window._toolbars["tools"] = tool_toolbar

    def setup_statusbar(self) -> None:
        """Set up status bar."""
        statusbar = self._window.statusBar()
        statusbar.showMessage("Ready")

    def setup_docking(self) -> None:
        """Set up docking areas."""
        window = self._window
        window.setCorner(Qt.Corner.TopLeftCorner, Qt.DockWidgetArea.LeftDockWidgetArea)
        window.setCorner(Qt.Corner.TopRightCorner, Qt.DockWidgetArea.RightDockWidgetArea)
        window.setCorner(Qt.Corner.BottomLeftCorner, Qt.DockWidgetArea.LeftDockWidgetArea)
        window.setCorner(Qt.Corner.BottomRightCorner, Qt.DockWidgetArea.RightDockWidgetArea)
        window._apply_dock_separator_style()

    def raise_visible_floating_tool_toolbar(self) -> None:
        window = self._window
        frame = window._floating_tool_toolbar
        if frame is not None and not frame.isHidden():
            frame.raise_()
            window._request_native_canvas_refresh_deferred()

    def request_native_canvas_refresh(
        self,
        *,
        force_full_recompose: bool = False,
    ) -> None:
        canvas = getattr(self._window, "canvas", None)
        if isinstance(canvas, QObject) and not is_valid(canvas):
            return
        refresh = getattr(canvas, "request_native_surface_refresh", None)
        if callable(refresh):
            refresh(force_full_recompose=force_full_recompose)
            return
        update = getattr(canvas, "update", None)
        if callable(update):
            update()

    def request_native_canvas_refresh_deferred(
        self,
        *,
        force_full_recompose: bool = False,
    ) -> None:
        window = self._window
        window._request_native_canvas_refresh(force_full_recompose=force_full_recompose)

        def _refresh() -> None:
            if not is_valid(window):
                return
            window._request_native_canvas_refresh(force_full_recompose=force_full_recompose)

        QTimer.singleShot(0, _refresh)
        QTimer.singleShot(16, _refresh)

    def show_floating_tool_toolbar_at_default(self) -> None:
        window = self._window
        frame = window._floating_tool_toolbar
        if frame is None:
            return
        if frame.isHidden():
            if not self.restore_floating_tool_toolbar_state():
                frame.show_at_default_position()
            else:
                frame.show()
                frame.raise_()
        else:
            frame.show()
            frame.raise_()
        window._sync_action_from_toolbar("tools", True)
        window._request_native_canvas_refresh_deferred()

    def restore_initial_floating_tool_toolbar_state(self) -> None:
        window = self._window
        frame = window._floating_tool_toolbar
        if frame is None:
            return
        state = self.load_floating_tool_toolbar_state()
        if state is not None:
            restored = self.restore_floating_tool_toolbar_state_from(state)
            if not bool(state.get("visible", True)):
                if restored:
                    frame.hide()
                else:
                    frame.show_at_default_position()
                    frame.hide()
                window._sync_action_from_toolbar("tools", False)
                window._request_native_canvas_refresh_deferred()
                return
            if restored:
                frame.show()
                frame.raise_()
                window._sync_action_from_toolbar("tools", True)
                window._request_native_canvas_refresh_deferred()
                return
        self.show_floating_tool_toolbar_at_default()

    def save_floating_tool_toolbar_state(self) -> None:
        from loomstroke.platform.launch_options import ai_features_enabled  # noqa: PLC0415

        if not ai_features_enabled():
            return
        frame = getattr(self._window, "_floating_tool_toolbar", None)
        if frame is None:
            return
        snapshot_state = getattr(frame, "snapshot_state", None)
        if not callable(snapshot_state):
            return
        settings = getattr(self._window, "_settings", None)
        setter = getattr(settings, "set", None)
        if callable(setter):
            setter(self._floating_toolbar_state_key, snapshot_state())

    def restore_floating_tool_toolbar_state(self) -> bool:
        state = self.load_floating_tool_toolbar_state()
        return self.restore_floating_tool_toolbar_state_from(state) if state is not None else False

    def load_floating_tool_toolbar_state(self) -> dict[str, Any] | None:
        settings = getattr(self._window, "_settings", None)
        getter = getattr(settings, "get", None)
        if not callable(getter):
            return None
        raw = getter(self._floating_toolbar_state_key, {})
        return raw if isinstance(raw, dict) else None

    def restore_floating_tool_toolbar_state_from(self, state: dict[str, Any]) -> bool:
        frame = getattr(self._window, "_floating_tool_toolbar", None)
        if frame is None:
            return False
        restore_state = getattr(frame, "restore_state", None)
        return bool(callable(restore_state) and restore_state(state))
