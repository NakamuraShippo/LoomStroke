from __future__ import annotations

import json
from typing import Any

MIME_TYPE = "application/x-loomstroke-shape+json"


def build_payload(
    *,
    plugin_id: str,
    insert_policy: str,
    default_size_px: int,
    keep_aspect: bool,
    shape_id: str | None = None,
    basic_shape_id: str | None = None,
    kind: str | None = None,
) -> str:
    payload_kind = kind or ("basic" if basic_shape_id else "library")
    payload = {
        "v": 1,
        "plugin_id": plugin_id,
        "kind": payload_kind,
        "insert_policy": insert_policy,
        "default_size_px": int(default_size_px),
        "keep_aspect": bool(keep_aspect),
    }
    if shape_id:
        payload["shape_id"] = shape_id
    if basic_shape_id:
        payload["basic_shape_id"] = basic_shape_id
    return json.dumps(payload)


def parse_payload(payload_json: str) -> dict[str, Any] | None:  # noqa: PLR0911
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("v") != 1:
        return None
    if "plugin_id" not in payload:
        return None
    kind = payload.get("kind")
    if kind is None:
        kind = "basic" if "basic_shape_id" in payload else "library"
        payload["kind"] = kind
    if kind == "basic":
        if "basic_shape_id" not in payload:
            return None
    elif "shape_id" not in payload:
        return None
    return payload
