"""Internal color-state delegation for :mod:`loomstroke.host_api`."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, cast

from .core.color_state import ColorDescriptor, ColorState, ColorStateSnapshot
from .core.commands import SetActiveColorCommand
from .core.events import ColorChangeEvent, Events

if TYPE_CHECKING:
    from .host_api import HostAPI

logger = logging.getLogger("loomstroke.host_api")


class HostAPIColorFacade:
    """Own the HostAPI color bridge without changing the plugin-facing API."""

    def __init__(self, host: HostAPI) -> None:
        self._host = host

    def attach_color_state(self, state: ColorState) -> None:
        """Attach an external color state instance (bootstrap)."""
        object.__setattr__(self._host, "color_state", state)
        self.persist_color_state()

    def get_active_color(self) -> ColorDescriptor:
        """Return the current active color descriptor."""
        return self._host.color_state.current

    def get_color_snapshot(self) -> ColorStateSnapshot:
        """Return a snapshot of the current color state."""
        return self._host.color_state.as_snapshot()

    def get_color_history(self, limit: int = 10) -> Sequence[ColorDescriptor]:
        """Return recent color history up to limit entries."""
        if limit <= 0:
            return tuple()
        history = list(self._host.color_state.history)
        return tuple(history[:limit])

    def set_active_color(self, color: ColorDescriptor, *, source: str = "plugin") -> None:
        """Update the active color using a command so undo systems can react."""
        core_event_bus = getattr(self._host, "events", None)
        command = SetActiveColorCommand(
            self._host.color_state,
            color,
            event_bus=core_event_bus,
            source=source,
        )
        executed = False
        canvas = getattr(self._host, "canvas", None)
        submit = getattr(canvas, "submit", None) if canvas else None
        if callable(submit):
            try:
                executed = bool(submit(command, merge_key="color.change"))
            except Exception as exc:
                logger.warning("Canvas submit for color change failed: %s", exc)
        if not executed:
            try:
                command.apply(cast(Any, canvas))
            except Exception as exc:
                logger.error("Failed to apply color change command: %s", exc)
                return
        self.persist_color_state()

    def subscribe_color_changed(self, callback: Callable[[ColorStateSnapshot], None]) -> Any:
        """Subscribe to color change events and receive state snapshots."""

        def _handler(_payload: Any) -> None:
            try:
                callback(self.get_color_snapshot())
            except Exception as exc:
                logger.error("Color change subscriber raised: %s", exc)

        # Keep a strong reference so the EventBus weak-ref does not GC the closure.
        self._host._color_change_handlers.append(_handler)

        subscribe = getattr(self._host.events, "on", None)
        if not callable(subscribe):
            subscribe = getattr(self._host.events, "subscribe", None)
        if callable(subscribe):
            return subscribe(Events.COLOR_CHANGED, _handler)
        raise RuntimeError("Event bus does not support color change subscription")

    def emit_color_state(self, *, source: str = "bootstrap") -> None:
        """Broadcast the current color state to all listeners."""
        self.publish_color_snapshot(self.get_color_snapshot(), source=source)

    def persist_color_state(self) -> None:
        """Persist the current color state through the attached settings service."""
        setter = getattr(self._host.settings, "set", None)
        if callable(setter):
            try:
                setter("color/state", self._host.color_state.to_dict())
            except Exception as exc:
                logger.debug("Failed to persist color state: %s", exc)

    def publish_color_snapshot(self, snapshot: ColorStateSnapshot, *, source: str) -> None:
        """Publish a color snapshot on the host event bus."""
        payload = ColorChangeEvent(
            rgba=snapshot.current.rgba,
            blend_mode=snapshot.current.blend_mode,
            previous_rgba=snapshot.previous.rgba if snapshot.previous else None,
            history=tuple(color.rgba for color in snapshot.history),
            source=source,
        )
        publish = getattr(self._host.events, "publish", None)
        if callable(publish):
            publish(Events.COLOR_CHANGED, payload)
            return
        emit = getattr(self._host.events, "emit", None)
        if callable(emit):
            emit(Events.COLOR_CHANGED, payload)


__all__ = ["HostAPIColorFacade"]
