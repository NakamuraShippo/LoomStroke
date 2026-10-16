"""
Host API contract for LoomStroke plugins.

This module defines the stable interface between the host (core) and plugins.
All plugins interact with the host exclusively through these interfaces.
Per Master.md specifications, plugins cannot directly access pixels.
"""

from __future__ import annotations

import contextlib
import importlib
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from packaging.specifiers import SpecifierSet
from packaging.version import Version

from . import host_api_protocols as _host_api_protocols
from .core.color_state import ColorDescriptor, ColorState, ColorStateSnapshot
from .core.events import Events, MenuVisibilityEvent
from .core.selection_clipboard import SelectionClipboard
from .host_api_color import HostAPIColorFacade
from .host_api_ui import HostAPIUIFacade

if TYPE_CHECKING:
    from .ui.i18n import TranslationDomainRegistry

logger = logging.getLogger(__name__)


def _default_color_state() -> ColorState:
    return ColorState(current=ColorDescriptor((0.0, 0.0, 0.0, 1.0)))


Point = _host_api_protocols.Point
ProgressCallback = _host_api_protocols.ProgressCallback
PanelLayout = _host_api_protocols.PanelLayout
UILayoutSnapshot = _host_api_protocols.UILayoutSnapshot
SaveOptions = _host_api_protocols.SaveOptions
LoadOptions = _host_api_protocols.LoadOptions
StrokePoint = _host_api_protocols.StrokePoint
Selection = _host_api_protocols.Selection
SelectionPayload = _host_api_protocols.SelectionPayload
LayerRef = _host_api_protocols.LayerRef
DocumentRef = _host_api_protocols.DocumentRef
PageId = _host_api_protocols.PageId
CanvasSpec = _host_api_protocols.CanvasSpec
PageSummary = _host_api_protocols.PageSummary
PageTemplateSummary = _host_api_protocols.PageTemplateSummary
ShortcutActionInfo = _host_api_protocols.ShortcutActionInfo
ShortcutSchemeValidation = _host_api_protocols.ShortcutSchemeValidation
ShortcutService = _host_api_protocols.ShortcutService
TranslationDomainLoader = _host_api_protocols.TranslationDomainLoader
CanvasService = _host_api_protocols.CanvasService
HistoryAwareCanvas = _host_api_protocols.HistoryAwareCanvas
HistoryService = _host_api_protocols.HistoryService
InputService = _host_api_protocols.InputService
SettingsService = _host_api_protocols.SettingsService
DocumentService = _host_api_protocols.DocumentService
PageService = _host_api_protocols.PageService
EventBus = _host_api_protocols.EventBus
UILayoutService = _host_api_protocols.UILayoutService
UIService = _host_api_protocols.UIService


