"""Two-point measurement; no document commands or history state."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from PySide6.QtGui import QPainter
from shiboken6 import isValid

from ...core.events import Events
from ...core.measurement_geometry import measure_points
from ...host_api import StrokePoint
from ...ui.canvas.measurement_overlay import draw_measurement
from ...ui.translations import translated_literal_domain
from ..sdk.base import PluginBase, PluginMetadata, PluginType
from ..sdk.snap import get_guide_controller, snap_position
from ..sdk.tool import ToolDescriptor, ToolMetadata, ToolSettings


def _ts(source: str) -> str:
    return translated_literal_domain("MeasurementTool", source, use_fallback=True)


class MeasurementTool(PluginBase):
    def __init__(self) -> None:
        super().__init__(
            PluginMetadata(
                id="loomstroke.measurement",
                name=_ts("Measure"),
                version="0.1.0",
                description=_ts("Measure distances and angles"),
                author="LoomStroke Team",
                types=[PluginType.TOOL],
            )
        )
        self.start: tuple[float, float] | None = None
        self.end: tuple[float, float] | None = None
        self._dragging = False
        self._widget: Any = None
        self._registered = False
        self._page_token: Any = None
        self._page_handler = self._on_page_changed

    def descriptor(self) -> ToolDescriptor:
        return ToolDescriptor(
            id="layout_measure",
            display_name=_ts("Measure"),
            cursor="crosshair",
            category="utility",
            order=95,
        )

    def tool_metadata(self) -> ToolMetadata:
        return ToolMetadata(
            tool_id="layout_measure",
            display_name=_ts("Measure"),
            category="utility",
            tags=["measure"],
            group_id="utility.measure",
            group_label=_ts("Measure"),
        )

    def activate(self, host_api: Any) -> None:
        super().activate(host_api)
        manager = getattr(host_api, "_tool_manager", None)
        if manager is not None and manager.has_tool("layout_measure"):
            return
        if any(
            getattr(getattr(tool, "descriptor", lambda: None)(), "id", None) == "layout_measure"
            for tool in getattr(host_api, "_pending_tools", [])
        ):
            return
        host_api.register_tool(self)
        self._registered = True

    def enable(self) -> None:
        bus = getattr(self.host_api, "events", None)
        if bus is not None and self._page_token is None:
            self._page_token = bus.on(Events.ACTIVE_PAGE_CHANGED, self._page_handler)

    def disable(self) -> None:
        self._clear()
        bus = getattr(self.host_api, "events", None)
        if bus is not None and self._page_token is not None:
            bus.off(Events.ACTIVE_PAGE_CHANGED, self._page_token)
        self._page_token = None

    def deactivate(self) -> None:
        self.disable()
        if self._registered and self.host_api is not None:
            self.host_api.unregister_tool("layout_measure")
        self._registered = False
        super().deactivate()

    def _on_page_changed(self, payload: object) -> None:
        self._clear()

    def _clear(self) -> None:
        self.start = self.end = None
        self._dragging = False
        if self._widget is not None and isValid(self._widget):
            if getattr(self._widget, "_tool_overlay_fn", None) == self._paint:
                self._widget.set_tool_overlay_fn(None)
            self._widget.update()
        self._widget = None

    def on_pointer_down(self, pt: StrokePoint, **modifiers: bool) -> None:
        self.start = self.end = snap_position(self.host_api, *pt.pos)
        self._dragging = True
        ui = getattr(self.host_api, "ui", None)
        window = getattr(ui, "get_main_window", lambda: None)()
        self._widget = getattr(window, "canvas", None)
        if self._widget is not None and isValid(self._widget):
            self._widget.set_tool_overlay_fn(self._paint)
            self._widget.update()

    def on_pointer_move(self, pts: Iterable[StrokePoint]) -> None:
        if not self._dragging:
            return
        for point in pts:
            self.end = snap_position(self.host_api, *point.pos)
        if self._widget is not None and isValid(self._widget):
            self._widget.update()

    def on_pointer_up(self, pt: StrokePoint) -> None:
        if self._dragging:
            self.on_pointer_move((pt,))
            self._dragging = False

    def on_pointer_hover(self, pt: StrokePoint) -> None:
        pass

    def on_key(self, key: str, pressed: bool) -> bool:
        if pressed and key in ("Escape", "Esc"):
            self._clear()
            return True
        return False

    def on_wheel(self, delta: float, pt: StrokePoint) -> bool:
        return False

    def build_commands(self) -> list[Any]:
        return []

    def get_settings(self) -> ToolSettings:
        return ToolSettings()

    def set_settings(self, settings: ToolSettings) -> None:
        pass

    def create_options_widget(self) -> None:
        return None

    def context_toolbar_spec(self) -> None:
        return None

    def get_context_anchor_rect(self) -> None:
        return None

    def _paint(self, painter: QPainter) -> None:
        if self.start is None or self.end is None:
            return
        controller = get_guide_controller(self.host_api)
        if controller is not None:
            unit, dpi = controller.ruler_state().unit, controller.document_dpi()
        else:
            spec = self.host_api.pages.get_active_canvas_spec()
            unit = self.host_api.settings.get("view/ruler_unit", "px")
            dpi = spec.dpi
        result = measure_points(self.start, self.end, unit=unit, dpi=dpi)
        draw_measurement(painter, self.start, self.end, result)
