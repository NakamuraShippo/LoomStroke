"""Core EventBus wiring and history/tool event handling for MainWindow."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..core.events import Events, HistoryChangedEvent, HistoryResetEvent


class MainWindowCoreEventCoordinator:
    """Coordinate MainWindow core EventBus subscriptions and lightweight handlers."""

    def __init__(self, window: Any) -> None:
        self._window = window

    def initialize_state(self) -> None:
        self._window._active_tool = None

    def connect_signals(self) -> None:
        """Connect core document/tool/history signals to MainWindow facades."""
        window = self._window
        window._core_event_handlers = [
            window._on_tool_changed_event,
            window._on_document_changed_event,
            window._on_document_saved_event,
            window._on_document_loaded_event,
            window._on_document_created_event,
            window._on_document_closed_event,
            window._on_document_save_failed_event,
            window._on_document_save_outcome_event,
        ]
        window._event_bus.on(Events.TOOL_CHANGED, window._core_event_handlers[0])
        window._event_bus.on(Events.DOC_CHANGED, window._core_event_handlers[1])
        window._event_bus.on(Events.DOC_SAVED, window._core_event_handlers[2])
        window._event_bus.on(Events.DOC_LOADED, window._core_event_handlers[3])
        window._event_bus.on(Events.DOC_CREATED, window._core_event_handlers[4])
        window._event_bus.on(Events.DOC_CLOSED, window._core_event_handlers[5])
        window._event_bus.on(Events.DOC_SAVE_FAILED, window._core_event_handlers[6])
        window._event_bus.on(Events.DOC_SAVE_OUTCOME, window._core_event_handlers[7])

        window._history_event_handlers.clear()
        window._history_event_tokens.clear()
        history_handler = window._on_history_changed_event
        reset_handler = window._on_history_reset_event
        window._history_event_handlers.extend([history_handler, reset_handler])
        window._history_event_tokens.append(
            window._event_bus.on(Events.HISTORY_CHANGED, history_handler)
        )
        window._history_event_tokens.append(
            window._event_bus.on(Events.HISTORY_STATE, history_handler)
        )
        window._history_event_tokens.append(
            window._event_bus.on(Events.DOC_HISTORY_RESET, reset_handler)
        )
        self.connect_startpage_requests()

    def connect_startpage_requests(self) -> None:
        """Connect StartPage-related EventBus requests."""
        window = self._window
        window._startpage_event_handlers.clear()
        window._startpage_event_tokens.clear()
        window._layer_event_bus_ids.clear()

        new_handler = window._on_new_request
        open_handler = window._on_open_request
        close_handler = window._on_close_request
        create_handler = window._on_create_document_request

        window._startpage_event_handlers.extend(
            [new_handler, open_handler, close_handler, create_handler]
        )
        window._startpage_event_tokens.append(window._event_bus.on("file.new", new_handler))
        window._startpage_event_tokens.append(window._event_bus.on("file.open", open_handler))
        window._startpage_event_tokens.append(window._event_bus.on("file.close", close_handler))
        window._startpage_event_tokens.append(
            window._event_bus.on("ui.request.create_document", create_handler)
        )
        self.subscribe_layer_events(window._event_bus)

    def connect_panel_events(self) -> None:
        """Connect panel/plugin EventBus handlers owned by MainWindow."""
        window = self._window
        panel_register_handler = window._on_panel_register
        panel_toggle_handler = window._on_panel_toggle
        panel_rename_handler = window._on_panel_rename
        plugin_manager_handler = window._on_plugin_manager_event
        window._panel_event_handlers.extend(
            [panel_register_handler, panel_toggle_handler, panel_rename_handler]
        )
        window._panel_event_tokens.extend(
            [
                window._event_bus.on("ui.panel.register", panel_register_handler),
                window._event_bus.on("ui.panel.toggle", panel_toggle_handler),
                window._event_bus.on("ui.panel.rename", panel_rename_handler),
            ]
        )
        window._plugin_manager_event_handlers.append(plugin_manager_handler)
        window._plugin_manager_event_tokens.append(
            window._event_bus.on("ui.plugins.manager", plugin_manager_handler)
        )

        # Connect layer blend mode event (keep strong reference for weakref-based EventBus).
        blend_handler = window._on_layer_blend_mode_changed
        window._layer_event_handlers.append(blend_handler)
        window._layer_event_tokens.append(
            window._event_bus.on("layer.blend_mode_changed", blend_handler)
        )

    def subscribe_layer_events(self, bus: Any) -> None:
        window = self._window
        if bus is None or id(bus) in window._layer_event_bus_ids:
            return
        subscribe = getattr(bus, "on", None)
        if not callable(subscribe):
            return
        handler = window._on_layer_event_from_bus
        window._layer_event_handlers.append(handler)
        window._layer_event_tokens.append(
            subscribe(Events.LAYER_SELECTED, window._on_layer_selected_from_bus)
        )
        for layer_event in (
            Events.ACTIVE_PAGE_CHANGED,
            Events.LAYER_ADDED,
            Events.LAYER_REMOVED,
            Events.LAYER_CHANGED,
        ):
            window._layer_event_tokens.append(subscribe(layer_event, handler))
        window._layer_event_bus_ids.add(id(bus))

    def disconnect_event_bus_subscriptions(self) -> None:
        """Disconnect EventBus subscriptions owned by MainWindow."""
        window = self._window
        bus = window._event_bus
        if bus is None:
            return

        window._history_event_handlers.clear()
        window._panel_event_handlers.clear()
        window._plugin_manager_event_handlers.clear()
        window._layer_event_handlers.clear()
        window._startpage_event_handlers.clear()

        if window._history_event_tokens:
            history_events = [
                Events.HISTORY_CHANGED,
                Events.HISTORY_STATE,
                Events.DOC_HISTORY_RESET,
            ]
            for event, token in zip(
                history_events,
                window._history_event_tokens,
                strict=False,
            ):
                bus.off(event, token)
            window._history_event_tokens.clear()

        if window._startpage_event_tokens:
            start_events = [
                "file.new",
                "file.open",
                "file.close",
                "ui.request.create_document",
            ]
            for event, token in zip(
                start_events,
                window._startpage_event_tokens,
                strict=False,
            ):
                bus.off(event, token)
            window._startpage_event_tokens.clear()

        if window._panel_event_tokens:
            panel_events = [
                "ui.panel.register",
                "ui.panel.toggle",
                "ui.panel.rename",
            ]
            for event, token in zip(
                panel_events,
                window._panel_event_tokens,
                strict=False,
            ):
                bus.off(event, token)
            window._panel_event_tokens.clear()

        if window._plugin_manager_event_tokens:
            for token in window._plugin_manager_event_tokens:
                bus.off("ui.plugins.manager", token)
            window._plugin_manager_event_tokens.clear()

        if window._layer_event_tokens:
            layer_events = [
                Events.ACTIVE_PAGE_CHANGED,
                Events.LAYER_ADDED,
                Events.LAYER_REMOVED,
                Events.LAYER_CHANGED,
                Events.LAYER_SELECTED,
            ]
            for event, token in zip(layer_events, window._layer_event_tokens, strict=False):
                bus.off(event, token)
            window._layer_event_tokens.clear()

        window._layer_event_bus_ids.clear()

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
