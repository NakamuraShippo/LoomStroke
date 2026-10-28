"""Serialization helpers for MCP resources, tools, and previews."""

from __future__ import annotations

import base64
import json
from dataclasses import asdict, is_dataclass, replace
from datetime import date, datetime
from enum import Enum
from io import BytesIO
from pathlib import Path
from typing import Any, cast

import numpy as np
from PIL import Image
from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage

from loomstroke.ui.canvas.render_scene import RenderLayerSnapshot, RenderSceneSnapshot

from .safety import scrub_secrets
from .snapshot_layer_payloads import layer_payload_changes, scene_payload_changes


def to_jsonable(value: Any) -> Any:  # noqa: PLR0911, PLR0912
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, QColor):
        return qcolor_to_rgba(value)
    if isinstance(value, QRect):
        return qrect_to_tuple(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.ndarray):
        return {
            "shape": list(value.shape),
            "dtype": str(value.dtype),
        }
    if isinstance(value, bytes):
        return {
            "encoding": "base64",
            "data": base64.b64encode(value).decode("ascii"),
        }
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [to_jsonable(item) for item in value]
    if hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            return to_jsonable(value.to_dict())
        except Exception:
            return repr(value)
    if is_dataclass(value):
        return to_jsonable(asdict(cast(Any, value)))
    if hasattr(value, "__dict__"):
        try:
            return to_jsonable(vars(value))
        except TypeError:
            return repr(value)
    return value


def dumps_json(value: Any) -> str:
    return json.dumps(scrub_secrets(to_jsonable(value)), ensure_ascii=False, sort_keys=True)


def qcolor_to_rgba(color: QColor) -> dict[str, int]:
    return {
        "r": int(color.red()),
        "g": int(color.green()),
        "b": int(color.blue()),
        "a": int(color.alpha()),
    }


def qrect_to_tuple(rect: QRect | None) -> tuple[int, int, int, int] | None:
    if rect is None or rect.isEmpty():
        return None
    return (int(rect.x()), int(rect.y()), int(rect.width()), int(rect.height()))


def qimage_to_rgba_array(image: QImage) -> np.ndarray:
    converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
    byte_length = converted.bytesPerLine() * converted.height()
    buffer = converted.bits()
    data_view = buffer if isinstance(buffer, memoryview) else memoryview(buffer)
    arr = np.frombuffer(data_view, dtype=np.uint8, count=byte_length).reshape(
        converted.height(), converted.bytesPerLine()
    )
    arr = arr[:, : converted.width() * 4]
    return arr.reshape(converted.height(), converted.width(), 4).copy()


def rgba_array_to_png_bytes(rgba: np.ndarray) -> bytes:
    arr = np.asarray(rgba, dtype=np.uint8)
    if arr.ndim != 3 or arr.shape[2] != 4:
        raise ValueError("preview image must be RGBA")
    out = BytesIO()
    Image.fromarray(arr, mode="RGBA").save(out, format="PNG")
    return out.getvalue()


def qimage_to_png_bytes(image: QImage) -> bytes:
    return rgba_array_to_png_bytes(qimage_to_rgba_array(image))


def png_content(png_bytes: bytes) -> dict[str, str]:
    return {
        "type": "image",
        "mimeType": "image/png",
        "data": base64.b64encode(png_bytes).decode("ascii"),
    }


def json_text_content(value: Any) -> dict[str, str]:
    return {
        "type": "text",
        "text": dumps_json(value),
    }


def copy_qimage(image: QImage | None) -> QImage | None:
    if image is None or image.isNull():
        return None
    return image.copy()


def detach_layer_snapshot(layer: RenderLayerSnapshot) -> RenderLayerSnapshot:
    return _detach_layer(layer, {})


def _detach_layer(layer, memo):
    if id(layer) in memo:
        return memo[id(layer)]
    detached = replace(layer, children=[], **layer_payload_changes(layer, memo))
    memo[id(layer)] = detached
    detached.children = [_detach_layer(child, memo) for child in layer.children]
    return detached


def detach_scene_snapshot(scene: RenderSceneSnapshot) -> RenderSceneSnapshot:
    memo = {}
    return replace(
        scene,
        layers=tuple(_detach_layer(layer, memo) for layer in scene.layers),
        flat_layers=tuple(_detach_layer(layer, memo) for layer in scene.flat_layers),
        frame_preview_source_layers=(
            tuple(_detach_layer(layer, memo) for layer in scene.frame_preview_source_layers)
            if scene.frame_preview_source_layers is not None
            else None
        ),
        **scene_payload_changes(scene, memo),
    )


