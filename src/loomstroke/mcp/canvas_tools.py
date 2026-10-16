"""Canvas/image geometry and ICC assignment using command-backed services."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from PySide6.QtGui import QColor

from loomstroke.io.color_profile_registry import ColorProfileRegistry
from loomstroke.io.file_io import ColorProfile

from .canvas_property_command import CanvasPropertyCommand
from .editing_common import current_revision, edit_operation, invalid
from .paint_extension_arguments import ID, action_schema, validate
from .serialization import to_jsonable
from .types import McpToolSpec

DIMENSION = {"type": "integer", "minimum": 1, "maximum": 16384}
DPI = {"type": "integer", "minimum": 1, "maximum": 9600}
COLOR = {"type": "string", "pattern": "^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$"}
TOOLS = (
    McpToolSpec(
        "loomstroke_manage_canvas",
        "Read canvas dimensions/DPI/background/ICC; resize (crop/extend with anchor), "
        "resample, set DPI/background, flip or rotate the entire image. All changes are "
        "undoable. Geometry supports dimensions 1-16384 and the GUI's interpolation modes.",
        action_schema(
            {
                "get": ({}, ()),
                "resize": (
                    {
                        "width": DIMENSION,
                        "height": DIMENSION,
                        "anchor": {
                            "type": "array",
                            "items": {"type": "number", "minimum": 0, "maximum": 1},
                            "minItems": 2,
                            "maxItems": 2,
                        },
                    },
                    ("width", "height"),
                ),
                "resample": (
                    {
                        "width": DIMENSION,
                        "height": DIMENSION,
                        "dpi": DPI,
                        "mode": {
                            "enum": ["nearest", "bilinear", "bicubic", "lanczos", "hamming", "box"]
                        },
                    },
                    ("width", "height"),
                ),
                "set_dpi": ({"dpi": DPI}, ("dpi",)),
                "set_background": ({"color": COLOR}, ("color",)),
                "flip": ({"horizontal": {"type": "boolean"}, "vertical": {"type": "boolean"}}, ()),
                "rotate": ({"angle": {"enum": [90, -90, 180]}}, ("angle",)),
            }
        ),
    ),
    McpToolSpec(
        "loomstroke_canvas_profile",
        "List validated ICC profiles including built-in sRGB; register .icc/.icm bytes from "
        "MCP roots (maximum 16 MiB); assign a registered RGB profile or clear tagging. "
        "Assignment is metadata-only, preserves pixels, and is undoable.",
        action_schema(
            {
                "list": ({}, ()),
                "register": ({"path": ID}, ("path",)),
                "assign": (
                    {"profileId": {"type": ["string", "null"], "minLength": 1}},
                    ("profileId",),
                ),
            }
        ),
    ),
)


def _registry(service: Any) -> ColorProfileRegistry:
    registry = getattr(service, "_mcp_color_profiles", None)
    if registry is None:
        registry = ColorProfileRegistry()
        registry.srgb()
        service._mcp_color_profiles = registry
    current = getattr(service.canvas_service, "color_profile", None)
    if current is not None:
        try:
            registry.register(current.data)
        except ValueError as exc:
            raise invalid("The current working ICC profile is invalid.", "profileId") from exc
    return registry


def _config(service: Any, canvas: Any) -> dict[str, Any]:
    profile = getattr(canvas, "color_profile", None)
    registry = _registry(service)
    return {
        "width": canvas._width,
        "height": canvas._height,
        "dpi": canvas._dpi,
        "background": to_jsonable(canvas._background),
        "profile": to_jsonable(registry.register(profile.data)) if profile else None,
        "revision": current_revision(service),
    }


async def dispatch(service: Any, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = validate(TOOLS, name, arguments)
    if name == "loomstroke_canvas_profile":
        return await _profile(service, name, args)
    canvas = service.canvas_service
    if args["action"] == "get":
        if canvas is None:
            raise invalid("An open canvas is required.")
        return _config(service, canvas)
    async with edit_operation(service, name, args) as canvas:
        # Validate response metadata before a command can alter the document.
        _registry(service)
        before = canvas.history_undo_count()
        action = args["action"]
        if action == "resize":
            canvas.update_canvas_config(
                width=args["width"],
                height=args["height"],
                anchor=tuple(args.get("anchor", (0.5, 0.5))),
            )
        elif action in {"resample", "set_dpi"}:
            succeeded = canvas.resample_canvas(
                new_width=args.get("width", canvas._width),
                new_height=args.get("height", canvas._height),
                dpi=args.get("dpi"),
                mode=args.get("mode", "bicubic"),
            )
            if not succeeded:
                raise invalid("The image size operation could not be applied.")
        elif action == "set_background":
            text = args["color"]
            color = QColor(
                int(text[1:3], 16),
                int(text[3:5], 16),
                int(text[5:7], 16),
                int(text[7:9], 16) if len(text) == 9 else 255,
            )
            canvas._simple_layer_command_submit().submit(
                CanvasPropertyCommand("background", color), "Canvas Background"
            )
        elif action == "flip":
            canvas.flip_image(
                horizontal=args.get("horizontal", False), vertical=args.get("vertical", False)
            )
        else:
            canvas.rotate_image(angle=args["angle"])
        return {
            "status": "applied" if canvas.history_undo_count() > before else "noop",
            **_config(service, canvas),
        }


async def _profile(service: Any, name: str, args: dict[str, Any]) -> dict[str, Any]:
    registry = _registry(service)
    if args["action"] == "list":
        return {"profiles": to_jsonable(registry.list_profiles())}
    async with edit_operation(service, name, args) as canvas:
        registry = _registry(service)
        if args["action"] == "register":
            path = service.safety.validate_input_path(
                args["path"], allowed_suffixes=(".icc", ".icm")
            )
            if path.stat().st_size > 16_777_216:
                raise invalid("ICC profiles must not exceed 16 MiB.", "path")
            with path.open("rb") as stream:
                payload = stream.read(16_777_217)
            if len(payload) > 16_777_216:
                raise invalid("ICC profiles must not exceed 16 MiB.", "path")
            try:
                profile = registry.register(payload)
            except ValueError as exc:
                raise invalid("The file is not a valid ICC profile.", "path") from exc
            return {"profile": to_jsonable(profile)}
        profile_id = args["profileId"]
        profile = None
        if profile_id is not None:
            info = registry.info(profile_id)
            if info is None or info.colorspace != "RGB":
                raise invalid("A registered RGB profile is required.", "profileId")
            profile = ColorProfile(info.name, registry.profile_bytes(profile_id))
        changed = canvas._simple_layer_command_submit().submit(
            CanvasPropertyCommand("profile", profile), "Assign ICC Profile"
        )
        return {"status": "applied" if changed else "noop", **_config(service, canvas)}
