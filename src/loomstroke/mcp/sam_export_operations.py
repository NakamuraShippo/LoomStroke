"""Detached transparent cutout and sprite-atlas exports through existing helpers."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

from PySide6.QtGui import QImage

from loomstroke.ai_selection.asset_export import (
    SpriteSheetFrame,
    build_sprite_atlas_payload,
    compose_sprite_sheet_image,
    encode_sprite_sheet_image,
    pack_sprite_sheet,
    trim_image_to_alpha_bbox,
    write_export_file,
)
from loomstroke.ai_selection.cut_to_layer_adapter import build_masked_layer_image
from loomstroke.ai_selection.layer_mask_adapter import build_layer_mask_image_from_asset

from .editing_common import edit_operation, invalid
from .sam_asset_common import (
    MAX_AGGREGATE_MASK_PIXELS,
    asset_error,
    dimensions,
    identifier,
    integer,
    load,
    provider_store,
)
from .sam_layer_operations import raster_source
from .types import McpStructuredError, ReasonCode


def _frames(
    store: Any, source: Any, ids: list[str], names: list[str], width: int, height: int, padding: int
) -> list[SpriteSheetFrame]:
    frames = []
    for asset_id, name in zip(ids, names, strict=True):
        mask = build_layer_mask_image_from_asset(
            store, asset_id, canvas_width=width, canvas_height=height
        )
        image = build_masked_layer_image(source, mask)
        trimmed = trim_image_to_alpha_bbox(image, padding_px=padding) if image is not None else None
        if trimmed is None:
            raise McpStructuredError(
                ReasonCode.CONFLICT,
                "The requested cutout contains no visible pixels.",
                details={"maskAssetId": asset_id},
            )
        image, bbox = trimmed
        frames.append(
            SpriteSheetFrame(name, image, bbox, (source.image.width(), source.image.height()))
        )
    return frames


def _write_pair(service: Any, path: Any, payload: bytes, atlas: bytes | None) -> None:
    # Revalidate immediately before each write; never overwrite a preexisting export.
    service.safety.validate_output_path(path, allowed_suffixes=(".png", ".webp"))
    write_export_file(path, payload)
    if atlas is not None:
        try:
            target = service.safety.validate_output_path(
                path.with_suffix(".json"), allowed_suffixes=(".json",)
            )
            write_export_file(target, atlas)
        except Exception:
            path.unlink(missing_ok=True)  # Only this transaction's newly created image.
            raise


async def export_assets(service: Any, name: str, args: Mapping[str, Any]) -> dict[str, Any]:
    _provider, store = provider_store(service)
    atlas_mode = name.endswith("export_sprite_atlas")
    path_value = args.get("outputPath")
    if not isinstance(path_value, str) or not path_value:
        raise invalid("'outputPath' must be a PNG or WebP output path.", "outputPath")
    path = service.safety.validate_output_path(path_value, allowed_suffixes=(".png", ".webp"))
    if atlas_mode:
        service.safety.validate_output_path(path.with_suffix(".json"), allowed_suffixes=(".json",))
    ids = args.get("maskAssetIds") if atlas_mode else [args.get("maskAssetId")]
    if not isinstance(ids, list) or not 1 <= len(ids) <= 64:
        raise invalid("'maskAssetIds' must contain 1-64 masks.", "maskAssetIds")
    ids = [identifier(value, "maskAssetIds") for value in ids]
    for value in ids:
        load(store, "mask_asset", value, "maskAssetIds")
    names = args.get("names", ids)
    if (
        not isinstance(names, list)
        or len(names) != len(ids)
        or any(not isinstance(value, str) or not value or len(value) > 192 for value in names)
        or len(set(names)) != len(names)
    ):
        raise invalid("'names' must contain one distinct non-empty name per mask.", "names")
    padding = integer(args.get("padding", 0), "padding", 0, 64)
    spacing = integer(args.get("spacing", 2), "spacing", 0, 64)
    max_side = integer(args.get("maxSide", 8192), "maxSide", 1, 8192)
    pot = args.get("powerOfTwo", False)
    if not isinstance(pot, bool):
        raise invalid("'powerOfTwo' must be boolean.", "powerOfTwo")
    width, height = dimensions(service, {})
    async with edit_operation(service, name, args) as canvas:
        layer = raster_source(canvas, args.get("sourceLayerId"))
        if (
            max(width * height, layer.image.width() * layer.image.height()) * len(ids)
            > MAX_AGGREGATE_MASK_PIXELS
        ):
            raise invalid("The export exceeds the 64 MP aggregate image limit.", "maskAssetIds")
        source = SimpleNamespace(
            image=QImage(layer.image), offset_x=layer.offset_x, offset_y=layer.offset_y
        )
        try:
            frames = await asyncio.to_thread(
                _frames, store, source, ids, names, width, height, padding
            )
            if atlas_mode:
                layout = await asyncio.to_thread(
                    pack_sprite_sheet,
                    frames,
                    spacing_px=spacing,
                    max_side_px=max_side,
                    power_of_two=pot,
                )
                image = await asyncio.to_thread(compose_sprite_sheet_image, layout)
                atlas = json.dumps(
                    build_sprite_atlas_payload(layout, image_name=path.name),
                    ensure_ascii=False,
                    indent=2,
                ).encode("utf-8")
                info = {
                    "sheetSize": list(layout.sheet_size),
                    "atlasPath": str(path.with_suffix(".json")),
                    "frameCount": len(frames),
                }
            else:
                image, atlas = frames[0].image, None
                info = {
                    "trimBBox": list(frames[0].trim_bbox),
                    "sourceSize": list(frames[0].source_size),
                }
            payload = await asyncio.to_thread(
                encode_sprite_sheet_image, image, image_format=path.suffix[1:]
            )
            await asyncio.to_thread(_write_pair, service, path, payload, atlas)
        except (OSError, ValueError) as exc:
            raise asset_error(exc) from exc
    return {"status": "completed", "outputPath": str(path), **info, "canvasChanged": False}
