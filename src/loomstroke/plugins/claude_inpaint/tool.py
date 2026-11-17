"""Inpaint pen: continue surrounding contours and colours across a brush stroke."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import numpy as np

from loomstroke.core.telea_inpaint import TeleaInpaintCommand
from loomstroke.plugins.sdk.base import PluginMetadata, PluginType
from loomstroke.plugins.sdk.tool import PropertySpec, PropertyType, ToolDescriptor, ToolMetadata

from .algorithm import MAX_FEATHER, claude_inpaint_pixels
from .controller import LocalRepairPreparationController, RepairMessages
from .mask_preview import RepairMaskPreview

_MAX_SIZE = 200
_MAX_RADIUS = 15


def _ts(source: str) -> str:
    from loomstroke.ui.translations import translated_literal_domain  # noqa: PLC0415

    return translated_literal_domain("ClaudeInpaintPen", source, use_fallback=True)


_ICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"
 fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round">
 <path d="M3 8c4 0 5 3 9 3M12 13c4 0 5 3 9 3"/>
 <path d="M12 3v2M12 19v2M5 12h2M17 12h2"/>
 <circle cx="12" cy="12" r="2.2"/>
 </svg>"""

_MESSAGES = RepairMessages(
    "An inpaint is still being prepared.",
    "The inpaint pen could not repair this area.",
    "The document changed. The inpaint result was discarded.",
    "The inpaint pen could not apply the result. The original pixels were preserved.",
)


class ClaudeInpaintCommand(TeleaInpaintCommand):
    """One local, undoable raster patch for the inpaint pen."""


