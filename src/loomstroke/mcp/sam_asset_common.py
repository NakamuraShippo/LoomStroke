"""Bounded, collision-safe asset access shared by MCP AI Selection tools."""

from __future__ import annotations

import re
import uuid
from typing import Any

from .editing_common import finite_number, invalid
from .types import McpStructuredError, ReasonCode

MAX_MASK_PIXELS = 16_777_216
MAX_AGGREGATE_MASK_PIXELS = 67_108_864
ID_SCHEMA = {"type": "string", "minLength": 1, "maxLength": 192, "pattern": "^[A-Za-z0-9_.-]+$"}


def identifier(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[A-Za-z0-9_.-]{1,192}", value)
        or value in {".", ".."}
    ):
        raise invalid(f"'{field}' must be a safe non-empty asset identifier.", field)
    return value


def integer(value: Any, field: str, low: int, high: int) -> int:
    number = finite_number(value, field, minimum=low, maximum=high)
    if int(number) != number:
        raise invalid(f"'{field}' must be an integer.", field)
    return int(number)


def dimensions(service: Any, arguments: Any) -> tuple[int, int]:
    canvas = getattr(service, "canvas_service", None)
    fallback = canvas.get_canvas_size() if canvas is not None else (None, None)
    width = integer(arguments.get("canvasWidth", fallback[0]), "canvasWidth", 1, 16384)
    height = integer(arguments.get("canvasHeight", fallback[1]), "canvasHeight", 1, 16384)
    if width * height > MAX_MASK_PIXELS:
        raise invalid("The mask canvas exceeds the 16 MP limit.", "canvasWidth")
    return width, height


def provider_store(service: Any) -> tuple[Any, Any]:
    provider = getattr(service, "sam3_provider", None)
    store = getattr(provider, "asset_store", None)
    if store is None:
        raise McpStructuredError(
            ReasonCode.CAPABILITY_UNAVAILABLE, "AI Selection asset storage is not connected."
        )
    return provider, store


def output_id(store: Any, value: Any, *, source: bool = False) -> str:
    asset_id = (
        identifier(value, "outputAssetId") if value is not None else "mcp-" + uuid.uuid4().hex
    )
    path = store.source_snapshot_path(asset_id) if source else store.mask_asset_path(asset_id)
    if path.parent.exists():
        raise McpStructuredError(
            ReasonCode.CONFLICT,
            "The output identifier already exists; choose a fresh ID.",
            details={"assetId": asset_id},
        )
    return asset_id


def load(store: Any, kind: str, value: Any, field: str) -> Any:
    asset_id = identifier(value, field)
    try:
        return getattr(store, "load_" + kind)(asset_id)
    except (OSError, ValueError) as exc:
        raise McpStructuredError(
            ReasonCode.NOT_FOUND,
            "The requested AI Selection asset could not be loaded.",
            details={"argument": field, "assetId": asset_id},
        ) from exc


def mask_result(asset: Any) -> dict[str, Any]:
    return {
        "status": "completed",
        "maskAssetId": asset.asset_id,
        "maskAsset": asset.to_dict(),
        "resourceUri": f"loomstroke://sam3/mask-asset/{asset.asset_id}",
        "canvasChanged": False,
    }


def asset_error(exc: Exception) -> McpStructuredError:
    code = str(getattr(exc, "code", "asset_operation_failed"))
    return McpStructuredError(
        ReasonCode.BACKEND_FAILED,
        "The AI Selection asset operation failed.",
        details={"backendCode": code, "errorType": type(exc).__name__},
    )


def schema(properties: Any, required: Any = ()) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }
