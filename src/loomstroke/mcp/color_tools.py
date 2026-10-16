"""Shared painting color and eyedropper tools through the existing HostAPI."""

from __future__ import annotations

from collections.abc import Mapping
from math import floor
from typing import Any

from loomstroke.core.color_state import ColorDescriptor
from loomstroke.core.command_layer_access import _layer_transform

from .brush_settings import color_rgba
from .brush_tools import _SETTING_PROPERTIES, _schema
from .color_command import ColorEditCommand
from .composite_snapshot import composite_image
from .editing_common import edit_operation, invalid
from .editing_readiness import known_document_closed
from .types import McpStructuredError, McpToolSpec, ReasonCode

TOOLS = (
    McpToolSpec(
        "loomstroke_color_state",
        "Read shared active color, previous color and history.",
        _schema({}),
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_set_active_color",
        "Set the shared painting color through its existing command and history.",
        _schema({"color": _SETTING_PROPERTIES["color"]}, ["color"], edit=True),
    ),
    McpToolSpec(
        "loomstroke_eyedropper",
        "Sample RGBA8 at a canvas coordinate from the visible composite or a raster layer; "
        "optionally set active color.",
        _schema(
            {
                "x": {"type": "integer", "minimum": 0},
                "y": {"type": "integer", "minimum": 0},
                "source": {"enum": ["merged", "layer"]},
                "layerId": {"type": "string", "minLength": 1},
                "setActive": {"type": "boolean"},
            },
            ["x", "y"],
            edit=True,
        ),
    ),
)


def _host(service: Any) -> Any:
    host = service.host_api
    if host is None or not callable(getattr(host, "get_color_snapshot", None)):
        raise McpStructuredError(
            ReasonCode.CAPABILITY_UNAVAILABLE, "Shared color state is unavailable."
        )
    return host


def _descriptor(color: Any) -> dict[str, Any] | None:
    return {"rgba": list(color.rgba), "blendMode": color.blend_mode} if color is not None else None


def _state(host: Any) -> dict[str, Any]:
    snapshot = host.get_color_snapshot()
    return {
        "current": _descriptor(snapshot.current),
        "previous": _descriptor(snapshot.previous),
        "history": [_descriptor(color) for color in snapshot.history],
    }


async def dispatch(service: Any, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    host = _host(service)
    if name == "loomstroke_color_state":
        return _state(host)
    async with edit_operation(
        service, name, arguments, require_canvas=name.endswith("eyedropper")
    ) as canvas:
        if name.endswith("set_active_color"):
            rgba = color_rgba(arguments["color"])
            rgba = (*rgba, 255) if len(rgba) == 3 else rgba
        else:
            x, y = arguments["x"], arguments["y"]
            width, height = canvas.get_canvas_size()
            if x >= width or y >= height:
                raise invalid("The sample coordinate lies outside the canvas.")
            if arguments.get("source", "merged") == "layer":
                layer = canvas._get_layer_by_id(arguments.get("layerId"))
                if layer is None or layer.image is None:
                    raise McpStructuredError(
                        ReasonCode.NOT_FOUND, "The raster sample layer was not found."
                    )
                inverse = _layer_transform(layer).to_affine().inverse()
                if inverse is None:
                    raise invalid("The sample layer transform is not invertible.")
                local_x, local_y = inverse.apply(x + 0.5, y + 0.5)
                lx, ly = floor(local_x), floor(local_y)
                color = (
                    layer.image.pixelColor(lx, ly)
                    if 0 <= lx < layer.image.width() and 0 <= ly < layer.image.height()
                    else None
                )
            else:
                image = await composite_image(service)
                color = image.pixelColor(x, y)
            rgba = color.getRgb() if color is not None else (0, 0, 0, 0)
        if name.endswith("set_active_color") or arguments.get("setActive", True):
            descriptor = ColorDescriptor(tuple(channel / 255 for channel in rgba))
            if canvas is None or known_document_closed(service):
                host.set_active_color(descriptor, source="mcp")
            elif host.color_state.current != descriptor and not canvas.submit(
                ColorEditCommand(host, descriptor)
            ):
                raise McpStructuredError(
                    ReasonCode.BACKEND_FAILED, "The shared color edit could not be applied."
                )
        result = _state(host)
        result["rgba"] = list(rgba)
        return result