class ClaudeInpaintPenPlugin:
    """Collect a brush mask, show it, and repair it on a detached worker."""

    TOOL_ID = "claude_inpaint_pen"

    def __init__(self) -> None:
        self.host_api: Any = None
        self.size = 24
        self.radius = 3
        self.feather = 0
        self._stroke_feather = 0
        self._points: list[tuple[float, float]] = []
        self._drawing = False
        self._mask_preview = RepairMaskPreview()
        self._preparation = LocalRepairPreparationController(
            self,
            processor=self._process,
            command_type=ClaudeInpaintCommand,
            kind="claude_inpaint",
            domain="ClaudeInpaintPen",
            messages=_MESSAGES,
            context_padding=24,
        )
        self.metadata = PluginMetadata(
            id="loomstroke.claude_inpaint_pen",
            name=_ts("Inpaint Pen"),
            version="1.0.0",
            description=_ts("Continue surrounding contours and colors across the stroke"),
            author="LoomStroke Team",
            types=[PluginType.TOOL],
        )

    def descriptor(self) -> ToolDescriptor:
        return ToolDescriptor(
            id=self.TOOL_ID,
            display_name=_ts("Inpaint Pen"),
            cursor="crosshair",
            category="brush",
            icon_svg=_ICON,
            order=3,
        )

    def tool_metadata(self) -> ToolMetadata:
        descriptor = self.descriptor()
        return ToolMetadata(
            tool_id=descriptor.id,
            display_name=descriptor.display_name,
            category="brush",
            tags=["brush", "pen", "repair", "inpaint"],
            icon_svg=_ICON,
            group_id="brush.basic",
            group_label=_ts("Brushes"),
            group_order=10,
            properties=[
                PropertySpec(
                    key="size",
                    display_name=_ts("Size"),
                    type=PropertyType.INT,
                    min_value=1,
                    max_value=_MAX_SIZE,
                    default_value=self.size,
                    step=1,
                ),
                PropertySpec(
                    key="radius",
                    display_name=_ts("Sample Radius"),
                    type=PropertyType.INT,
                    min_value=1,
                    max_value=_MAX_RADIUS,
                    default_value=self.radius,
                    step=1,
                ),
                PropertySpec(
                    key="feather",
                    display_name=_ts("Edge Blend"),
                    type=PropertyType.INT,
                    min_value=0,
                    max_value=MAX_FEATHER,
                    default_value=self.feather,
                    step=1,
                ),
            ],
        )

    def activate(self, host: Any) -> None:
        self.host_api = host
        if not self._tool_registered(host):
            register = getattr(host, "register_tool", None)
            if callable(register):
                register(self)

    def enable(self) -> None:
        if self.host_api:
            self.host_api.events.emit("tool.changed", {"tool": self.TOOL_ID})

    def disable(self) -> None:
        self._cancel_stroke()
        self._preparation.cancel()

    def deactivate(self) -> None:
        self.disable()
        if self.host_api:
            unregister = getattr(self.host_api, "unregister_tool", None)
            if callable(unregister):
                unregister(self.TOOL_ID)
        self.host_api = None

    def on_pointer_down(
        self,
        pt: Any,
        *,
        alt: bool = False,
        shift: bool = False,
        ctrl: bool = False,
        meta: bool = False,
    ) -> None:
        del alt, shift, ctrl, meta
        if self.host_api is None or self._preparation.busy:
            return
        self._points = [tuple(pt.pos)]
        self._drawing = True
        self.host_api.input.pointer_capture(self.TOOL_ID)
        self._mask_preview.begin(self.host_api, self._points[0], self.size)

    def on_pointer_move(self, points: Iterable[Any]) -> None:
        if not self._drawing:
            return
        pending = [tuple(pt.pos) for pt in points]
        self._points.extend(pending)
        self._mask_preview.extend(pending)

    def on_pointer_up(self, pt: Any) -> None:
        if not self._drawing:
            return
        self._points.append(tuple(pt.pos))
        points = tuple(self._points)
        # Freeze the slider for this stroke before the worker starts.
        self._stroke_feather = self.feather
        self._cancel_stroke()
        self._preparation.prepare(points, self.size, self.radius)

    def on_pointer_hover(self, pt: Any) -> None:
        del pt

    def on_key(self, key: str, pressed: bool) -> bool:
        if pressed and key.lower() in {"escape", "esc"}:
            self._cancel_stroke()
            self._preparation.cancel()
            return True
        return False

    def on_wheel(self, delta: float, pt: Any) -> bool:
        del delta, pt
        return False

    def build_commands(self) -> list[Any]:
        return []

    def get_settings(self) -> dict[str, int]:
        return {"size": self.size, "radius": self.radius, "feather": self.feather}

    def set_settings(self, settings: Any) -> None:
        if isinstance(settings, dict):
            for key in ("size", "radius", "feather"):
                if key in settings:
                    self.set_setting(key, settings[key])

    def set_setting(self, key: str, value: Any) -> None:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return
        if key == "size":
            self.size = max(1, min(_MAX_SIZE, number))
            self._mask_preview.set_size(self.size)
        elif key == "radius":
            self.radius = max(1, min(_MAX_RADIUS, number))
        elif key == "feather":
            self.feather = max(0, min(MAX_FEATHER, number))

    def create_options_widget(self) -> None:
        return None

    def _process(self, pixels: np.ndarray, mask: np.ndarray, radius: int) -> np.ndarray:
        return claude_inpaint_pixels(pixels, mask, radius, feather=self._stroke_feather)

    def _cancel_stroke(self) -> None:
        self._mask_preview.clear()
        if self._drawing and self.host_api:
            self.host_api.input.release_capture(self.TOOL_ID)
        self._drawing = False
        self._points.clear()

    def _tool_registered(self, host: Any) -> bool:
        manager = getattr(host, "_tool_manager", None)
        if manager and hasattr(manager, "get_registered_tools"):
            return any(item.id == self.TOOL_ID for item in manager.get_registered_tools())
        pending = getattr(host, "_pending_tools", None)
        if isinstance(pending, list):
            return any(
                callable(getattr(item, "descriptor", None)) and item.descriptor().id == self.TOOL_ID
                for item in pending
            )
        return False


def create_plugin() -> ClaudeInpaintPenPlugin:
    return ClaudeInpaintPenPlugin()
