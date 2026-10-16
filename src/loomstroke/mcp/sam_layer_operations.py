"""Command-backed adapters for SAM extraction, clipping and candidate groups."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

from PySide6.QtGui import QImage

from loomstroke.ai_selection.candidate_group_layer_adapter import _candidate_layer_name
from loomstroke.ai_selection.cut_to_layer_adapter import _default_extract_layer_name

from .editing_common import current_revision, edit_operation, invalid
from .layer_edit_command import LayerEditCommand
from .layer_operations import _editable, _layer
from .sam_asset_common import (
    MAX_AGGREGATE_MASK_PIXELS,
    asset_error,
    dimensions,
    identifier,
    load,
    provider_store,
)
from .sam_layer_preparation import prepare_layer_adapter
from .types import McpStructuredError, ReasonCode


def raster_source(canvas: Any, layer_id: Any) -> Any:
    layer = _layer(canvas, layer_id)
    if layer.layer_type != "raster" or layer.image is None or layer.image.isNull():
        raise invalid("The source must be a readable raster layer.", "sourceLayerId")
    if layer.image.width() * layer.image.height() > MAX_AGGREGATE_MASK_PIXELS:
        raise invalid("The source layer exceeds the 64 MP limit.", "sourceLayerId")
    return layer


def _name(arguments: Mapping[str, Any]) -> str | None:
    value = arguments.get("name")
    if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 256):
        raise invalid("'name' must be a non-empty string up to 256 characters.", "name")
    return value


def _build(
    canvas: Any, store: Any, kind: str, args: Mapping[str, Any], width: int, height: int
) -> tuple[Any, dict[str, Any]]:
    layer_id = args.get("layerId") if kind == "apply_clipping_mask" else args.get("sourceLayerId")
    layer = raster_source(canvas, layer_id)
    _editable(canvas, layer)
    plan: dict[str, Any] = {"kind": kind, "mode": "foreground"}
    if kind == "create_candidate_group":
        candidate_set = load(store, "candidate_set", args.get("candidateSetId"), "candidateSetId")
        ids = args.get("candidateIds")
        if (
            not isinstance(ids, list)
            or not 2 <= len(ids) <= 32
            or any(not isinstance(value, str) for value in ids)
            or len(set(ids)) != len(ids)
        ):
            raise invalid(
                "'candidateIds' must contain 2-32 distinct candidate IDs.", "candidateIds"
            )
        safe_ids = [identifier(value, "candidateIds") for value in ids]
        by_id = {item.candidate_id: item for item in candidate_set.candidates}
        if any(value not in by_id for value in safe_ids):
            raise McpStructuredError(
                ReasonCode.NOT_FOUND, "A requested candidate is not in the candidate set."
            )
        candidates = [by_id[value] for value in safe_ids]
        if (
            max(width * height, layer.image.width() * layer.image.height()) * len(candidates)
            > MAX_AGGREGATE_MASK_PIXELS
        ):
            raise invalid(
                "The candidate group exceeds the 64 MP aggregate mask limit.", "candidateIds"
            )
        plan["masks"] = [
            (item.mask_asset_id, _candidate_layer_name(item.label, index))
            for index, item in enumerate(candidates, 1)
        ]
        plan["groupName"] = _name(args) or "SAM3 Candidates"
    else:
        asset = load(store, "mask_asset", args.get("maskAssetId"), "maskAssetId")
        if kind == "apply_clipping_mask":
            enabled = args.get("maskEnabled", True)
            if not isinstance(enabled, bool):
                raise invalid("'maskEnabled' must be boolean.", "maskEnabled")
            plan["maskEnabled"] = enabled
            plan["masks"] = [(asset.asset_id, "SAM3 Mask")]
        else:
            mode = args.get("mode", "foreground")
            if mode not in {"foreground", "background"}:
                raise invalid("'mode' must be foreground or background.", "mode")
            plan["mode"] = mode
            plan["masks"] = [(asset.asset_id, _name(args) or _default_extract_layer_name(mode))]
    return layer, plan


async def apply_layer_adapter(
    service: Any, name: str, arguments: Mapping[str, Any]
) -> dict[str, Any]:
    _provider, store = provider_store(service)
    kind = name.removeprefix("loomstroke_ai_select_")
    width, height = dimensions(service, {})
    async with edit_operation(service, name, arguments) as canvas:
        try:
            layer, plan = _build(canvas, store, kind, arguments, width, height)
            targets = [layer.id]
            revision = current_revision(service)
            source = SimpleNamespace(
                id=layer.id,
                image=QImage(layer.image),
                offset_x=layer.offset_x,
                offset_y=layer.offset_y,
            )
            adapter = await asyncio.to_thread(
                prepare_layer_adapter, store, source, plan, width, height
            )
            if service.canvas_service is not canvas or current_revision(service) != revision:
                raise McpStructuredError(
                    ReasonCode.CONFLICT, "The document changed while preparing SAM layer pixels."
                )
            _editable(canvas, layer)

            def operation(owner: Any) -> dict[str, Any]:
                if not adapter.apply(owner):
                    raise McpStructuredError(
                        ReasonCode.BACKEND_FAILED, "The SAM layer edit failed and was rolled back."
                    )
                created = list(getattr(adapter, "created_layer_ids", ()))
                created_id = getattr(adapter, "created_layer_id", None)
                group_id = getattr(adapter, "created_group_layer_id", None)
                if created_id:
                    created.append(created_id)
                command.target_ids.update(created)
                if group_id:
                    command.target_ids.add(group_id)
                return {"layerId": group_id or created_id or targets[0], "createdLayerIds": created}

            command = LayerEditCommand(targets, operation)
            if not canvas.submit(command):
                raise McpStructuredError(
                    ReasonCode.BACKEND_FAILED, "The SAM layer edit failed and was rolled back."
                )
        except (ValueError, OSError) as exc:
            raise asset_error(exc) from exc
    return {"status": "completed", **command.result, "canvasChanged": True, "undoable": True}
