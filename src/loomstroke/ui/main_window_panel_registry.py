"""Panel registration and UI-panel plugin sync for MainWindow."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from typing import Any, cast

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDockWidget, QMessageBox, QWidget

from ..plugins.sdk.base import PluginType
from .panel_ids import canonical_panel_id, panel_title_default, special_panel_note
from .translations import translate_with_fallback, translated_literal_domain

logger = logging.getLogger(__name__)

_DOCK_DEFAULT_AREA_PROP = "sr_default_area"
_DOCK_DEFAULT_VISIBLE_PROP = "sr_default_visible"


class MainWindowPanelVisibilityCoordinator:
    """Synchronize panel dock visibility and Window menu actions."""

    def __init__(self, window: Any) -> None:
        self._window = window

    def set_panel_visible(self, panel_id: str, visible: bool) -> bool:
        """Set visibility for a registered panel."""
        dock = self._window._panel_docks.get(canonical_panel_id(panel_id))
        if dock is None:
            return False
        if visible:
            self._window._ensure_dock_attached_for_show(dock)
        dock.setVisible(bool(visible))
        return True

    def toggle_dock_from_menu(self, panel_id: str, on: bool) -> None:
        """Toggle dock visibility from menu action."""
        panel_id = canonical_panel_id(panel_id)
        dock = self._window._panel_docks.get(panel_id)
        if not dock:
            return
        if on and not dock.isFloating():
            self._window._ensure_dock_attached_for_show(dock)
            cur_area = self._window.dockWidgetArea(dock)
            if cur_area != Qt.DockWidgetArea.NoDockWidgetArea and not dock.isVisible():
                self._window.addDockWidget(cur_area, dock)
        dock.setVisible(on)

    def sync_action_from_dock(self, panel_id: str, visible: bool) -> None:
        """Sync menu action state from dock visibility change."""
        panel_id = canonical_panel_id(panel_id)
        act = self._window._panel_actions.get(panel_id)
        if act and act.isChecked() != visible:
            old = act.blockSignals(True)
            act.setChecked(visible)
            act.blockSignals(old)


class MainWindowPanelPluginSetupCoordinator:
    """Collect plugin panel descriptors and register their widgets."""

    def __init__(self, window: Any, registry: Any) -> None:
        self._window = window
        self._registry = registry

    def sync_ui_panel_plugins(self) -> None:
        registry = self._window._plugin_registry
        if registry is None:
            return
        for plugin in registry.by_type(PluginType.UI_PANEL):
            self.sync_ui_panel_plugin(plugin)

    def sync_ui_panel_plugin(self, plugin: Any) -> bool:
        metadata = getattr(plugin, "metadata", None)
        owner_plugin_id = str(getattr(metadata, "id", plugin.__class__.__name__))
        if not self._window._ensure_plugin_activated(plugin):
            logger.warning(
                "failed to sync panel panel_id=<unknown> owner=%s reason=activate_failed",
                owner_plugin_id,
            )
            return False

        if owner_plugin_id == "comfyui_ui_panel":
            logger.info(
                "Keeping ComfyUI backend plugin active while omitting its legacy panel; "
                "use the AI Effects Manager ComfyUI tab"
            )
            return True

        descriptor, create_widget, panel_id = self.collect_panel_descriptor(
            plugin,
            owner_plugin_id,
        )
        if (
            descriptor is None
            or create_widget is None
            or not isinstance(panel_id, str)
            or not panel_id
        ):
            return False

        if self._registry.has_panel(panel_id):
            logger.warning(
                "duplicate panel_id detected, skip sync panel_id=%s owner=%s",
                panel_id,
                owner_plugin_id,
            )
            return True

        if self._registry.owner_has_panels(owner_plugin_id):
            logger.debug(
                "owner already has registered panel(s), skip legacy duplicate owner=%s panel_id=%s",
                owner_plugin_id,
                panel_id,
            )
            return True

        return self.register_plugin_panel(
            descriptor,
            create_widget,
            plugin,
            panel_id,
            owner_plugin_id,
        )

    def collect_panel_descriptor(
        self,
        plugin: Any,
        owner_plugin_id: str,
    ) -> tuple[Any | None, Callable[..., Any] | None, Any]:
        descriptor_getter = getattr(plugin, "descriptor", None)
        create_widget = getattr(plugin, "create_widget", None)
        if not callable(descriptor_getter) or not callable(create_widget):
            logger.warning(
                (
                    "failed to sync panel panel_id=<unknown> owner=%s "
                    "reason=missing_descriptor_or_widget_factory"
                ),
                owner_plugin_id,
            )
            return None, None, None

        try:
            descriptor = descriptor_getter()
        except Exception as exc:
            logger.error(
                "failed to sync panel panel_id=<unknown> owner=%s reason=descriptor_error:%s",
                owner_plugin_id,
                exc,
                exc_info=True,
            )
            return None, None, None

        panel_id = getattr(descriptor, "id", None)
        if not isinstance(panel_id, str) or not panel_id:
            logger.warning(
                "failed to sync panel panel_id=<unknown> owner=%s reason=invalid_panel_id",
                owner_plugin_id,
            )
            return None, None, None

        return descriptor, create_widget, panel_id

    def collect_default_size(self, descriptor: Any) -> tuple[int, int] | None:
        return cast(
            tuple[int, int] | None,
            self._registry._default_size_from_value(getattr(descriptor, "default_size", None)),
        )

    def register_plugin_panel(
        self,
        descriptor: Any,
        create_widget: Callable[[Any], QWidget],
        plugin: Any,
        panel_id: str,
        owner_plugin_id: str,
        *,
        area_override: str | Qt.DockWidgetArea | None = None,
        title_context: str | None = None,
        use_descriptor_default_size: bool = True,
    ) -> bool:
        try:
            widget = create_widget(self._window)
        except Exception as exc:
            logger.error(
                "failed to sync panel panel_id=%s owner=%s reason=create_widget_error:%s",
                panel_id,
                owner_plugin_id,
                exc,
                exc_info=True,
            )
            return False

        display_name = getattr(descriptor, "display_name", panel_id)
        area = area_override or getattr(descriptor, "default_position", "right")

        registered = self._registry.register_dynamic_panel(
            panel_id,
            display_name,
            widget,
            area=area,
            default_visible=True,
            owner_plugin_id=owner_plugin_id,
            closable=bool(getattr(descriptor, "closable", True)),
            resizable=bool(getattr(descriptor, "resizable", True)),
            floating=bool(getattr(descriptor, "floating", False)),
            default_size=(
                self.collect_default_size(descriptor) if use_descriptor_default_size else None
            ),
            title_context=title_context,
        )
        if not registered:
            logger.warning(
                "failed to sync panel panel_id=%s owner=%s reason=register_failed",
                panel_id,
                owner_plugin_id,
            )
            return False

        shown_cb = getattr(plugin, "on_panel_shown", None)
        if callable(shown_cb):
            with suppress(Exception):
                shown_cb()
        logger.info("synced panel panel_id=%s owner=%s", panel_id, owner_plugin_id)
        return True

    def setup_ui_panel_plugin_if_available(
        self,
        plugin_id: str,
        *,
        area_override: str | Qt.DockWidgetArea | None = None,
        title_context: str | None = None,
        use_descriptor_default_size: bool = False,
        before_register: Callable[[], None] | None = None,
        after_register: Callable[[], None] | None = None,
        success_message: str | None = None,
    ) -> bool:
        registry = self._window._plugin_registry
        if registry is None:
            return False
        plugin = registry.get(plugin_id)
        if plugin is None:
            return False

        return self.setup_ui_panel_plugin(
            plugin,
            plugin_id,
            area_override=area_override,
            title_context=title_context,
            use_descriptor_default_size=use_descriptor_default_size,
            before_register=before_register,
            after_register=after_register,
            success_message=success_message,
        )

    def setup_ui_panel_plugin(
        self,
        plugin: Any,
        owner_plugin_id: str,
        *,
        area_override: str | Qt.DockWidgetArea | None = None,
        title_context: str | None = None,
        use_descriptor_default_size: bool = False,
        before_register: Callable[[], None] | None = None,
        after_register: Callable[[], None] | None = None,
        success_message: str | None = None,
    ) -> bool:
        descriptor, create_widget, panel_id = self.collect_panel_descriptor(
            plugin,
            owner_plugin_id,
        )
        if (
            descriptor is None
            or create_widget is None
            or not isinstance(panel_id, str)
            or not panel_id
        ):
            return False
        if self._registry.has_panel(panel_id) or self._registry.owner_has_panels(owner_plugin_id):
            return True
        if not self._window._ensure_plugin_activated(plugin):
            logger.warning(
                "failed to setup panel panel_id=%s owner=%s reason=activate_failed",
                panel_id,
                owner_plugin_id,
            )
            return False

        if before_register is not None:
            before_register()
        registered = self.register_plugin_panel(
            descriptor,
            create_widget,
            plugin,
            panel_id,
            owner_plugin_id,
            area_override=area_override,
            title_context=title_context,
            use_descriptor_default_size=use_descriptor_default_size,
        )
        if registered:
            if after_register is not None:
                after_register()
            if success_message:
                logger.info(success_message)
        return registered

    def unsync_ui_panel_plugin(self, plugin_id: str) -> int:
        hidden_cb = None
        registry = self._window._plugin_registry
        if registry is not None:
            plugin = registry.get(plugin_id)
            if plugin is not None:
                hidden_cb = getattr(plugin, "on_panel_hidden", None)
        removed = self._registry.unregister_panels_by_owner(plugin_id)
        if removed > 0 and callable(hidden_cb):
            with suppress(Exception):
                hidden_cb()
        return int(removed)


class MainWindowPanelRegistryCoordinator:
    """Coordinate registered panel state while keeping MainWindow as facade."""

    def __init__(self, window: Any) -> None:
        self._window = window
        self._visibility = MainWindowPanelVisibilityCoordinator(window)
        self._plugin_setup = MainWindowPanelPluginSetupCoordinator(window, self)

    def initialize_state(self) -> None:
        window = self._window
        window._panels = {}
        window._panel_toggles = {}
        window._panel_actions = {}
        window._panel_docks = {}
        window._panel_titles = {}
        window._panel_title_sources = {}
        window._panel_title_contexts = {}
        window._panel_title_source_defaults = dict(window._PANEL_TITLE_SOURCE_DEFAULTS)
        window._panel_owners = {}
        window._owner_panels = {}

    def on_panel_register(self, payload: Mapping[str, Any]) -> None:
        """Handle panel registration via EventBus."""
        pid = payload.get("id")
        title = payload.get("title", pid)
        title_context = payload.get("title_context")
        dock = payload.get("dock")
        widget = payload.get("widget", dock)
        area = payload.get("area", "right")
        dv = payload.get("default_visible", True)
        owner_plugin_id = payload.get("owner_plugin_id")
        closable = bool(payload.get("closable", True))
        resizable = bool(payload.get("resizable", True))
        floating = bool(payload.get("floating", False))
        default_size = self._default_size_from_value(payload.get("default_size"))
        if isinstance(pid, str):
            self.register_dynamic_panel(
                pid,
                title,
                widget,
                area=area,
                default_visible=bool(dv),
                owner_plugin_id=owner_plugin_id if isinstance(owner_plugin_id, str) else None,
                closable=closable,
                resizable=resizable,
                floating=floating,
                default_size=default_size,
                title_context=title_context if isinstance(title_context, str) else None,
            )

    def on_panel_toggle(self, payload: Mapping[str, Any]) -> None:
        """Handle panel toggle via EventBus."""
        pid = payload.get("id")
        on = bool(payload.get("on", True))
        if isinstance(pid, str):
            panel_id = canonical_panel_id(pid)
            action = self._window._panel_actions.get(panel_id)
            if action is not None:
                action.setChecked(on)

    def on_panel_rename(self, payload: Mapping[str, Any]) -> None:
        """Handle panel rename via EventBus."""
        pid = payload.get("id")
        title = payload.get("title")
        title_context = payload.get("title_context")
        if isinstance(pid, str) and isinstance(title, str):
            panel_id = canonical_panel_id(pid)
            title_key, resolved_context = self.resolve_panel_title_source(
                panel_id,
                title,
                title_context if isinstance(title_context, str) else None,
            )
            self._window._panel_title_sources[panel_id] = title_key
            self._window._panel_title_contexts[panel_id] = resolved_context
            translated_title = self.translate_panel_title(title_key, resolved_context)
            self._window._panel_titles[panel_id] = translated_title
            if panel_id in self._window._panel_actions:
                self._window._panel_actions[panel_id].setText(translated_title)
            if panel_id in self._window._panel_docks:
                self._window._panel_docks[panel_id].setWindowTitle(translated_title)

    def resolve_panel_title_source(
        self,
        panel_id: str,
        title: Any,
        context: str | None = None,
    ) -> tuple[str, str]:
        source_text = getattr(title, "source_text", None)
        title_domain = getattr(title, "translation_domain", None)
        title_key = source_text if isinstance(source_text, str) and source_text else str(title)
        if isinstance(context, str) and context:
            return title_key, context
        if isinstance(title_domain, str) and title_domain:
            return title_key, title_domain
        canonical_id = canonical_panel_id(panel_id)
        default = self._window._panel_title_source_defaults.get(
            canonical_id
        ) or panel_title_default(panel_id)
        if default is not None and title_key == default[0]:
            return default
        return title_key, "MainWindow"

    def translate_panel_title(self, source: str, context: str) -> str:
        return translate_with_fallback(context, source)

    def refresh_panel_titles(self) -> None:
        for panel_id, title_key in self._window._panel_title_sources.items():
            context = self._window._panel_title_contexts.get(panel_id, "MainWindow")
            translated_title = self.translate_panel_title(title_key, context)
            if panel_id in self._window._panel_docks:
                self._window._panel_docks[panel_id].setWindowTitle(translated_title)
            action = self._window._panel_actions.get(panel_id)
            if action is not None:
                action.setText(translated_title)
            self._window._panel_titles[panel_id] = translated_title

    def bind_panel_owner(self, panel_id: str, owner_plugin_id: str | None) -> None:
        panel_id = canonical_panel_id(panel_id)
        self.unbind_panel_owner(panel_id)
        if not owner_plugin_id:
            return
        self._window._panel_owners[panel_id] = owner_plugin_id
        owner_panels = self._window._owner_panels.setdefault(owner_plugin_id, set())
        owner_panels.add(panel_id)

    def unbind_panel_owner(self, panel_id: str) -> None:
        panel_id = canonical_panel_id(panel_id)
        owner = self._window._panel_owners.pop(panel_id, None)
        if owner is None:
            return
        owner_panels = self._window._owner_panels.get(owner)
        if not owner_panels:
            return
        owner_panels.discard(panel_id)
        if not owner_panels:
            self._window._owner_panels.pop(owner, None)

    def owner_has_panels(self, owner_plugin_id: str) -> bool:
        owned = self._window._owner_panels.get(owner_plugin_id)
        return bool(owned)

    def has_panel(self, panel_id: str) -> bool:
        """Return whether the panel is currently registered."""
        return canonical_panel_id(panel_id) in self._window._panel_docks

    def set_panel_visible(self, panel_id: str, visible: bool) -> bool:
        """Set visibility for a registered panel."""
        return self._visibility.set_panel_visible(panel_id, visible)

    def unregister_panel(self, panel_id: str) -> bool:
        """Unregister a panel and remove its menu action + dock."""
        panel_id = canonical_panel_id(panel_id)
        dock = self._window._panel_docks.get(panel_id)
        if dock is None:
            return False

        action = self._window._panel_actions.pop(panel_id, None)
        if action is not None and self._window._window_menu is not None:
            with suppress(Exception):
                self._window._window_menu.removeAction(action)
        if action is not None:
            with suppress(Exception):
                action.deleteLater()

        self._window._panel_docks.pop(panel_id, None)
        self._window._panel_titles.pop(panel_id, None)
        self._window._panel_title_sources.pop(panel_id, None)
        self._window._panel_title_contexts.pop(panel_id, None)
        self._window._panels.pop(panel_id, None)
        self._window._panel_toggles.pop(panel_id, None)
        self.unbind_panel_owner(panel_id)

        if self._window._comfyui_dock is dock:
            self._window._comfyui_dock = None
            self._window._comfyui_panel_widget = None

        with suppress(Exception):
            self._window.removeDockWidget(dock)
        with suppress(Exception):
            dock.hide()
            dock.setParent(None)
            dock.deleteLater()

        self._window._build_window_menu()
        logger.info("Unregistered panel: %s", panel_id)
        return True

    def unregister_panels_by_owner(self, owner_plugin_id: str) -> int:
        """Unregister all panels owned by a plugin."""
        panel_ids = sorted(self._window._owner_panels.get(owner_plugin_id, set()))
        removed = 0
        for panel_id in panel_ids:
            if self.unregister_panel(panel_id):
                removed += 1
        logger.info("unsynced panels owner=%s count=%d", owner_plugin_id, removed)
        return removed

    def panel_area_from_descriptor(self, position: str) -> Qt.DockWidgetArea:
        normalized = position.strip().lower()
        mapping = {
            "left": Qt.DockWidgetArea.LeftDockWidgetArea,
            "right": Qt.DockWidgetArea.RightDockWidgetArea,
            "top": Qt.DockWidgetArea.TopDockWidgetArea,
            "bottom": Qt.DockWidgetArea.BottomDockWidgetArea,
            "floating": Qt.DockWidgetArea.RightDockWidgetArea,
        }
        return mapping.get(normalized, Qt.DockWidgetArea.RightDockWidgetArea)

    def add_panel(
        self,
        panel_id: str,
        widget: QWidget,
        title: str,
        area: Qt.DockWidgetArea = Qt.DockWidgetArea.LeftDockWidgetArea,
    ) -> QDockWidget:
        """Add a legacy dockable panel and register it with the Window menu."""
        if panel_id in self._window._panels:
            logger.warning("Panel %s already exists", panel_id)
            return cast(QDockWidget, self._window._panels[panel_id])

        dock = QDockWidget(title, self._window)
        dock.setObjectName(panel_id)
        dock.setWidget(widget)

        self._window.addDockWidget(area, dock)
        self._window._panels[panel_id] = dock

        self.register_panel(panel_id, title, dock, default_visible=True)
        dock.visibilityChanged.connect(
            lambda visible: self._window.panel_toggled.emit(panel_id, visible)
        )

        logger.info("Added panel: %s", panel_id)
        return dock

    def register_dynamic_panel(
        self,
        panel_id: str,
        title: Any,
        widget: Any,
        *,
        area: str | Qt.DockWidgetArea = "right",
        default_visible: bool = True,
        owner_plugin_id: str | None = None,
        closable: bool = True,
        resizable: bool = True,
        floating: bool = False,
        default_size: tuple[int, int] | None = None,
        title_context: str | None = None,
    ) -> bool:
        """Register a panel from plugin metadata in a uniform way."""
        if not panel_id:
            logger.warning("register_dynamic_panel rejected: empty panel_id")
            return False
        canonical_id = canonical_panel_id(panel_id)
        if self.has_panel(canonical_id):
            logger.warning("Duplicate panel_id detected, skipping registration: %s", panel_id)
            return False

        title_key, resolved_context = self.resolve_panel_title_source(
            canonical_id,
            title,
            title_context,
        )
        if isinstance(area, Qt.DockWidgetArea):
            dock_area = area
        else:
            dock_area = self.panel_area_from_descriptor(str(area))
        translated_title = self.translate_panel_title(title_key, resolved_context)
        dock = self.build_dynamic_dock(canonical_id, translated_title, widget)
        if dock is None:
            return False

        features = (
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        if closable:
            features |= QDockWidget.DockWidgetFeature.DockWidgetClosable
        dock.setFeatures(features)

        self._window.addDockWidget(dock_area, dock)

        if default_size is not None:
            try:
                dock.resize(int(default_size[0]), int(default_size[1]))
            except Exception:
                logger.debug("Invalid default_size for panel %s: %s", canonical_id, default_size)

        if not resizable and default_size is not None:
            with suppress(Exception):
                dock.setMinimumSize(int(default_size[0]), int(default_size[1]))
                dock.setMaximumSize(int(default_size[0]), int(default_size[1]))

        self._window._panels[canonical_id] = dock
        self.register_panel(
            canonical_id,
            title_key,
            dock,
            default_visible=default_visible,
            owner_plugin_id=owner_plugin_id,
            title_context=resolved_context,
        )

        if floating:
            with suppress(Exception):
                dock.setFloating(True)
                dock.raise_()

        return True

    def build_dynamic_dock(self, panel_id: str, title: str, widget: Any) -> QDockWidget | None:
        if isinstance(widget, QDockWidget):
            dock = widget
            if dock.parent() is not self._window:
                dock.setParent(self._window)
            if dock.widget() is None:
                logger.warning("register_dynamic_panel rejected: dock has no widget (%s)", panel_id)
                return None
            return dock
        if isinstance(widget, QWidget):
            dock = QDockWidget(title, self._window)
            dock.setWidget(widget)
            return dock
        logger.warning(
            "register_dynamic_panel rejected: panel widget is not QWidget (%s)",
            panel_id,
        )
        return None

    def register_panel(
        self,
        panel_id: str,
        title: str,
        dock: QDockWidget,
        *,
        default_visible: bool = True,
        owner_plugin_id: str | None = None,
        title_context: str | None = None,
    ) -> None:
        """
        Register a panel (QDockWidget) with Window menu for individual toggle.

        Args:
            panel_id: Unique panel ID
            title: Menu display name
            dock: The QDockWidget to register
            default_visible: Initial visibility state
        """
        panel_id = canonical_panel_id(panel_id)
        existing_dock = self._window._panel_docks.get(panel_id)
        if existing_dock is not None and existing_dock is not dock:
            logger.warning("Panel %s is already registered; skipping duplicate", panel_id)
            return

        self._window._panel_docks[panel_id] = dock
        title_key, resolved_context = self.resolve_panel_title_source(
            panel_id,
            title,
            title_context,
        )
        self._window._panel_title_sources[panel_id] = title_key
        self._window._panel_title_contexts[panel_id] = resolved_context
        translated_title = self.translate_panel_title(title_key, resolved_context)
        self._window._panel_titles[panel_id] = translated_title
        self.bind_panel_owner(panel_id, owner_plugin_id)
        dock.setObjectName(panel_id)
        dock.setWindowTitle(translated_title)
        default_area = self._window.dockWidgetArea(dock)
        if default_area != Qt.DockWidgetArea.NoDockWidgetArea:
            dock.setProperty(_DOCK_DEFAULT_AREA_PROP, int(cast("Any", default_area).value))
        dock.setProperty(_DOCK_DEFAULT_VISIBLE_PROP, bool(default_visible))
        self._window._ensure_dock_tracking(dock)
        self._window._apply_panel_outline(dock)
        self._window._apply_panel_min_size(dock)
        content = dock.widget()
        if content is not None:
            content.setAutoFillBackground(True)

        dock.visibilityChanged.connect(
            lambda vis, pid=panel_id: self._window._sync_action_from_dock(pid, vis)
        )

        if self._window._window_menu is None:
            self._window._build_window_menu()

        act = self._window._panel_actions.get(panel_id)
        action_title = translated_literal_domain(
            resolved_context,
            title_key,
            use_fallback=True,
        )
        if act is None:
            act = self._window._mk_action(
                action_title,
                None,
                None,
                checkable=True,
                checked=default_visible,
            )
            act.toggled.connect(
                lambda on, pid=panel_id: self._window._toggle_dock_from_menu(pid, on)
            )
            self._window._panel_actions[panel_id] = act
            if self._window._window_menu is not None:
                self._window._window_menu.addAction(act)
        else:
            act.setText(translated_title)

        dock.setVisible(default_visible)
        act.setChecked(default_visible)

        logger.info(
            "Registered panel: %s - '%s' (visible: %s, menu: %s)",
            panel_id,
            translated_title,
            default_visible,
            self._window._window_menu is not None,
        )
        self._window._build_window_menu()
        reapply_pending_tabs = getattr(
            self._window,
            "_reapply_pending_layout_tab_groups",
            None,
        )
        if callable(reapply_pending_tabs):
            reapply_pending_tabs()

    def iter_panels(self) -> Iterable[tuple[str, QDockWidget]]:
        """Return an iterable of registered panel dock widgets."""
        return tuple(self._window._panel_docks.items())

    def get_panel_dock(self, panel_id: str) -> QDockWidget | None:
        """Return the dock widget associated with a panel identifier."""
        return cast(QDockWidget | None, self._window._panel_docks.get(canonical_panel_id(panel_id)))

    def sync_ui_panel_plugins(self) -> None:
        self._plugin_setup.sync_ui_panel_plugins()

    def sync_ui_panel_plugin(self, plugin: Any) -> bool:
        return self._plugin_setup.sync_ui_panel_plugin(plugin)

    def collect_panel_descriptor(
        self,
        plugin: Any,
        owner_plugin_id: str,
    ) -> tuple[Any | None, Callable[..., Any] | None, Any]:
        return self._plugin_setup.collect_panel_descriptor(plugin, owner_plugin_id)

    def collect_default_size(self, descriptor: Any) -> tuple[int, int] | None:
        return self._plugin_setup.collect_default_size(descriptor)

    def register_plugin_panel(
        self,
        descriptor: Any,
        create_widget: Callable[[Any], QWidget],
        plugin: Any,
        panel_id: str,
        owner_plugin_id: str,
        *,
        area_override: str | Qt.DockWidgetArea | None = None,
        title_context: str | None = None,
        use_descriptor_default_size: bool = True,
    ) -> bool:
        return self._plugin_setup.register_plugin_panel(
            descriptor,
            create_widget,
            plugin,
            panel_id,
            owner_plugin_id,
            area_override=area_override,
            title_context=title_context,
            use_descriptor_default_size=use_descriptor_default_size,
        )

    def setup_ui_panel_plugin_if_available(
        self,
        plugin_id: str,
        *,
        area_override: str | Qt.DockWidgetArea | None = None,
        title_context: str | None = None,
        use_descriptor_default_size: bool = False,
        before_register: Callable[[], None] | None = None,
        after_register: Callable[[], None] | None = None,
        success_message: str | None = None,
    ) -> bool:
        return self._plugin_setup.setup_ui_panel_plugin_if_available(
            plugin_id,
            area_override=area_override,
            title_context=title_context,
            use_descriptor_default_size=use_descriptor_default_size,
            before_register=before_register,
            after_register=after_register,
            success_message=success_message,
        )

    def setup_ui_panel_plugin(
        self,
        plugin: Any,
        owner_plugin_id: str,
        *,
        area_override: str | Qt.DockWidgetArea | None = None,
        title_context: str | None = None,
        use_descriptor_default_size: bool = False,
        before_register: Callable[[], None] | None = None,
        after_register: Callable[[], None] | None = None,
        success_message: str | None = None,
    ) -> bool:
        return self._plugin_setup.setup_ui_panel_plugin(
            plugin,
            owner_plugin_id,
            area_override=area_override,
            title_context=title_context,
            use_descriptor_default_size=use_descriptor_default_size,
            before_register=before_register,
            after_register=after_register,
            success_message=success_message,
        )

    def unsync_ui_panel_plugin(self, plugin_id: str) -> int:
        return self._plugin_setup.unsync_ui_panel_plugin(plugin_id)

    def toggle_dock_from_menu(self, panel_id: str, on: bool) -> None:
        """Toggle dock visibility from menu action."""
        self._visibility.toggle_dock_from_menu(panel_id, on)

    def sync_action_from_dock(self, panel_id: str, visible: bool) -> None:
        """Sync menu action state from dock visibility change."""
        self._visibility.sync_action_from_dock(panel_id, visible)

    def run_panel_audit(self) -> dict[str, Any]:
        """Audit panel registration and visibility state."""
        audit_result: dict[str, Any] = {
            "registered_panels": [],
            "visible_panels": [],
            "special_panels": [],
            "issues": [],
            "canvas": {"exists": False, "type": None, "visible": False},
        }

        for panel_id, dock in self._window._panel_docks.items():
            canonical_id = canonical_panel_id(panel_id)
            panel_info = {
                "id": panel_id,
                "title": self._window._panel_titles.get(panel_id, "Unknown"),
                "dock_exists": dock is not None,
                "is_visible": dock.isVisible() if dock else False,
                "has_action": panel_id in self._window._panel_actions,
            }
            audit_result["registered_panels"].append(panel_info)

            if dock and dock.isVisible():
                audit_result["visible_panels"].append(panel_id)

            note = special_panel_note(panel_id)
            if note is not None:
                audit_result["special_panels"].append(f"{panel_id}: {note}")

            if canonical_id != panel_id:
                audit_result["issues"].append(
                    f"{panel_id}: non-canonical panel id; expected {canonical_id}"
                )
            if not dock:
                audit_result["issues"].append(f"{panel_id}: Dock widget not found")
            elif panel_id not in self._window._panel_actions:
                audit_result["issues"].append(f"{panel_id}: Window menu action missing")

        central = self._window.centralWidget()
        audit_result["canvas"] = {
            "exists": central is not None,
            "type": type(central).__name__ if central else None,
            "visible": central.isVisible() if central else False,
        }

        if central is None:
            audit_result["issues"].append("No central widget (canvas) set")

        return audit_result

    def show_panel_audit_dialog(self) -> None:
        """Show panel audit results in a dialog."""
        audit = self.run_panel_audit()
        QMessageBox.information(
            self._window,
            "Panel Audit",
            self.format_panel_audit_report(audit),
        )

    @staticmethod
    def format_panel_audit_report(audit: Mapping[str, Any]) -> str:
        msg = "Panel Audit Report\n"
        msg += "================\n\n"
        msg += f"Canvas: {'✓' if audit['canvas']['exists'] else '✗'}"
        if audit["canvas"]["exists"]:
            msg += f" ({audit['canvas']['type']})\n"
        else:
            msg += "\n"
        msg += f"Registered Panels: {len(audit['registered_panels'])}\n"
        msg += f"Visible Panels: {len(audit['visible_panels'])}\n\n"

        if audit["registered_panels"]:
            msg += "Panels:\n"
            for panel in audit["registered_panels"]:
                status = "✓" if panel["is_visible"] else "○"
                msg += f"  {status} {panel['title']} ({panel['id']})\n"

        if audit["special_panels"]:
            msg += "\nSpecial Handling:\n"
            for note in audit["special_panels"]:
                msg += f"- {note}\n"

        if audit["issues"]:
            msg += "\nIssues Found:\n"
            for issue in audit["issues"]:
                msg += f"• {issue}\n"
        else:
            msg += "\n✓ No issues found"
        return msg

    def _default_size_from_value(self, value: Any) -> tuple[int, int] | None:
        if (
            isinstance(value, (tuple, list))
            and len(value) >= 2
            and isinstance(value[0], int)
            and isinstance(value[1], int)
        ):
            return (value[0], value[1])
        return None
