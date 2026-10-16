"""Viewport and Reference Viewer controls, confined to the GUI thread."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from PySide6.QtGui import QImageReader

from .brush_tools import _schema
from .editing_common import edit_operation, invalid
from .extension_services import require_service
from .types import McpStructuredError, McpToolSpec, ReasonCode

TOOLS = (
    McpToolSpec(
        "loomstroke_view",
        "Read/set canvas viewport zoom, center, rotation; pan in widget pixels or fit the canvas.",
        _schema(
            {
                "action": {"enum": ["get", "set", "pan", "fit", "reset"]},
                "zoom": {"type": "number", "minimum": 0.01, "maximum": 32},
                "rotation": {"type": "number"},
                "center": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 2,
                    "maxItems": 2,
                },
                "delta": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 2,
                    "maxItems": 2,
                },
                "padding": {"type": "number", "minimum": 0, "maximum": 2},
            },
            ["action"],
        ),
    ),
    McpToolSpec(
        "loomstroke_reference",
        "Manage Reference Viewer images, active index and opacity. "
        "Imported images must lie inside configured roots.",
        _schema(
            {
                "action": {"enum": ["list", "add", "select", "remove", "opacity"]},
                "path": {"type": "string", "minLength": 1},
                "index": {"type": "integer", "minimum": 0},
                "opacity": {"type": "number", "minimum": 0, "maximum": 1},
            },
            ["action"],
        ),
    ),
)


def _references(panel: Any) -> dict[str, Any]:
    return {
        "images": [
            {"index": index, "name": label, "width": pixmap.width(), "height": pixmap.height()}
            for index, (label, pixmap) in enumerate(panel._images)
        ],
        "activeIndex": panel._current_index,
        "opacity": panel._view._opacity,
    }


async def dispatch(  # noqa: PLR0912, PLR0915 - dialog-free existing-widget action dispatch
    service: Any, name: str, arguments: Mapping[str, Any]
) -> dict[str, Any]:
    action = arguments["action"]
    target = require_service(
        service, "viewport" if name == "loomstroke_view" else "reference_viewer"
    )
    if action in {"get", "list"}:
        return target.get_state() if name == "loomstroke_view" else _references(target)
    async with edit_operation(service, name, arguments, require_canvas=False) as canvas:
        if name == "loomstroke_view":
            if action == "set":
                state = target.get_state()
                if "center" in arguments:
                    state["center_x"], state["center_y"] = arguments["center"]
                if "zoom" in arguments:
                    state["zoom"] = arguments["zoom"]
                if "rotation" in arguments:
                    if not target.state.allow_rotation:
                        raise McpStructuredError(ReasonCode.CONFLICT, "View rotation is disabled.")
                    state["rotation"] = arguments["rotation"] % 360
                target.set_state(state)
            elif action == "pan":
                if "delta" not in arguments:
                    raise invalid("Pan requires delta=[dx,dy].")
                target.pan(*arguments["delta"])
            elif action == "reset":
                target.reset()
            else:
                if canvas is None:
                    raise McpStructuredError(
                        ReasonCode.CAPABILITY_UNAVAILABLE, "Fit requires a canvas."
                    )
                target.fit_to_content(
                    *canvas.get_canvas_size(), padding=arguments.get("padding", 0.1)
                )
            return target.get_state()
        if action == "add":
            path = service.safety.validate_input_path(
                arguments.get("path", ""),
                allowed_suffixes=(".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"),
            )
            reader = QImageReader(str(path))
            size = reader.size()
            if size.width() * size.height() > 16_777_216:
                raise invalid("Reference images may contain at most 16,777,216 pixels.")
            image = reader.read()
            if image.isNull():
                raise invalid("The reference image cannot be decoded.")
            target.add_image(Path(path).name, image)
        elif action == "opacity":
            if "opacity" not in arguments:
                raise invalid("Opacity is required.")
            target._opacity_slider.setValue(round(arguments["opacity"] * 100))
        else:
            index = arguments.get("index", target._current_index)
            if not 0 <= index < len(target._images):
                raise McpStructuredError(ReasonCode.NOT_FOUND, "Reference index was not found.")
            target._current_index = index
            if action == "remove":
                target._on_remove()
            else:
                target._show_current()
        return _references(target)
