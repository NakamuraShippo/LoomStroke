"""Palette and swatch CRUD with the color manager's repository and file format."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from PySide6.QtCore import QObject
from shiboken6 import isValid

from loomstroke.plugins.color_manager.color_models import ColorPalette
from loomstroke.plugins.color_manager.errors import PaletteSettingsUpdateError
from loomstroke.plugins.color_manager.palette_repository import PaletteFormat

from .brush_tools import _schema
from .editing_common import edit_operation, invalid
from .extension_services import get_services, require_service
from .types import McpStructuredError, McpToolSpec, ReasonCode

logger = logging.getLogger(__name__)

_ACTIONS = [
    "list",
    "get",
    "upsert",
    "delete",
    "add_swatch",
    "update_swatch",
    "delete_swatch",
    "reorder",
    "import",
    "export",
]
TOOLS = (
    McpToolSpec(
        "loomstroke_palette",
        "Manage shared palettes/swatches, or import/export their existing JSON format. "
        "Palette color.rgba uses normalized channels 0..1. "
        "All mutations persist and refresh the Color panel.",
        _schema(
            {
                "action": {"enum": _ACTIONS},
                "paletteId": {"type": "string", "minLength": 1},
                "palette": {"type": "object"},
                "swatch": {"type": "object"},
                "swatchId": {"type": "string", "minLength": 1},
                "order": {"type": "array", "items": {"type": "string"}},
                "path": {"type": "string", "minLength": 1},
                "overwrite": {"type": "boolean"},
            },
            ["action"],
        ),
    ),
)


def _validate_palette(payload: Mapping[str, Any]) -> ColorPalette:
    if (
        not isinstance(payload.get("id"), str)
        or not payload["id"]
        or not isinstance(payload.get("name"), str)
    ):
        raise invalid("Palettes require nonempty id and a name.", "palette")
    swatches = payload.get("swatches", [])
    if not isinstance(swatches, list) or len(swatches) > 4096:
        raise invalid("Palette swatches must be a list of at most 4096 entries.")
    ids = set()
    for item in swatches:
        if (
            not isinstance(item, Mapping)
            or not isinstance(item.get("id"), str)
            or not item["id"]
            or item["id"] in ids
        ):
            raise invalid("Swatch IDs must be nonempty and unique.")
        ids.add(item["id"])
        color = item.get("color")
        rgba = color.get("rgba") if isinstance(color, Mapping) else None
        if (
            not isinstance(rgba, list)
            or len(rgba) != 4
            or any(
                isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= 1
                for value in rgba
            )
        ):
            raise invalid("Swatch color.rgba requires four normalized channels.")
    return ColorPalette.from_dict(dict(payload))


async def _refresh(service: Any, repository: Any, panel: Any) -> None:
    def current() -> bool:
        services = get_services(service)
        return (
            panel is not None
            and services.get("palette_repository") is repository
            and services.get("color_panel") is panel
            and (not isinstance(panel, QObject) or isValid(panel))
        )

    if current():
        palettes = await repository.list_palettes()
        if current():
            panel.set_palettes(palettes)


async def dispatch(service: Any, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    repository = require_service(service, "palette_repository")
    panel = get_services(service).get("color_panel")
    try:
        return await _dispatch_palette(service, name, arguments, repository, panel)
    except PaletteSettingsUpdateError:
        try:
            await _refresh(service, repository, panel)
        except Exception:
            logger.warning("Saved palette view could not be refreshed")
        raise McpStructuredError(
            ReasonCode.BACKEND_FAILED,
            "The palette file was saved, but its location setting could not be updated.",
            details={"file_committed": True, "settings_updated": False},
        ) from None


async def _dispatch_palette(  # noqa: PLR0912, PLR0915 - explicit repository action dispatch
    service: Any, name: str, arguments: Mapping[str, Any], repository: Any, panel: Any
) -> dict[str, Any]:
    action = arguments["action"]
    if action == "list":
        return {"palettes": [palette.to_dict() for palette in await repository.list_palettes()]}
    palette_id = arguments.get("paletteId")
    if action == "get":
        palette = await repository.get_palette(palette_id)
        if palette is None:
            raise McpStructuredError(ReasonCode.NOT_FOUND, "Palette was not found.")
        return {"palette": palette.to_dict()}
    path = None
    if action in {"import", "export"}:
        if "path" not in arguments:
            raise invalid("A path is required for palette import/export.")
        validator = (
            service.safety.validate_input_path
            if action == "import"
            else service.safety.validate_output_path
        )
        options = (
            {"overwrite": bool(arguments.get("overwrite", False))} if action == "export" else {}
        )
        path = validator(arguments["path"], allowed_suffixes=(".json",), **options)
        if action == "export" and path.exists() and not arguments.get("overwrite", False):
            raise McpStructuredError(ReasonCode.CONFLICT, "The palette output already exists.")
    async with edit_operation(service, name, arguments, require_canvas=False):
        if action == "import":
            palettes = await repository.import_file(path, fmt=PaletteFormat.JSON)
            palettes = [_validate_palette(palette.to_dict()) for palette in palettes]
            for palette in palettes:
                await repository.upsert_palette(palette)
        elif action == "export":
            path = service.safety.validate_output_path(
                path, allowed_suffixes=(".json",), overwrite=bool(arguments.get("overwrite", False))
            )
            if path.exists() and not arguments.get("overwrite", False):
                raise McpStructuredError(ReasonCode.CONFLICT, "The palette output already exists.")
            await repository.export_palette(palette_id, path)
        elif action == "upsert":
            await repository.upsert_palette(_validate_palette(arguments.get("palette", {})))
        else:
            palette = await repository.get_palette(palette_id)
            if palette is None:
                raise McpStructuredError(ReasonCode.NOT_FOUND, "Palette was not found.")
            if action == "delete":
                await repository.delete_palette(palette_id)
            else:
                payload = deepcopy(palette.to_dict())
                swatches = payload["swatches"]
                swatch_id = arguments.get("swatchId")
                if action == "add_swatch":
                    swatches.append(dict(arguments.get("swatch", {})))
                elif action in {"update_swatch", "delete_swatch"}:
                    index = next(
                        (i for i, swatch in enumerate(swatches) if swatch["id"] == swatch_id), None
                    )
                    if index is None:
                        raise McpStructuredError(ReasonCode.NOT_FOUND, "Swatch was not found.")
                    if swatches[index].get("locked"):
                        raise McpStructuredError(
                            ReasonCode.PERMISSION_DENIED, "The swatch is locked."
                        )
                    if action == "delete_swatch":
                        swatches.pop(index)
                    else:
                        update = dict(arguments.get("swatch", {}))
                        if "id" in update and update["id"] != swatch_id:
                            raise invalid("Swatch IDs cannot be changed.")
                        swatches[index].update(update)
                else:
                    order = arguments.get("order", [])
                    if len(order) != len(swatches) or set(order) != {
                        swatch["id"] for swatch in swatches
                    }:
                        raise invalid("Order must contain every swatch ID exactly once.")
                    payload["swatches"] = sorted(swatches, key=lambda item: order.index(item["id"]))
                await repository.upsert_palette(_validate_palette(payload))
        await _refresh(service, repository, panel)
        return {"status": "completed", "action": action, "path": str(path) if path else None}
