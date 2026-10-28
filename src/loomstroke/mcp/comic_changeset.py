"""Editable ComicPagePlan changeset helpers for LoomStroke MCP."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from loomstroke.core.commands import Command
from loomstroke.plugins.vector_tools.common import (
    Anchor,
    VectorObject,
    VectorPath,
    VectorStyle,
    denormalize_vector_object,
    ensure_vector_element_ids,
    normalize_rgb,
    normalize_vector_object,
)

from .comic_changeset_preflight import _comic_object_ids_available
from .comic_native_text import build_comic_native_text

COMIC_CHANGESET_OPERATION_KINDS = frozenset(
    {
        "create_vector_layer",
        "add_panel_vector_object",
        "add_balloon_vector_object",
        "add_text_object",
    }
)


class ComicApplyCommand(Command):
    """Apply editable comic vector/text operations as one undoable command."""

    vector_object = object()

    def __init__(self, operations: Sequence[Mapping[str, Any]]) -> None:
        super().__init__()
        self.operations = [dict(operation) for operation in operations]
        self._layer_id = "__loomstroke_comic_apply__"
        self._created_layer_ids: list[str] = []
        self._inserted_objects: list[tuple[str, int, str]] = []
        self.applied_records: list[dict[str, Any]] = []

    @property
    def created_layer_ids(self) -> list[str]:
        return list(self._created_layer_ids)

    @property
    def vector_object_ids(self) -> list[str]:
        return [item[2] for item in self._inserted_objects]

    def apply(self, canvas: Any) -> bool:
        if self._executed or not _comic_object_ids_available(canvas, self.operations, _get_layer):
            return False
        self._created_layer_ids = []
        self._inserted_objects = []
        self.applied_records = []
        layer_id_map: dict[str, str] = {}
        try:
            for operation in self.operations:
                kind = _operation_kind(operation)
                if kind == "create_vector_layer":
                    actual_id = self._apply_create_vector_layer(canvas, operation)
                    layer_id_map[str(operation.get("layerId") or actual_id)] = actual_id
                    continue
                if kind in {
                    "add_panel_vector_object",
                    "add_balloon_vector_object",
                    "add_text_object",
                } and not self._apply_add_vector_object(canvas, operation, layer_id_map):
                    self._rollback(canvas)
                    return False
            self._bump_texture_versions(canvas)
            self._executed = True
            return True
        except Exception:
            self._rollback(canvas)
            return False

    def revert(self, canvas: Any) -> bool:
        if not self._executed:
            return False
        self._rollback(canvas)
        self._executed = False
        return True

    def _apply_create_vector_layer(self, canvas: Any, operation: Mapping[str, Any]) -> str:
        requested_id = str(operation.get("layerId") or "").strip()
        name = str(operation.get("name") or "Comic Layer")
        vector_kind = str(operation.get("vectorKind") or "shape")
        existing = _get_layer(canvas, requested_id) if requested_id else None
        if existing is not None:
            return str(getattr(existing, "id", requested_id))

        restore = getattr(canvas, "restore_vector_layer", None)
        if callable(restore) and requested_id:
            ref = restore(layer_id=requested_id, name=name, vector_kind=vector_kind)
        else:
            add_layer = getattr(canvas, "add_vector_layer", None)
            ref = add_layer(name, vector_kind=vector_kind) if callable(add_layer) else None
        actual_id = str(getattr(ref, "id", "") or "")
        if not actual_id:
            raise RuntimeError("vector layer creation failed")
        self._created_layer_ids.append(actual_id)
        self.applied_records.append(
            {
                "kind": "create_vector_layer",
                "recordType": "layer",
                "recordId": actual_id,
                "vectorKind": vector_kind,
            }
        )
        return actual_id

    def _apply_add_vector_object(
        self,
        canvas: Any,
        operation: Mapping[str, Any],
        layer_id_map: Mapping[str, str],
    ) -> bool:
        add_vector = getattr(canvas, "add_vector_object", None)
        if not callable(add_vector):
            return False
        requested_layer_id = str(operation.get("layerId") or "")
        layer_id = layer_id_map.get(requested_layer_id, requested_layer_id)
        obj = normalize_vector_object(operation.get("object"))
        if obj is None:
            return False
        result = add_vector(obj, layer_id=layer_id, recompose=False)
        if result is None:
            return False
        inserted_layer_id, index = result
        object_id = str(getattr(obj, "id", ""))
        self._inserted_objects.append((str(inserted_layer_id), int(index), object_id))
        self.applied_records.append(
            {
                "kind": _operation_kind(operation),
                "recordType": "vector_object",
                "recordId": object_id,
                "layerId": str(inserted_layer_id),
            }
        )
        return True

    def _rollback(self, canvas: Any) -> None:
        remove_vector = getattr(canvas, "remove_vector_object", None)
        if callable(remove_vector):
            for layer_id, index, object_id in reversed(self._inserted_objects):
                remove_vector(layer_id, index, recompose=False, vec_id=object_id)
        remove_layer = getattr(canvas, "remove_layer", None)
        if callable(remove_layer):
            for layer_id in reversed(self._created_layer_ids):
                remove_layer(layer_id)
        self._inserted_objects = []
        self._created_layer_ids = []

    def _bump_texture_versions(self, canvas: Any) -> None:
        layer_ids = sorted({layer_id for layer_id, _, _ in self._inserted_objects})
        bump = getattr(canvas, "bump_layer_texture_versions", None)
        if callable(bump):
            bump(layer_ids)


def build_comic_changeset_operations(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Return editable PCT operation payloads for a ComicPagePlan."""

    page_digest = _stable_suffix(plan)
    shape_layer_id = f"comic_shape_{page_digest}"
    text_layer_id = f"comic_text_{page_digest}"
    operations: list[dict[str, Any]] = [
        {
            "kind": "create_vector_layer",
            "layerId": shape_layer_id,
            "name": "Comic Shapes",
            "vectorKind": "shape",
        },
        {
            "kind": "create_vector_layer",
            "layerId": text_layer_id,
            "name": "Comic Text",
            "vectorKind": "text",
        },
    ]
    warnings: list[str] = []
    panel_count = 0
    text_count = 0
    shape_count = 0
    style = _mapping(plan.get("style"))
    for panel_index, raw_panel in enumerate(_list(plan.get("panels"))):
        panel = _mapping(raw_panel)
        panel_id = str(panel.get("id") or f"panel_{panel_index + 1}")
        operations.append(
            _vector_operation(
                "add_panel_vector_object",
                shape_layer_id,
                _shape_object(
                    panel.get("rect"),
                    object_id=_object_id("panel", panel_id),
                    role="panel",
                    source_id=panel_id,
                    panel_id=panel_id,
                    fill_color=panel.get("background") or "#FFFDF8",
                    stroke_color=style.get("panelStrokeColor") or "#111111",
                    stroke_width=style.get("panelStrokeWidth") or 5,
                    radius=panel.get("borderRadius") or 4,
                ),
            )
        )
        panel_count += 1
        shape_count += 1
        for slot_index, raw_slot in enumerate(_list(panel.get("artSlots"))):
            slot = _mapping(raw_slot)
            if slot.get("assetId"):
                warnings.append("raster_art_slots_preview_only")
            placeholder = _mapping(slot.get("placeholder"))
            source_id = str(slot.get("id") or f"{panel_id}_art_{slot_index + 1}")
            operations.append(
                _vector_operation(
                    "add_panel_vector_object",
                    shape_layer_id,
                    _shape_object(
                        slot.get("rect"),
                        object_id=_object_id("art", source_id),
                        role="art_slot_placeholder",
                        source_id=source_id,
                        panel_id=panel_id,
                        fill_color=placeholder.get("fillColor") or "#DDEBFF",
                        stroke_color="#111111",
                        stroke_width=3,
                        radius=4,
                    ),
                )
            )
            shape_count += 1
        for raw_balloon in _list(panel.get("balloons")):
            balloon = _mapping(raw_balloon)
            source_id = str(balloon.get("id") or f"{panel_id}_balloon")
            operations.append(
                _vector_operation(
                    "add_balloon_vector_object",
                    shape_layer_id,
                    _shape_object(
                        balloon.get("rect"),
                        object_id=_object_id("balloon", source_id),
                        role="balloon",
                        source_id=source_id,
                        panel_id=panel_id,
                        fill_color=balloon.get("fillColor") or "#FFFFFF",
                        stroke_color=balloon.get("strokeColor") or "#111111",
                        stroke_width=balloon.get("strokeWidth") or 4,
                        radius=balloon.get("radius") or 10,
                    ),
                )
            )
            operations.append(_text_operation(text_layer_id, balloon, panel_id=panel_id))
            shape_count += 1
            text_count += 1
        for raw_caption in _list(panel.get("captions")):
            caption = _mapping(raw_caption)
            source_id = str(caption.get("id") or f"{panel_id}_caption")
            operations.append(
                _vector_operation(
                    "add_balloon_vector_object",
                    shape_layer_id,
                    _shape_object(
                        caption.get("rect"),
                        object_id=_object_id("caption_frame", source_id),
                        role="caption_frame",
                        source_id=source_id,
                        panel_id=panel_id,
                        fill_color=caption.get("fillColor") or "#FFF4A8",
                        stroke_color=caption.get("strokeColor") or "#111111",
                        stroke_width=caption.get("strokeWidth") or 3,
                        radius=caption.get("radius") or 8,
                    ),
                )
            )
            operations.append(_text_operation(text_layer_id, caption, panel_id=panel_id))
            shape_count += 1
            text_count += 1

    return {
        "operations": operations,
        "summary": {
            "panelCount": panel_count,
            "shapeObjectCount": shape_count,
            "textObjectCount": text_count,
            "layerCount": 2,
        },
        "warnings": sorted(set(warnings)),
    }


def is_comic_changeset_operation(operation: Any) -> bool:
    return _operation_kind(operation) in COMIC_CHANGESET_OPERATION_KINDS


def _vector_operation(kind: str, layer_id: str, obj: VectorObject) -> dict[str, Any]:
    normalized = ensure_vector_element_ids(obj)
    return {
        "kind": kind,
        "layerId": layer_id,
        "object": denormalize_vector_object(normalized),
    }


def _text_operation(layer_id: str, box: Mapping[str, Any], *, panel_id: str) -> dict[str, Any]:
    source_id = str(box.get("id") or f"{panel_id}_text")
    obj = build_comic_native_text(
        box,
        object_id=_object_id("text", source_id),
    ).object
    meta = dict(obj.meta)
    meta["comic"] = {
        "schemaVersion": "mcp.comic_vector_object.v1",
        "role": "text",
        "sourceId": source_id,
        "panelId": panel_id,
    }
    obj = VectorObject(
        id=obj.id,
        paths=obj.paths,
        style=obj.style,
        meta=meta,
        preview=obj.preview,
    )
    return _vector_operation("add_text_object", layer_id, obj)


def _shape_object(
    rect_value: Any,
    *,
    object_id: str,
    role: str,
    source_id: str,
    panel_id: str,
    fill_color: Any,
    stroke_color: Any,
    stroke_width: Any,
    radius: Any,
) -> VectorObject:
    x, y, width, height = _rect_tuple(rect_value)
    path = VectorPath(
        anchors=[
            Anchor((x, y)),
            Anchor((x + width, y)),
            Anchor((x + width, y + height)),
            Anchor((x, y + height)),
        ],
        closed=True,
    )
    return VectorObject(
        id=object_id,
        paths=[path],
        style=VectorStyle(
            stroke_color=_rgb(stroke_color, (17, 17, 17)),
            stroke_width=float(_number(stroke_width, 3.0)),
            fill_enabled=True,
            fill_color=_rgb(fill_color, (255, 255, 255)),
            join="round",
            cap="round",
        ),
        meta={
            "comic": {
                "schemaVersion": "mcp.comic_vector_object.v1",
                "role": role,
                "sourceId": source_id,
                "panelId": panel_id,
                "shape": "rounded_rect",
                "radius": float(_number(radius, 0.0)),
            }
        },
    )


def _operation_kind(operation: Any) -> str:
    if not isinstance(operation, Mapping):
        return ""
    return str(operation.get("kind") or operation.get("op") or "")


def _get_layer(canvas: Any, layer_id: str) -> Any | None:
    getter = getattr(canvas, "_get_layer_by_id", None)
    if callable(getter):
        return getter(layer_id)
    getter = getattr(canvas, "get_layer_by_id", None)
    if callable(getter):
        return getter(layer_id)
    return None


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _rect_tuple(value: Any) -> tuple[float, float, float, float]:
    rect = _mapping(value)
    return (
        float(_number(rect.get("x"), 0.0)),
        float(_number(rect.get("y"), 0.0)),
        max(1.0, float(_number(rect.get("width"), 1.0))),
        max(1.0, float(_number(rect.get("height"), 1.0))),
    )


def _rgb(value: Any, default: tuple[int, int, int]) -> tuple[int, int, int]:
    return normalize_rgb(value) or default


def _number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return default


def _stable_suffix(plan: Mapping[str, Any]) -> str:
    payload = json.dumps(plan, ensure_ascii=True, sort_keys=True, default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]


def _object_id(prefix: str, source_id: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in source_id).strip("_")
    digest = hashlib.sha1(f"{prefix}:{source_id}".encode()).hexdigest()[:8]
    return f"comic-{prefix}-{safe[:32] or 'item'}-{digest}"


__all__ = [
    "COMIC_CHANGESET_OPERATION_KINDS",
    "ComicApplyCommand",
    "build_comic_changeset_operations",
    "is_comic_changeset_operation",
]
