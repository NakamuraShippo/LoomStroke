"""CRUD and image/ABR import for the existing Basic Pen brush store."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from PySide6.QtGui import QImage, QImageReader

from loomstroke.core.commands import UpdateSelectionMaskCommand
from loomstroke.plugins.basic_pen.brush_store import _now_iso

from .brush_settings import validate_settings
from .brush_tools import _SETTINGS, _schema
from .composite_snapshot import composite_image
from .editing_common import edit_operation, invalid
from .extension_services import get_services, require_service
from .types import McpStructuredError, McpToolSpec, ReasonCode

TOOLS = (
    McpToolSpec(
        "loomstroke_brush_asset",
        "Manage Basic Pen brush assets: list/get, import tip image or ABR, "
        "create a tip from selection, update/delete and export tip PNG. "
        "Store and IDs use the existing Brush Manager.",
        _schema(
            {
                "action": {
                    "enum": [
                        "list",
                        "get",
                        "import_tip",
                        "import_abr",
                        "from_selection",
                        "update",
                        "delete",
                        "export_tip",
                    ]
                },
                "brushId": {"type": "string", "minLength": 1},
                "path": {"type": "string", "minLength": 1},
                "label": {"type": "string", "minLength": 1, "maxLength": 256},
                "settings": _SETTINGS,
                "overwrite": {"type": "boolean"},
            },
            ["action"],
            edit=True,
        ),
    ),
)


def brush_tip(store: Any, brush_id: Any) -> tuple[dict[str, Any], Path]:
    swatch = store.get_swatch(brush_id)
    if swatch is None:
        raise McpStructuredError(ReasonCode.NOT_FOUND, "Brush asset was not found.")
    path = store.resolve_tip_path(swatch.get("tip_path"))
    if path is None or not path.is_relative_to(store.root_dir.resolve()) or not path.is_file():
        raise invalid("The stored brush tip is missing or lies outside its brush store.")
    return swatch, path


def _read_tip(path: Path) -> QImage:
    reader = QImageReader(str(path))
    size = reader.size()
    if size.width() * size.height() > 4_194_304:
        raise invalid("A brush tip may contain at most 4,194,304 pixels.")
    image = reader.read()
    if image.isNull():
        raise invalid("The brush tip cannot be decoded.")
    return image


def _swatch(store: Any, path: Path, label: str, settings: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": f"b_{uuid.uuid4().hex[:16]}",
        "label": label,
        "tip_path": store.tip_path_relative(path.name),
        "params": {
            "size": 12.0,
            "opacity": 1.0,
            "hardness": 0.8,
            "flow": 1.0,
            "spacing": 0.12,
            "tone_range": 1.0,
            "tone_gamma": 0.6,
            **settings,
        },
        "meta": {"source": "native", "tags": ["mcp"], "created_at": _now_iso()},
    }


def _add_tip(store: Any, image: QImage, label: str, settings: Mapping[str, Any]) -> dict[str, Any]:
    store.ensure_dirs()
    path = store.brush_tips_dir / f"mcp_{uuid.uuid4().hex}.png"
    if not image.save(str(path), "PNG"):
        raise McpStructuredError(ReasonCode.BACKEND_FAILED, "Brush tip could not be saved.")
    swatch = _swatch(store, path, label, settings)
    store.add_swatch(swatch)
    return swatch


async def dispatch(  # noqa: PLR0912, PLR0915 - explicit brush-store action dispatch
    service: Any, name: str, arguments: Mapping[str, Any]
) -> dict[str, Any]:
    store = require_service(service, "brush_store")
    action = arguments["action"]
    if action == "list":
        return {"brushes": store.list_swatches()}
    if action == "get":
        brush, _ = brush_tip(store, arguments.get("brushId"))
        return {"brush": brush}
    settings = validate_settings(arguments.get("settings", {}), "basic_pen")
    path = None
    if action in {"import_tip", "import_abr"}:
        suffixes = (
            (".abr",)
            if action == "import_abr"
            else (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff")
        )
        path = service.safety.validate_input_path(
            arguments.get("path", ""), allowed_suffixes=suffixes
        )
    elif action == "export_tip":
        path = service.safety.validate_output_path(
            arguments.get("path", ""),
            allowed_suffixes=(".png",),
            overwrite=bool(arguments.get("overwrite", False)),
        )
    async with edit_operation(
        service, name, arguments, require_canvas=action == "from_selection"
    ) as canvas:
        before = deepcopy(store.load_db())
        brushes = []
        try:
            if action == "import_tip":
                image = _read_tip(path)
                brushes = [_add_tip(store, image, arguments.get("label", path.stem), settings)]
            elif action == "import_abr":
                from loomstroke.plugins.basic_pen.abr_importer import (  # noqa: PLC0415
                    ABRExtractionBudget,
                    ABRImporter,
                    ABRParseError,
                )

                if path.stat().st_size > 64 * 1024 * 1024:
                    raise invalid("ABR input exceeds 64 MiB.")
                store.ensure_dirs()
                destination = store.abr_cache_dir / f"mcp_{uuid.uuid4().hex}"
                try:
                    tips = await asyncio.to_thread(
                        ABRImporter().extract_tips,
                        str(path),
                        str(destination),
                        budget=ABRExtractionBudget(),
                    )
                except ABRParseError as exc:
                    raise invalid(str(exc), "path") from exc
                for index, tip_path in enumerate(tips):
                    brushes.append(
                        _add_tip(
                            store,
                            _read_tip(Path(tip_path)),
                            arguments.get("label", f"{path.stem} {index + 1}"),
                            settings,
                        )
                    )
            elif action == "from_selection":
                import numpy as np  # noqa: PLC0415

                mask, bounds = UpdateSelectionMaskCommand._read_selection(canvas)
                if mask is None or bounds is None:
                    raise invalid("Creating a brush tip requires a selection.")
                canvas_width, canvas_height = canvas.get_canvas_size()
                if canvas_width * canvas_height > 16_777_216:
                    raise invalid("Selection-to-tip supports at most 16,777,216 canvas pixels.")
                coverage = UpdateSelectionMaskCommand._mask_to_numpy(
                    mask, canvas_width, canvas_height
                )
                rows, columns = np.flatnonzero(coverage.any(axis=1)), np.flatnonzero(
                    coverage.any(axis=0)
                )
                if not len(rows) or not len(columns):
                    raise invalid("Creating a brush tip requires nonempty selection coverage.")
                x, y = int(columns[0]), int(rows[0])
                width, height = int(columns[-1] - x + 1), int(rows[-1] - y + 1)
                if width * height > 4_194_304:
                    raise invalid("The selected brush tip exceeds 4,194,304 pixels.")
                image = (await composite_image(service)).copy(x, y, width, height)
                image = image.convertToFormat(QImage.Format.Format_RGBA8888)
                pixels = np.frombuffer(image.bits(), np.uint8).reshape(height, width, 4)
                alpha = coverage[y : y + height, x : x + width]
                pixels[:, :, 3] = (
                    pixels[:, :, 3].astype(np.uint16) * alpha.astype(np.uint16) // 255
                ).astype(np.uint8)
                brushes = [_add_tip(store, image, arguments.get("label", "Selection"), settings)]
            else:
                brush, tip_path = brush_tip(store, arguments.get("brushId"))
                if action == "update":
                    if "label" in arguments:
                        store.rename_swatch(brush["id"], arguments["label"])
                    if "settings" in arguments:
                        store.update_swatch_params(brush["id"], {**brush["params"], **settings})
                elif action == "delete":
                    store.delete_swatch(brush["id"])
                else:
                    path = service.safety.validate_output_path(
                        path,
                        allowed_suffixes=(".png",),
                        overwrite=bool(arguments.get("overwrite", False)),
                    )
                    if not _read_tip(tip_path).save(str(path), "PNG"):
                        raise McpStructuredError(
                            ReasonCode.BACKEND_FAILED, "The brush tip export failed."
                        )
        except Exception:
            store._db = before
            store.save_db(before)
            raise
        providers = get_services(service)
        refresh = providers.get("brush_library_changed")
        panel = providers.get("brush_panel")
        if callable(refresh):
            refresh()
        elif panel is not None:
            panel.set_swatches(store.list_swatches())
        return {
            "status": "completed",
            "action": action,
            "brushes": brushes,
            "path": str(path) if path else None,
        }
