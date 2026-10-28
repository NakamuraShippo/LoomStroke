"""
Core service implementations for HostAPI.

Provides minimal implementations of the required services
according to Master.md specifications.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable, Iterable
from contextlib import nullcontext
from typing import Any, cast

from ..host_api import (
    DocumentRef,
    HistoryAwareCanvas,
    LayerRef,
    Selection,
    SelectionPayload,
    UILayoutService,
)
from .events import Events, HistoryChangedEvent, HistoryStateEvent

logger = logging.getLogger(__name__)


class MinimalCanvasService:
    """Minimal implementation of CanvasService."""

    def __init__(self) -> None:
        """Initialize canvas service."""
        self._current_doc = DocumentRef(id="default", name="Untitled")
        self._current_layer = LayerRef(id="layer_1", name="Layer 1")
        self._layers: list[LayerRef] = [self._current_layer]
        self._busy = False
        self._width = 1920
        self._height = 1080
        self._dpi = 96
        self._tile_size = 256
        self._max_layers = 100

    def current_document(self) -> DocumentRef:
        """Get the current document reference."""
        return self._current_doc

    def current_layer(self) -> LayerRef:
        """Get the current active layer reference."""
        return self._current_layer

    def get_layers(self) -> list[LayerRef]:
        """Get all known layers for the current document."""
        return list(self._layers)

    def get_selection(self) -> Selection | None:
        """Get the current selection if any."""
        return None  # No selection by default

    def get_selection_snapshot(self) -> SelectionPayload | None:
        """Minimal stub for selection snapshot support."""
        return None

    def submit(self, cmd: Any, *, merge_key: str | None = None) -> bool:
        """Submit a command for execution."""
        logger.debug(f"Canvas command submitted: {type(cmd).__name__}")
        # Minimal implementation - just log for now
        return True

    def begin_transaction(self, label: str) -> None:
        """Begin a transaction for grouping commands."""
        logger.debug(f"Transaction started: {label}")

    def end_transaction(self) -> None:
        """End the current transaction."""
        logger.debug("Transaction ended")

    def is_busy(self) -> bool:
        """Check if canvas is currently processing commands."""
        return self._busy

    def cache_layer_snapshot(
        self,
        layer_id: str | None = None,
        fmt: str = "png",
        name: str | None = None,
    ) -> str:
        """Minimal stub for layer snapshot support.

        Raises:
            NotImplementedError: Always raised for minimal service.
        """
        logger.warning("Layer snapshot caching is not available in MinimalCanvasService")
        raise NotImplementedError("Layer snapshot caching is not available in MinimalCanvasService")

    def apply_image(
        self,
        image: Any,
        *,
        position: tuple[int, int] = (0, 0),
        blend_mode: str = "normal",
        opacity: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Minimal stub for image application support."""
        logger.warning("ApplyImageCommand is not supported by MinimalCanvasService")
        return False

    def undo_image(
        self,
        *,
        position: tuple[int, int] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Minimal stub for undoing image application."""
        return False

    def get_layer_content_bounds(
        self, layer_id: str | None = None
    ) -> tuple[int, int, int, int] | None:
        """Minimal stub — no pixel data available."""
        return None

    def get_canvas_size(self) -> tuple[int, int]:
        """Minimal stub — returns default size."""
        return (0, 0)

    def rasterize_layer(self, layer_id: str) -> bool:
        """Minimal stub for layer rasterization."""
        logger.warning("Rasterize is not supported by MinimalCanvasService (%s)", layer_id)
        return False

    def update_canvas_config(
        self,
        *,
        width: int | None = None,
        height: int | None = None,
        dpi: int | None = None,
        tile_size: int | None = None,
        max_layers: int | None = None,
        anchor: tuple[float, float] | None = None,
    ) -> dict[str, Any]:
        """Update stored canvas configuration values."""
        applied: dict[str, Any] = {}

        if width is not None:
            self._width = max(1, int(width))
            applied["width"] = self._width

        if height is not None:
            self._height = max(1, int(height))
            applied["height"] = self._height

        if dpi is not None:
            self._dpi = max(1, int(dpi))
            applied["dpi"] = self._dpi

        if tile_size is not None:
            self._tile_size = max(1, int(tile_size))
            applied["tile_size"] = self._tile_size

        if max_layers is not None:
            self._max_layers = max(1, int(max_layers))
            applied["max_layers"] = self._max_layers

        return applied

    def set_active_layer(self, layer_id: str) -> None:
        """Set the current layer by ID (no-op if not found)."""
        for layer in self._layers:
            if layer.id == layer_id:
                self._current_layer = layer
                break


class MinimalHistoryService:
    """Delegate history operations to canvases that expose history hooks.

    CT-05 H5b: with an injected ``DocumentHistoryRouter`` (internal
    runtime object owned by ``PageServiceImpl``) this service becomes the
    unified document-history facade — can_undo/can_redo/undo/redo and the
    stack sizes cover the active content history *and* the book history
    in sequence chronology, and this service is the single publisher of
    the HISTORY_STATE / HISTORY_CHANGED pair.  Without a router it keeps
    the pre-H5b canvas-only behaviour (compatibility fallback).  The
    public HistoryService contract and event payload schemas are
    unchanged either way.
    """

    def __init__(
        self,
        canvas: Any | None = None,
        *,
        event_bus: Any | None = None,
        router: Any | None = None,
        state_notifier: Any | None = None,
        page_context_resolver: Callable[[str], Any] | None = None,
    ) -> None:
        self._canvas: HistoryAwareCanvas | None = None
        self._event_bus = event_bus
        self._router = router
        self._state_notifier = state_notifier
        self._page_context_resolver = page_context_resolver
        self._last_state: Any | None = None
        self._history_reset_token: Any | None = None
        self._history_reset_handler = self._handle_history_reset_event
        self._document_created_handler = self._handle_document_created_event
        if canvas is not None:
            self.attach_canvas(canvas)
        else:
            self._emit_state()
        self._subscribe_history_reset()

    def attach_canvas(self, canvas: Any) -> None:
        """Bind the canvas used for history operations."""

        if isinstance(canvas, HistoryAwareCanvas):
            self._canvas = canvas
        else:
            self._canvas = None
        self._emit_state()

    def can_undo(self) -> bool:
        """Check if undo is available."""

        if self._router is not None:
            return bool(self._router.can_undo())
        return self._call_bool("history_can_undo")

    def can_redo(self) -> bool:
        """Check if redo is available."""

        if self._router is not None:
            return bool(self._router.can_redo())
        return self._call_bool("history_can_redo")

    def undo(self) -> bool:
        """Perform undo operation.

        Router-backed: the router picks the content/book candidate with
        the largest sequence and never falls back to the other scope on
        failure; the dispatch runs in a notifier batch so one logical
        undo publishes exactly one unified state pair.
        """

        if self._router is not None:
            with self._notifier_batch():
                result = bool(self._router.undo())
        else:
            result = self._call_bool("history_undo")
        self._emit_state()
        return result

    def redo(self) -> bool:
        """Perform redo operation (router-backed: smallest sequence)."""

        if self._router is not None:
            with self._notifier_batch():
                result = bool(self._router.redo())
        else:
            result = self._call_bool("history_redo")
        self._emit_state()
        return result

    def get_undo_stack_size(self) -> int:
        """Get the number of undoable operations."""

        if self._router is not None:
            return int(self._router.undo_count())
        return self._call_int("history_undo_count")

    def get_redo_stack_size(self) -> int:
        """Get the number of redoable operations."""

        if self._router is not None:
            return int(self._router.redo_count())
        return self._call_int("history_redo_count")

    # ------------------------------------------------------------------
    # Internal candidate surface for the H5c action presentation.  Not
    # part of the public HistoryService Protocol; read-only; canvas-only
    # fallbacks return None so the UI degrades to generic Undo/Redo.
    def _peek_undo_candidate(self) -> Any | None:
        """Return the routed next-undo candidate without mutating state."""
        if self._router is None:
            return None
        try:
            return self._router.peek_undo()
        except Exception:
            logger.debug("History undo candidate peek failed", exc_info=True)
            return None

    def _peek_redo_candidate(self) -> Any | None:
        """Return the routed next-redo candidate without mutating state."""
        if self._router is None:
            return None
        try:
            return self._router.peek_redo()
        except Exception:
            logger.debug("History redo candidate peek failed", exc_info=True)
            return None

    def _resolve_history_page_context(self, page_id: str) -> Any | None:
        """Resolve page metadata for a candidate (H5c wiring; read-only)."""
        resolver = self._page_context_resolver
        if resolver is None or not page_id:
            return None
        try:
            return resolver(page_id)
        except Exception:
            logger.debug("History page context resolution failed", exc_info=True)
            return None

    def notify_document_history_changed(self) -> None:
        """Unified-history listener entry point (CT-05 H5b wiring).

        ``PageServiceImpl``'s state notifier calls this once per logical
        operation; the state is re-evaluated and published only when the
        internal dedup key actually changed.
        """
        self._emit_state()

    def _notifier_batch(self) -> Any:
        notifier = self._state_notifier
        if notifier is not None:
            batch = getattr(notifier, "batch", None)
            if callable(batch):
                return batch()
        return nullcontext()

    def _history_snapshot(self) -> tuple[bool, bool, int, int]:
        if self._router is not None:
            undo_count = int(self._router.undo_count())
            redo_count = int(self._router.redo_count())
            return (
                bool(self._router.can_undo()),
                bool(self._router.can_redo()),
                undo_count + redo_count,
                undo_count,
            )
        can_undo = self._call_bool("history_can_undo")
        can_redo = self._call_bool("history_can_redo")
        undo_count = self._call_int("history_undo_count")
        redo_count = self._call_int("history_redo_count")
        length = undo_count + redo_count
        cursor = undo_count
        return (can_undo, can_redo, length, cursor)

    def _state_dedup_key(self, snapshot: tuple[bool, bool, int, int]) -> Any:
        """Internal re-notify key; never part of the public payloads.

        The router-backed key also tracks the top candidates
        (scope/sequence/active page) so a page switch between pages with
        identical counts, or a same-count content→book top change, still
        notifies — H5c labels will depend on the candidate (CT-05 H5b).
        """
        if self._router is None:
            return snapshot

        def _candidate_key(candidate: Any) -> Any:
            if candidate is None:
                return None
            return (
                getattr(candidate, "scope", None),
                getattr(candidate, "sequence", None),
                getattr(candidate, "active_page_id", None),
            )

        return (
            snapshot,
            _candidate_key(self._router.peek_undo()),
            _candidate_key(self._router.peek_redo()),
        )

    def _emit_state(self) -> None:
        if not self._event_bus:
            return
        snapshot = self._history_snapshot()
        dedup_key = self._state_dedup_key(snapshot)
        if dedup_key == self._last_state:
            return
        self._last_state = dedup_key
        payload = HistoryStateEvent(can_undo=snapshot[0], can_redo=snapshot[1])
        changed_payload = HistoryChangedEvent(
            can_undo=snapshot[0],
            can_redo=snapshot[1],
            length=snapshot[2],
            cursor=snapshot[3],
        )
        publish = getattr(self._event_bus, "publish", None)
        if callable(publish):
            publish(Events.HISTORY_STATE, payload)
            publish(Events.HISTORY_CHANGED, changed_payload)
            return
        emit = getattr(self._event_bus, "emit", None)
        if callable(emit):
            emit(Events.HISTORY_STATE, payload)
            emit(Events.HISTORY_CHANGED, changed_payload)

    def _subscribe_history_reset(self) -> None:
        if self._event_bus is None or self._history_reset_token is not None:
            return
        subscribe = getattr(self._event_bus, "on", None)
        if callable(subscribe):
            self._history_reset_token = subscribe(
                Events.DOC_HISTORY_RESET,
                self._history_reset_handler,
            )
            if self._router is None:
                self._document_created_token = subscribe(
                    Events.DOC_CREATED, self._document_created_handler
                )

    def _handle_document_created_event(self, _payload: Any) -> None:
        self._emit_state()

    def _handle_history_reset_event(self, payload: Any) -> None:
        # Force next snapshot to propagate by clearing cached state.
        self._last_state = None
        self._emit_state()

    def _call_bool(self, method_name: str) -> bool:
        canvas = self._canvas
        if canvas is None:
            return False
        method = getattr(canvas, method_name, None)
        if not callable(method):
            return False
        try:
            return bool(method())
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Canvas %s failed: %s", method_name, exc)
            return False

    def _call_int(self, method_name: str) -> int:
        canvas = self._canvas
        if canvas is None:
            return 0
        method = getattr(canvas, method_name, None)
        if not callable(method):
            return 0
        try:
            value = method()
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Canvas %s failed: %s", method_name, exc)
            return 0
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0


class MinimalInputService:
    """Minimal implementation of InputService."""

    def __init__(self) -> None:
        """Initialize input service."""
        self._captured_by: str | None = None

    def pointer_capture(self, tool_id: str) -> None:
        """Capture pointer input for a tool."""
        self._captured_by = tool_id
        logger.debug(f"Pointer captured by: {tool_id}")

    def release_capture(self, tool_id: str) -> None:
        """Release pointer capture."""
        if self._captured_by == tool_id:
            self._captured_by = None
            logger.debug(f"Pointer released by: {tool_id}")

    def is_captured(self) -> bool:
        """Check if pointer is captured."""
        return self._captured_by is not None

    def get_capture_owner(self) -> str | None:
        """Get the tool that owns pointer capture."""
        return self._captured_by

    def coalesced_points(self) -> list[Any]:
        """Return coalesced input points since last call (minimal stub)."""
        return []

    def predicted_points(self, count: int = 1) -> list[Any]:
        """Return predicted future points for smoothing (minimal stub)."""
        return []


class MinimalSettingsService:
    """Minimal implementation of SettingsService."""

    def __init__(self, settings_manager: Any) -> None:
        """
        Initialize settings service.

        Args:
            settings_manager: The global settings manager
        """
        self._settings = settings_manager
        self._subscriptions: dict[Any, tuple[str, Any]] = {}
        self._next_token = 0

    def get(self, key: str, default: Any = None) -> Any:
        """Get a setting value."""
        return self._settings.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a setting value."""
        self._settings.set(key, value)

    def get_section(self, section: str) -> dict[str, Any]:
        """Get all settings in a section."""

        data = self._settings.get_group(section)
        return cast(dict[str, Any], data)

    def subscribe(self, key: str, callback: Any) -> Any:
        """Subscribe to setting changes."""
        token = self._next_token
        self._next_token += 1
        self._subscriptions[token] = (key, callback)
        return token

    def unsubscribe(self, token: Any) -> None:
        """Unsubscribe from setting changes."""
        self._subscriptions.pop(token, None)


class MinimalUIService:
    """Minimal implementation of UIService."""

    def __init__(self) -> None:
        """Initialize UI service."""
        self._main_window: Any | None = None
        self._dock_widgets: list[tuple[Any, str]] = []
        self._tool_widgets: dict[str, Any] = {}
        self._progress_handlers: list[Any] = []
        self._layout_service: UILayoutService | None = None
        self._pending_panel_ops: deque[tuple[str, tuple[Any, ...], dict[str, Any]]] = deque()

    def get_main_window(self) -> Any:
        """Get the main window widget."""
        return self._main_window

    def get_layout_service(self) -> UILayoutService | None:
        """Return the registered layout service if available."""
        return self._layout_service

    def set_main_window(self, window: Any) -> None:
        """Set the main window reference."""
        self._main_window = window
        try:
            from ..ui.layout_service import QtUILayoutService  # noqa: PLC0415

            panel_provider: Callable[[], Iterable[Any]] | None = None
            if hasattr(window, "iter_panels") and callable(window.iter_panels):
                panel_provider = window.iter_panels
            settings_guard = getattr(window, "freeze_layout_settings", None)
            if settings_guard is not None and not callable(settings_guard):
                settings_guard = None
            self._layout_service = QtUILayoutService(
                window,
                panel_provider=panel_provider,
                settings_guard=settings_guard,
            )
        except Exception as exc:
            logger.debug("Failed to initialise UILayoutService: %s", exc)
            self._layout_service = None
        self._flush_pending_panel_ops()

    def _flush_pending_panel_ops(self) -> None:
        window = self._main_window
        if window is None:
            return
        while self._pending_panel_ops:
            op_name, args, kwargs = self._pending_panel_ops.popleft()
            try:
                if op_name == "register":
                    register = getattr(window, "register_dynamic_panel", None)
                    if callable(register):
                        register(*args, **kwargs)
                elif op_name == "unregister":
                    unregister = getattr(window, "unregister_panel", None)
                    if callable(unregister):
                        unregister(*args, **kwargs)
                elif op_name == "set_visible":
                    set_visible = getattr(window, "set_panel_visible", None)
                    if callable(set_visible):
                        set_visible(*args, **kwargs)
            except Exception as exc:
                logger.warning("Failed to flush pending panel operation (%s): %s", op_name, exc)

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
        window = self._main_window
        if window is None:
            self._pending_panel_ops.append(
                (
                    "register",
                    (panel_id, title, widget),
                    {
                        "area": area,
                        "default_visible": default_visible,
                        "owner_plugin_id": owner_plugin_id,
                        "closable": closable,
                        "resizable": resizable,
                        "floating": floating,
                        "default_size": default_size,
                    },
                )
            )
            return True
        register = getattr(window, "register_dynamic_panel", None)
        if not callable(register):
            logger.warning("Main window does not support dynamic panel registration")
            return False
        try:
            return bool(
                register(
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
            )
        except Exception as exc:
            logger.warning("register_panel_widget failed for %s: %s", panel_id, exc)
            return False

    def unregister_panel(self, panel_id: str) -> bool:
        window = self._main_window
        if window is None:
            self._pending_panel_ops.append(("unregister", (panel_id,), {}))
            return True
        unregister = getattr(window, "unregister_panel", None)
        if not callable(unregister):
            return False
        try:
            return bool(unregister(panel_id))
        except Exception as exc:
            logger.warning("unregister_panel failed for %s: %s", panel_id, exc)
            return False

    def has_panel(self, panel_id: str) -> bool:
        window = self._main_window
        if window is None:
            # A queued registration already owns its widget. Respect the last
            # structural operation so repeated plugin activation cannot rebuild it.
            for operation, args, _kwargs in reversed(self._pending_panel_ops):
                if args[0] == panel_id and operation in {"register", "unregister"}:
                    return operation == "register"
            return False
        has_panel = getattr(window, "has_panel", None)
        if not callable(has_panel):
            return False
        try:
            return bool(has_panel(panel_id))
        except Exception as exc:
            logger.debug("has_panel query failed for %s: %s", panel_id, exc)
            return False

    def set_panel_visible(self, panel_id: str, visible: bool) -> bool:
        window = self._main_window
        if window is None:
            self._pending_panel_ops.append(("set_visible", (panel_id, visible), {}))
            return True
        set_visible = getattr(window, "set_panel_visible", None)
        if not callable(set_visible):
            return False
        try:
            return bool(set_visible(panel_id, visible))
        except Exception as exc:
            logger.warning("set_panel_visible failed for %s: %s", panel_id, exc)
            return False

    def add_dock_widget(self, widget: Any, area: str = "left") -> None:
        """Add a dock widget to the main window."""
        self._dock_widgets.append((widget, area))
        logger.debug(f"Dock widget added to {area}")

    def add_tool_widget(self, tool_id: str, widget: Any) -> None:
        """Add a tool-specific widget."""
        self._tool_widgets[tool_id] = widget
        logger.debug(f"Tool widget added for: {tool_id}")

    def show_notification(self, message: str, level: str = "info") -> None:
        """Show a notification to the user."""
        logger.info(f"Notification ({level}): {message}")

    def show_message(self, message: str, duration: int = 3000) -> None:
        """Display a transient status message (compat with full UI service)."""
        logger.info(f"Message: {message} (duration={duration}ms)")
        self.show_notification(message, level="info")

    def show_progress(self, message: str, value: int, max_value: int = 100) -> None:
        """Show progress feedback."""
        logger.debug(f"Progress: {message} ({value}/{max_value})")

    def add_toolbar(self, toolbar: Any) -> None:
        """Add a toolbar to the main window (minimal stub)."""
        logger.debug("MinimalUIService.add_toolbar ignored (no main window)")

    def layout_snapshot(self, *, include_panels: bool = True) -> Any:
        """Capture a UI layout snapshot (minimal stub; returns None)."""
        return None


# Optional implementation hints, not required attributes of UIService providers.
# Copying a function's attributes to a wrapper does not mark the wrapper itself.
MinimalUIService.show_message.__dict__["_loomstroke_log_only_ui"] = MinimalUIService.show_message
MinimalUIService.show_notification.__dict__["_loomstroke_log_only_ui"] = (
    MinimalUIService.show_notification
)
