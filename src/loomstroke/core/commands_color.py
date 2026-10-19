"""Color state commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .color_state import ColorDescriptor, ColorState, ColorStateSnapshot
from .command_types import Command
from .event_bus import EventBus
from .events import ColorChangeEvent, Events
from .native_cmyk.color_descriptor import set_canvas_foreground

if TYPE_CHECKING:
    from .canvas import Canvas


class SetActiveColorCommand(Command):
    """Command that updates the shared color state."""

    def __init__(
        self,
        state: ColorState,
        new_color: ColorDescriptor,
        *,
        event_bus: EventBus | None = None,
        source: str = "plugin",
    ) -> None:
        super().__init__(merge_key="color.change")
        self._state = state
        self._new_color = new_color.clamp()
        self._event_bus = event_bus
        self._source = source
        self._before: ColorStateSnapshot | None = None
        self._after: ColorStateSnapshot | None = None

    def apply(self, canvas: Canvas) -> bool:
        self._before = self._state.as_snapshot()
        set_canvas_foreground(canvas, self._new_color)
        if self._before.current == self._new_color:
            self._after = self._before
            return True
        self._after = self._state.apply(self._new_color)
        self._emit_event(self._after)
        self._executed = True
        return True

    def revert(self, canvas: Canvas) -> bool:
        if not self._before:
            return False
        set_canvas_foreground(canvas, self._before.current)
        self._state.revert(self._before)
        self._emit_event(self._before)
        self._executed = False
        return True

    def _emit_event(self, snapshot: ColorStateSnapshot) -> None:
        if not self._event_bus:
            return
        payload = ColorChangeEvent(
            rgba=snapshot.current.rgba,
            blend_mode=snapshot.current.blend_mode,
            previous_rgba=snapshot.previous.rgba if snapshot.previous else None,
            history=tuple(color.rgba for color in snapshot.history),
            source=self._source,
        )
        publish = getattr(self._event_bus, "publish", None)
        if callable(publish):
            publish(Events.COLOR_CHANGED, payload)
            return
        emit = getattr(self._event_bus, "emit", None)
        if callable(emit):
            emit(Events.COLOR_CHANGED, payload)