def serialize_canvas_state(scene: RenderSceneSnapshot) -> dict[str, Any]:
    state = scene.canvas_state
    return {
        "width": int(state.width),
        "height": int(state.height),
        "dpi": int(state.dpi),
        "background": qcolor_to_rgba(state.background),
        "activeLayerId": state.active_layer_id,
        "activeIndex": int(state.active_index),
        "layerCacheVersion": int(state.layer_cache_version),
    }


def serialize_layer_snapshot(layer: RenderLayerSnapshot) -> dict[str, Any]:
    image = layer.image
    dimensions = None
    if image is not None and not image.isNull():
        dimensions = {"width": int(image.width()), "height": int(image.height())}
    return cast(
        dict[str, Any],
        scrub_secrets(
            {
                "id": layer.id,
                "name": layer.name,
                "visible": bool(layer.visible),
                "opacity": float(layer.opacity),
                "blendMode": to_jsonable(layer.blend_mode),
                "textureVersion": int(layer.texture_version),
                "transformVersion": int(layer.transform_version),
                "offset": {"x": int(layer.offset_x), "y": int(layer.offset_y)},
                "locked": bool(layer.locked),
                "alphaLock": bool(layer.alpha_lock),
                "layerType": layer.layer_type,
                "vectorKind": layer.vector_kind,
                "vectorObjectCount": len(layer.vector_objects),
                "parentId": layer.parent_id,
                "children": [serialize_layer_snapshot(child) for child in layer.children],
                "clipping": bool(layer.clipping),
                "maskEnabled": bool(layer.mask_enabled),
                "maskEditMode": layer.mask_edit_mode,
                "hasLayerMask": layer.layer_mask is not None,
                "effectType": layer.filter_type,
                "effectParams": to_jsonable(layer.filter_params),
                "hasEffectMask": layer.filter_mask is not None,
                "effectMaskTextureVersion": int(layer.filter_mask_texture_version),
                "aiEffect": to_jsonable(layer.ai_effect),
                "origin": to_jsonable(layer.origin),
                "imageDimensions": dimensions,
            }
        ),
    )


def serialize_selection(scene: RenderSceneSnapshot) -> dict[str, Any]:
    selection = scene.selection
    mask = selection.mask
    preview = selection.preview_image
    return {
        "selectionId": selection.selection_id,
        "hasSelection": bool(selection.has_selection),
        "bounds": qrect_to_tuple(selection.bounds),
        "maskDimensions": (
            {"width": int(mask.width()), "height": int(mask.height())}
            if mask is not None and not mask.isNull()
            else None
        ),
        "previewBounds": selection.preview_bounds,
        "previewDimensions": (
            {"width": int(preview.width()), "height": int(preview.height())}
            if preview is not None and not preview.isNull()
            else None
        ),
    }


def serialize_vector_state(scene: RenderSceneSnapshot) -> dict[str, Any]:
    vector = scene.vector
    return cast(
        dict[str, Any],
        scrub_secrets(
            {
                "previewLayerId": vector.preview_layer_id,
                "hasPreviewObject": vector.preview_object is not None,
                "selection": to_jsonable(vector.selection),
                "layers": [
                    {
                        "layerId": layer.layer_id,
                        "vectorKind": layer.vector_kind,
                        "vectorObjectCount": len(layer.vector_objects),
                    }
                    for layer in vector.layers
                ],
            }
        ),
    )


def serialize_scene_snapshot(scene: RenderSceneSnapshot) -> dict[str, Any]:
    return {
        "canvas": serialize_canvas_state(scene),
        "layers": [serialize_layer_snapshot(layer) for layer in scene.layers],
        "flatLayerIds": [layer.id for layer in scene.flat_layers],
        "parentIds": list(scene.parent_ids),
        "activeLayerId": scene.active_layer_id,
        "activeFlatIndex": int(scene.active_flat_index),
        "selection": serialize_selection(scene),
        "vector": serialize_vector_state(scene),
        "schema": {
            "name": "RenderSceneSnapshot",
            "version": 1,
            "transportDetached": True,
        },
    }
