"""Capture detached AI Selection sources through existing host/render/readback paths."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

from PySide6.QtGui import QImage

from loomstroke.ai_selection.selection_bounds_source import SelectionBoundsSourceResolver
from loomstroke.ai_selection.selection_source_snapshot import _encode_qimage_png
from loomstroke.ai_selection.source_image_resolver import (
    Sam3AiSelectSourceImageError,
    resolve_ai_select_source_image,
)

from .composite_snapshot import composite_image
from .editing_common import edit_operation, invalid
from .extension_services import get_services
from .sam_asset_common import (
    MAX_AGGREGATE_MASK_PIXELS,
    asset_error,
    integer,
    output_id,
    provider_store,
)
from .types import McpStructuredError, ReasonCode

SOURCE_SCOPES = ("active_layer", "visible_composite", "selection_bounds", "page", "imported_asset")


async def _source_image(
    service: Any, arguments: Mapping[str, Any]
) -> tuple[QImage, dict[str, Any]]:
    scope = arguments.get("scope", "visible_composite")
    padding = integer(arguments.get("padding", 0), "padding", 0, 512)
    if scope not in SOURCE_SCOPES:
        raise invalid("'scope' is not a supported AI Selection source scope.", "scope")
    if scope != "selection_bounds" and padding:
        raise invalid("Padding applies to selection_bounds sources only.", "padding")
    if arguments.get("assetId") is not None and scope != "imported_asset":
        raise invalid("'assetId' requires imported_asset scope.", "assetId")
    if arguments.get("pageId") is not None and scope not in {"page", "visible_composite"}:
        raise invalid("'pageId' requires page or visible_composite scope.", "pageId")
    metadata: dict[str, Any] = {"source": "mcp_ai_select_source", "scope": scope}
    if scope == "imported_asset":
        asset_id = arguments.get("assetId")
        if not isinstance(asset_id, str) or not asset_id or len(asset_id) > 256:
            raise invalid("'assetId' must name a registered raster asset.", "assetId")
        if service._asset_record(asset_id) is None:
            raise McpStructuredError(ReasonCode.NOT_FOUND, "The imported asset is not registered.")
        image = QImage.fromData(service._asset_bytes(asset_id))
        metadata["assetId"] = asset_id
        return image, metadata
    if scope in {"visible_composite", "selection_bounds", "page"}:
        if scope == "page" and not arguments.get("pageId"):
            raise invalid("'pageId' is required for page scope.", "pageId")
        image = await composite_image(service, arguments)
        metadata["renderBackend"] = "wgpu"
        metadata["pageId"] = arguments.get("pageId") or service._active_page_id()
        if scope == "selection_bounds":
            image, mapping = SelectionBoundsSourceResolver(Sam3AiSelectSourceImageError).resolve(
                service.canvas_service, image, padding=padding
            )
            metadata["mapping"] = mapping.to_dict()
        return image, metadata
    host = getattr(service, "host_api", None) or SimpleNamespace(
        canvas=service.canvas_service, pages=service.page_service
    )
    resolved = resolve_ai_select_source_image(
        host,
        scope=scope,
        page_id=arguments.get("pageId"),
        render_page=get_services(service).get("render_page"),
    )
    metadata.update(
        {
            "documentId": resolved.document_id,
            "pageId": resolved.page_id,
            "layerId": resolved.layer_id,
            "dpi": resolved.dpi,
        }
    )
    return resolved.image, metadata


async def create_source_snapshot(
    service: Any, name: str, arguments: Mapping[str, Any]
) -> dict[str, Any]:
    _provider, store = provider_store(service)
    scope = arguments.get("scope", "visible_composite")
    async with edit_operation(
        service, name, arguments, require_canvas=scope != "imported_asset"
    ) as canvas:
        snapshot_id = output_id(store, arguments.get("outputAssetId"), source=True)
        layer_id = arguments.get("layerId")
        if layer_id is not None and (
            scope != "active_layer" or not isinstance(layer_id, str) or not layer_id
        ):
            raise invalid("'layerId' requires active_layer scope and a valid layer ID.", "layerId")
        previous_id = canvas.current_layer().id if canvas is not None else None
        previous_mask_flag = getattr(canvas, "_mask_edit_flag", None)
        try:
            if layer_id is not None:
                if canvas._get_layer_by_id(layer_id) is None:
                    raise McpStructuredError(
                        ReasonCode.NOT_FOUND, "The source layer was not found."
                    )
                canvas.set_active_layer(layer_id)
            image, metadata = await _source_image(service, arguments)
        except Sam3AiSelectSourceImageError as exc:
            raise asset_error(exc) from exc
        finally:
            if layer_id is not None and previous_id is not None:
                canvas.set_active_layer(previous_id)
                if previous_mask_flag is not None:
                    canvas._mask_edit_flag = previous_mask_flag
        if image.isNull() or image.width() * image.height() > MAX_AGGREGATE_MASK_PIXELS:
            raise invalid("The source must be a readable image with at most 64 MP.", "scope")
        try:
            payload, width, height = await asyncio.to_thread(_encode_qimage_png, image)
            source_ref = await asyncio.to_thread(
                store.save_source_snapshot,
                payload,
                snapshot_id=snapshot_id,
                kind=scope,
                width=width,
                height=height,
                document_id=metadata.get("documentId"),
                page_id=metadata.get("pageId"),
                layer_id=metadata.get("layerId"),
                dpi=metadata.get("dpi"),
                metadata=metadata,
            )
        except (OSError, ValueError) as exc:
            raise asset_error(exc) from exc
    return {
        "status": "completed",
        "sourceSnapshotId": source_ref.snapshot_id,
        "sourceRef": source_ref.to_dict(),
        "mapping": metadata.get("mapping"),
        "resourceUri": f"loomstroke://sam3/source-snapshot/{source_ref.snapshot_id}",
        "canvasChanged": False,
    }