@dataclass(frozen=True)
class HostAPI:
    """
    Main host API provided to plugins.

    This is the primary interface through which plugins interact with the host.
    All services are guaranteed to be available and stable across versions.
    """

    canvas: CanvasService
    history: HistoryService
    input: InputService
    settings: SettingsService
    events: EventBus
    document: DocumentService
    pages: PageService
    ui: UIService
    shortcuts: ShortcutService | None = None
    color_state: ColorState = field(default_factory=_default_color_state, repr=False)
    selection_clipboard: SelectionClipboard = field(default_factory=SelectionClipboard, repr=False)
    _menu_visibility_handlers: list[Callable[[Any], None]] = field(
        default_factory=list,
        init=False,
        repr=False,
        compare=False,
    )
    _color_change_handlers: list[Callable[[Any], None]] = field(
        default_factory=list,
        init=False,
        repr=False,
        compare=False,
    )
    _canvas_widget: Any = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )
    _color_state_facade: HostAPIColorFacade | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )
    _ui_facade: HostAPIUIFacade | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    @property
    def version(self) -> str:
        """Get the host API version (SemVer)."""
        return "1.0.0"

    def is_compatible(self, required_version: str) -> bool:
        """
        Check if the host API is compatible with a required version.

        Args:
            required_version: Required version string (SemVer range)

        Returns:
            True if compatible
        """

        try:
            spec = SpecifierSet(required_version)
            return Version(self.version) in spec
        except Exception:
            # Invalid specifier format - treat as incompatible
            return False

    # --- Canvas widget bridging ----------------------------------------------
    def attach_canvas_widget(self, widget: Any) -> None:
        """Attach the canvas widget for GPU readback access (pick_color_at etc.).

        Stored on a frozen dataclass via ``object.__setattr__`` so callers
        outside the bootstrap path cannot accidentally mutate it.
        """
        self._ui_facade_for().attach_canvas_widget(widget)

    # --- Shortcut bridging ----------------------------------------------------
    def attach_shortcut_service(self, service: ShortcutService) -> None:
        """Attach the shortcut service once the UI layer has initialised it."""

        object.__setattr__(self, "shortcuts", service)

    def register_shortcut_action(
        self,
        action_id: str,
        *,
        label: str,
        default: str | None = None,
        category: str = "General",
        description: str | None = None,
        owner: str | None = None,
    ) -> str | None:
        """Convenience helper for registering shortcuts via the host service."""

        if not self.shortcuts:
            raise RuntimeError("Shortcut service is not attached to HostAPI")
        return self.shortcuts.register_action(
            action_id,
            label=label,
            default=default,
            category=category,
            description=description,
            owner=owner,
        )

    def validate_shortcut_scheme(
        self,
        payload: Any,
        *,
        require_known: bool = False,
    ) -> ShortcutSchemeValidation:
        """Proxy shortcut scheme validation to the attached service."""

        if not self.shortcuts:
            raise RuntimeError("Shortcut service is not attached to HostAPI")
        return self.shortcuts.validate_scheme(payload, require_known=require_known)

    def register_translation_domain(
        self,
        name: str,
        loader: TranslationDomainLoader,
    ) -> Callable[[], None]:
        """Allow plugins to register a translation domain loader."""

        module = importlib.import_module("loomstroke.ui.i18n")
        registry = cast("TranslationDomainRegistry", module.translation_domains)
        return registry.register(name, loader)

    def _color_facade_for(self) -> HostAPIColorFacade:
        facade = self._color_state_facade
        if facade is None:
            facade = HostAPIColorFacade(self)
            object.__setattr__(self, "_color_state_facade", facade)
        return facade

    def _ui_facade_for(self) -> HostAPIUIFacade:
        facade = self._ui_facade
        if facade is None:
            facade = HostAPIUIFacade(self)
            object.__setattr__(self, "_ui_facade", facade)
        return facade

    # --- Color management ---------------------------------------------------
    def attach_color_state(self, state: ColorState) -> None:
        """Attach an external color state instance (bootstrap)."""
        self._color_facade_for().attach_color_state(state)

    def get_active_color(self) -> ColorDescriptor:
        """Return the current active color descriptor."""
        return self._color_facade_for().get_active_color()

    def get_color_snapshot(self) -> ColorStateSnapshot:
        """Return a snapshot of the current color state."""
        return self._color_facade_for().get_color_snapshot()

    def get_color_history(self, limit: int = 10) -> Sequence[ColorDescriptor]:
        """Return recent color history up to limit entries."""
        return self._color_facade_for().get_color_history(limit)

    def get_layout_snapshot(self, *, include_panels: bool = True) -> UILayoutSnapshot | None:
        """Return a captured UI layout snapshot if the UI supports it."""
        return self._ui_facade_for().get_layout_snapshot(include_panels=include_panels)

    def register_panel_widget(
        self,
        panel_id: str,
        title: str,
        widget: Any,
        *,
        area: str = "right",
        default_visible: bool = True,
        owner_plugin_id: str | None = None,
        closable: bool = True,
        resizable: bool = True,
        floating: bool = False,
        default_size: tuple[int, int] | None = None,
    ) -> bool:
        """Register a panel via the attached UI service."""
        return self._ui_facade_for().register_panel_widget(
            panel_id,
            title,
            widget,
            area=area,
            default_visible=default_visible,
            owner_plugin_id=owner_plugin_id,
            closable=closable,
            resizable=resizable,
            floating=floating,
            default_size=default_size,
        )

    def unregister_panel(self, panel_id: str) -> bool:
        """Unregister a panel via the attached UI service."""
        return self._ui_facade_for().unregister_panel(panel_id)

    def has_panel(self, panel_id: str) -> bool:
        """Return True when the UI currently has the panel registered."""
        return self._ui_facade_for().has_panel(panel_id)

    def set_panel_visible(self, panel_id: str, visible: bool) -> bool:
        """Set panel visibility via the attached UI service."""
        return self._ui_facade_for().set_panel_visible(panel_id, visible)

    def get_pixel_color(self, x: int, y: int) -> ColorDescriptor | None:
        """Sample a pixel color from the composited canvas at (*x*, *y*).

        Returns a :class:`ColorDescriptor` with normalised RGBA values, or
        ``None`` when the coordinates are out of bounds.

        Uses GPU readback from the canvas widget (matches on-screen
        display exactly). Phase 1 removed the CPU compositing fallback.
        """
        return self._ui_facade_for().get_pixel_color(x, y)

    def set_active_color(self, color: ColorDescriptor, *, source: str = "plugin") -> None:
        """Update the active color using a command so undo systems can react."""
        self._color_facade_for().set_active_color(color, source=source)

    def subscribe_color_changed(self, callback: Callable[[ColorStateSnapshot], None]) -> Any:
        """Subscribe to color change events and receive state snapshots."""
        return self._color_facade_for().subscribe_color_changed(callback)

    def subscribe_menu_visibility_changed(
        self, callback: Callable[[MenuVisibilityEvent], None]
    ) -> Callable[[], None]:
        """Subscribe to menu visibility toggles emitted by the host UI."""

        def _handler(payload: Any) -> None:
            try:
                if isinstance(payload, MenuVisibilityEvent):
                    callback(payload)
                    return
                if isinstance(payload, Mapping):
                    callback(
                        MenuVisibilityEvent(
                            menu_id=str(payload.get("menu_id", "unknown")),
                            enabled=bool(payload.get("enabled", False)),
                            source=payload.get("source"),
                        )
                    )
            except Exception as exc:
                logger.error("Menu visibility subscriber raised: %s", exc)

        subscribe = getattr(self.events, "on", None)
        unsubscribe_method: Callable[[], None] | None = None
        token: Any | None = None
        if not callable(subscribe):
            subscribe = getattr(self.events, "subscribe", None)
        if callable(subscribe):
            token = subscribe(Events.MENU_VISIBILITY_CHANGED, _handler)
            self._menu_visibility_handlers.append(_handler)
            if callable(token):

                def _wrapped_unsubscribe() -> None:
                    cast(Callable[[], None], token)()
                    with contextlib.suppress(ValueError):
                        self._menu_visibility_handlers.remove(_handler)

                return _wrapped_unsubscribe
            unsubscribe_method = getattr(self.events, "off", None)
            if not callable(unsubscribe_method):
                unsubscribe_method = getattr(self.events, "unsubscribe", None)
            if callable(unsubscribe_method):

                def _unsubscribe() -> None:
                    with contextlib.suppress(Exception):
                        unsubscribe_method(Events.MENU_VISIBILITY_CHANGED, token)
                    with contextlib.suppress(ValueError):
                        self._menu_visibility_handlers.remove(_handler)

                return _unsubscribe
        raise RuntimeError("Event bus does not support menu visibility subscription")

    def emit_color_state(self, *, source: str = "bootstrap") -> None:
        """Broadcast the current color state to all listeners."""
        self._color_facade_for().emit_color_state(source=source)

    def _persist_color_state(self) -> None:
        self._color_facade_for().persist_color_state()

    def _publish_color_snapshot(self, snapshot: ColorStateSnapshot, *, source: str) -> None:
        self._color_facade_for().publish_color_snapshot(snapshot, source=source)

    # --- ToolManager bridging (for UI to register tools) ---
    _tool_manager: Any | None = field(default=None, init=False, repr=False, compare=False)
    _pending_tools: list[Any] = field(default_factory=list, init=False, repr=False, compare=False)
    _tool_unregistering: set[str] = field(
        default_factory=set, init=False, repr=False, compare=False
    )

    def set_tool_manager(self, tool_manager: Any) -> None:
        """Bind a ToolManager so plugins can register tools via HostAPI."""
        object.__setattr__(self, "_tool_manager", tool_manager)
        # Flush any tools registered before the tool manager was set
        manager_obj = self._tool_manager
        for tool in list(self._pending_tools):
            if manager_obj is None or not hasattr(manager_obj, "register_tool"):
                break
            try:
                manager_obj.register_tool(tool)
            except Exception:
                continue
        self._pending_tools.clear()

    def register_tool(self, tool: Any) -> None:
        """Allow plugins to register tools in a HostAPI-friendly way."""
        tool_manager = self._tool_manager
        if tool_manager is not None and hasattr(tool_manager, "register_tool"):
            try:
                tool_manager.register_tool(tool)
                return
            except Exception:
                logger.debug("tool manager register_tool failed", exc_info=True)
        self._pending_tools.append(tool)

    def unregister_tool(self, tool_id: str) -> None:
        tm = getattr(self, "_tool_manager", None)
        if not tm or not hasattr(tm, "unregister_tool"):
            return
        if tool_id in self._tool_unregistering:
            return
        self._tool_unregistering.add(tool_id)
        try:
            with contextlib.suppress(Exception):
                tm.unregister_tool(tool_id)
        finally:
            self._tool_unregistering.discard(tool_id)

    # --- Generation history ---------------------------------------------------
    _generation_history: Any | None = field(default=None, init=False, repr=False, compare=False)
    # --- AI Effect gateway registry -------------------------------------------
    # Exposed so panels (Effect Panel, Layers Panel) can look up the
    # currently-registered AIGateway instances without importing the AI
    # Effect plugin's internals. Used at runtime to fetch the gateway's
    # service-cached ``object_info`` for parameter-widget enrichment of
    # presets authored before backend constraints were captured.
    _ai_effect_gateway_registry: Any | None = field(
        default=None, init=False, repr=False, compare=False
    )
    # --- AI Effect generation submitter ----------------------------------------
    # Async callable ``(Mapping) -> dict`` accepting loomstroke_submit_generation
    # style arguments. Attached by the app (privacy fail-close wrapped) so
    # plugins (SAM3 AI Select preview submit) can run asset-only generations
    # without importing the AI Effect coordinator.
    _ai_effect_generation_submitter: Any | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def attach_generation_history(self, store: Any) -> None:
        """Attach the GenerationHistoryStore after bootstrap."""
        object.__setattr__(self, "_generation_history", store)

    def attach_ai_effect_gateway_registry(self, registry: Any) -> None:
        """Attach the :class:`GatewayRegistry` after AI Effect subsystem init."""
        object.__setattr__(self, "_ai_effect_gateway_registry", registry)

    def attach_ai_effect_generation_submitter(self, submitter: Any) -> None:
        """Attach the async generation submitter after MCP handler bootstrap."""
        object.__setattr__(self, "_ai_effect_generation_submitter", submitter)

    def get_service(self, name: str) -> Any | None:
        """Look up an optional service by name.

        Currently supported names:
        - ``"generation_history"`` → GenerationHistoryStore
        - ``"ai_effect_gateway_registry"`` → ``GatewayRegistry``
        - ``"ai_effect_generation_submitter"`` → async ``(Mapping) -> dict``
        - ``"filter_pen"`` → optional command-backed raster filter stroke service
          (``"effect_pen"`` remains a compatibility alias)
        """
        if name == "generation_history":
            return self._generation_history
        if name == "ai_effect_gateway_registry":
            return self._ai_effect_gateway_registry
        if name == "ai_effect_generation_submitter":
            return self._ai_effect_generation_submitter
        if name in {"filter_pen", "effect_pen"}:
            return self._ui_facade_for().get_filter_pen_service()
        return None
