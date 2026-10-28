"""Core EventBus wiring and history/tool event handling for MainWindow."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..core.events import Events, HistoryChangedEvent, HistoryResetEvent


class MainWindowCoreEventCoordinator:
    """Coordinate MainWindow core EventBus subscriptions and lightweight handlers."""

    def __init__(self, window: Any) -> None:
        self._window = window
        # Each receipt owns the exact bound method retained by the weak bus.
        # Window lists remain compatibility views, never the unsubscribe key.
        self._subscriptions: list[tuple[str, Any, str, Any, Any]] = []

    def initialize_state(self) -> None:
        self._window._active_tool = None

    def connect_signals(self) -> None:
        """Connect core document/tool/history signals to MainWindow facades."""
        window = self._window
        self._replace_group(
            "core",
            window._event_bus,
            (
                (Events.TOOL_CHANGED, window._on_tool_changed_event),
                (Events.DOC_CHANGED, window._on_document_changed_event),
                (Events.DOC_SAVED, window._on_document_saved_event),
                (Events.DOC_LOADED, window._on_document_loaded_event),
                (Events.DOC_CREATED, window._on_document_created_event),
                (Events.DOC_CLOSED, window._on_document_closed_event),
                (Events.DOC_SAVE_FAILED, window._on_document_save_failed_event),
                (Events.DOC_SAVE_OUTCOME, window._on_document_save_outcome_event),
            ),
        )
        history_handler = window._on_history_changed_event
        self._replace_group(
            "history",
            window._event_bus,
            (
                (Events.HISTORY_CHANGED, history_handler),
                (Events.HISTORY_STATE, history_handler),
                (Events.DOC_HISTORY_RESET, window._on_history_reset_event),
            ),
        )
        self.connect_startpage_requests()

    def connect_startpage_requests(self) -> None:
        """Connect StartPage-related EventBus requests."""
        window = self._window
        self._replace_group(
            "startpage",
            window._event_bus,
            (
                ("file.new", window._on_new_request),
                ("file.open", window._on_open_request),
                ("file.close", window._on_close_request),
                ("ui.request.create_document", window._on_create_document_request),
            ),
        )
        self.subscribe_layer_events(window._event_bus)

    def connect_panel_events(self) -> None:
        """Connect panel/plugin EventBus handlers owned by MainWindow."""
        window = self._window
        self._replace_group(
            "panel",
            window._event_bus,
            (
                ("ui.panel.register", window._on_panel_register),
                ("ui.panel.toggle", window._on_panel_toggle),
                ("ui.panel.rename", window._on_panel_rename),
            ),
        )
        self._replace_group(
            "plugin_manager",
            window._event_bus,
            (("ui.plugins.manager", window._on_plugin_manager_event),),
        )
        self._replace_group(
            "blend",
            window._event_bus,
            (("layer.blend_mode_changed", window._on_layer_blend_mode_changed),),
        )

    def subscribe_layer_events(self, bus: Any) -> None:
        window = self._window
        if bus is None or any(
            group == "layer" and source is bus for group, source, *_ in self._subscriptions
        ):
            return
        handler = window._on_layer_event_from_bus
        self._add_group(
            "layer",
            bus,
            (
                (Events.LAYER_SELECTED, window._on_layer_selected_from_bus),
                (Events.ACTIVE_PAGE_CHANGED, handler),
                (Events.LAYER_ADDED, handler),
                (Events.LAYER_REMOVED, handler),
                (Events.LAYER_CHANGED, handler),
            ),
        )

    def disconnect_event_bus_subscriptions(self) -> None:
        """Disconnect EventBus subscriptions owned by MainWindow."""
        self._remove_group(None)

    def _replace_group(self, group: str, bus: Any, bindings: Any) -> None:
        self._remove_group(group)
        self._add_group(group, bus, bindings)

    def _add_group(self, group: str, bus: Any, bindings: Any) -> None:
        subscribe = getattr(bus, "on", None)
        if not callable(subscribe):
            return
        try:
            for event, handler in bindings:
                token = subscribe(event, handler)
                self._subscriptions.append((group, bus, event, token, handler))
        finally:
            # Partial registration remains owned and can be disconnected.
            self._sync_tracking_views()

    def _remove_group(self, group: str | None) -> None:
        removed = [record for record in self._subscriptions if group is None or record[0] == group]
        self._subscriptions = [
            record for record in self._subscriptions if group is not None and record[0] != group
        ]
        try:
            for index, (_kind, bus, event, token, _handler) in enumerate(removed):
                try:
                    bus.off(event, token)
                except Exception:
                    # Do not forget an off() failure or later registrations.
                    self._subscriptions.extend(removed[index:])
                    raise
        finally:
            self._sync_tracking_views()

    def _sync_tracking_views(self) -> None:
        window = self._window
        for name in ("core", "history", "startpage", "panel", "plugin_manager", "layer"):
            groups = ("layer", "blend") if name == "layer" else (name,)
            records = [record for record in self._subscriptions if record[0] in groups]
            handlers = []
            for _kind, _bus, _event, _token, handler in records:
                if all(existing is not handler for existing in handlers):
                    handlers.append(handler)
            handler_name = f"_{name}_event_handlers"
            view = getattr(window, handler_name, None)
            if isinstance(view, list):
                view[:] = handlers
            else:
                setattr(window, handler_name, handlers)
            if name != "core":
                getattr(window, f"_{name}_event_tokens")[:] = [record[3] for record in records]
        window._layer_event_bus_ids.clear()
        window._layer_event_bus_ids.update(
            id(bus) for kind, bus, *_ in self._subscriptions if kind == "layer"
        )

    def on_tool_changed_event(self, event: Any) -> None:
        """Handle tool change event from EventBus."""
        if hasattr(event, "new_tool_id"):
            self._window._active_tool = event.new_tool_id
            self._window.tool_changed.emit(event.new_tool_id)

    def on_history_changed_event(self, payload: Any) -> None:
        """Update undo/redo actions when history stacks change."""
        can_undo: bool | None
        can_redo: bool | None
        if isinstance(payload, HistoryChangedEvent):
            can_undo = payload.can_undo
            can_redo = payload.can_redo
        else:
            can_undo = getattr(payload, "can_undo", None)
            can_redo = getattr(payload, "can_redo", None)
            if isinstance(payload, Mapping):
                can_undo = payload.get("can_undo", can_undo)
                can_redo = payload.get("can_redo", can_redo)
        # history.changed is not a visual event: undo/redo pixel changes
        # reach the preview through the layer/composited invalidation path
        # (composited_changed), never through an extra readback here
        # (UI-PERF-01).
        self._window._update_history_actions(
            can_undo=bool(can_undo) if can_undo is not None else None,
            can_redo=bool(can_redo) if can_redo is not None else None,
        )

    def on_tool_undo_state_changed(self, available: bool) -> None:
        self._window._tool_can_undo = bool(available)
        self._window._update_history_actions()

    def on_history_reset_event(self, payload: Any) -> None:
        """Show a brief status message when history is cleared."""
        reason = getattr(payload, "reason", None)
        if isinstance(payload, HistoryResetEvent):
            reason = payload.reason or reason

        if reason == "save":
            message = self._window.tr("Undo history cleared after save")
        elif reason == "load":
            message = self._window.tr("Undo history cleared after load")
        else:
            message = self._window.tr("Undo history cleared")

        self._window.statusBar().showMessage(message, 3000)
        self._window._update_history_actions(can_undo=False, can_redo=False)
