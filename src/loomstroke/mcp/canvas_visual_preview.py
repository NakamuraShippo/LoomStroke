"""Low-resolution visual preview metadata for Canvas Agent changesets."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen

from .canvas_shape_preview import draw_changeset_shapes
from .safety import scrub_secrets
from .serialization import qimage_to_png_bytes

VISUAL_PREVIEW_SCHEMA_VERSION = "canvas_visual_preview.v1"
MAX_VISUAL_PREVIEW_DIMENSION = 256
MIN_VISUAL_PREVIEW_DIMENSION = 32

_PAYLOAD_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "binary",
        "bytes",
        "credential",
        "data",
        "fullresolutionimage",
        "full_resolution_image",
        "image",
        "mask",
        "pngbytes",
        "prompt",
        "providerrequestbody",
        "raw_lstroke",
        "rawbinary",
        "rawlstroke",
        "rawmask",
        "rawprompt",
        "requestbody",
        "sessiontoken",
        "token",
    }
)

AssetRegister = Callable[[bytes, Mapping[str, int], Mapping[str, Any]], str | None]


class CanvasAgentVisualPreviewer:
    """Build additive visual preview metadata without mutating the document."""

    def __init__(
        self,
        *,
        register_asset: AssetRegister | None,
        renderer_available: bool = True,
        max_dimension: int = MAX_VISUAL_PREVIEW_DIMENSION,
    ) -> None:
        self._register_asset = register_asset
        self._renderer_available = bool(renderer_available)
        self._max_dimension = max(
            MIN_VISUAL_PREVIEW_DIMENSION,
            min(MAX_VISUAL_PREVIEW_DIMENSION, int(max_dimension)),
        )

    def build(
        self,
        changeset: Mapping[str, Any],
        context: Mapping[str, Any],
        preview: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Return a `canvas_visual_preview.v1` artifact."""

        visual_preview_id = f"vp_{uuid.uuid4().hex[:12]}"
        warnings: list[dict[str, Any]] = []
        thumbnail = {
            "beforeAssetId": None,
            "afterAssetId": None,
            "diffAssetId": None,
        }
        thumbnail_asset_write = "none"

        if self._renderer_available and self._register_asset is not None:
            try:
                thumbnail = self._thumbnail_assets(
                    visual_preview_id,
                    changeset=changeset,
                    context=context,
                    preview=preview,
                )
                if any(thumbnail.values()):
                    thumbnail_asset_write = "lowres_runtime_asset"
            except Exception:  # pragma: no cover - defensive renderer boundary
                warnings.append(
                    {
                        "code": "visual_preview_metadata_fallback",
                    }
                )
        else:
            warnings.append(
                {
                    "code": "visual_preview_metadata_fallback",
                }
            )

        visual_preview = {
            "schemaVersion": VISUAL_PREVIEW_SCHEMA_VERSION,
            "visualPreviewId": visual_preview_id,
            "runId": changeset.get("runId"),
            "changesetId": changeset.get("changesetId") or preview.get("changesetId"),
            "baseDocumentRevision": (
                changeset.get("baseDocumentRevision") or context.get("contextDigest")
            ),
            "mode": "before_after_overlay",
            "thumbnail": thumbnail,
            "thumbnailAssetWrite": thumbnail_asset_write,
            "canvasOverlay": _canvas_overlay(changeset, context, preview),
            "candidateStack": _strip_payloads(preview.get("candidateStack") or []),
            "warnings": warnings,
            "privacy": {
                "payloadOmitted": True,
                "binaryPayloadOmitted": True,
                "fullResolutionPayloadIncluded": False,
                "rawMaskIncluded": False,
                "credentialValueIncluded": False,
            },
            "documentMutation": "none",
            "historyMutation": "none",
        }
        return scrub_secrets(_strip_payloads(visual_preview))

    def _thumbnail_assets(
        self,
        visual_preview_id: str,
        *,
        changeset: Mapping[str, Any],
        context: Mapping[str, Any],
        preview: Mapping[str, Any],
    ) -> dict[str, str | None]:
        before = _mapping(preview.get("before"))
        after = _mapping(preview.get("after"))
        before_image = _draw_stack_thumbnail(before, context, self._max_dimension)
        after_image = _draw_stack_thumbnail(after, context, self._max_dimension)
        from .panel_frame_preview import draw_frame_preview  # noqa: PLC0415

        draw_frame_preview(after_image, preview)
        diff_image = _draw_diff_thumbnail(before, after, context, self._max_dimension)
        if not bool(preview.get("blocked")):
            draw_changeset_shapes(after_image, changeset, context)
            draw_changeset_shapes(diff_image, changeset, context)
        return {
            "beforeAssetId": self._register_thumbnail(
                visual_preview_id,
                "before",
                before_image,
            ),
            "afterAssetId": self._register_thumbnail(
                visual_preview_id,
                "after",
                after_image,
            ),
            "diffAssetId": self._register_thumbnail(
                visual_preview_id,
                "diff",
                diff_image,
            ),
        }

    def _register_thumbnail(
        self,
        visual_preview_id: str,
        kind: str,
        image: QImage,
    ) -> str | None:
        register = self._register_asset
        if register is None:
            return None
        payload = qimage_to_png_bytes(image)
        dimensions = {"width": int(image.width()), "height": int(image.height())}
        return register(
            payload,
            dimensions,
            {
                "schemaVersion": VISUAL_PREVIEW_SCHEMA_VERSION,
                "visualPreviewId": visual_preview_id,
                "previewKind": kind,
                "lowResolution": True,
                "payloadOmittedFromResponse": True,
            },
        )


def build_visual_preview_metadata_fallback(
    changeset: Mapping[str, Any],
    context: Mapping[str, Any],
    preview: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a safe fallback artifact when the visual previewer itself fails."""

    visual_preview_id = f"vp_{uuid.uuid4().hex[:12]}"
    return scrub_secrets(
        _strip_payloads(
            {
                "schemaVersion": VISUAL_PREVIEW_SCHEMA_VERSION,
                "visualPreviewId": visual_preview_id,
                "runId": changeset.get("runId"),
                "changesetId": changeset.get("changesetId") or preview.get("changesetId"),
                "baseDocumentRevision": (
                    changeset.get("baseDocumentRevision") or context.get("contextDigest")
                ),
                "mode": "before_after_overlay",
                "thumbnail": {
                    "beforeAssetId": None,
                    "afterAssetId": None,
                    "diffAssetId": None,
                },
                "thumbnailAssetWrite": "none",
                "canvasOverlay": _canvas_overlay(changeset, context, preview),
                "candidateStack": _strip_payloads(preview.get("candidateStack") or []),
                "warnings": [
                    {
                        "code": "visual_preview_metadata_fallback",
                    }
                ],
                "privacy": {
                    "payloadOmitted": True,
                    "binaryPayloadOmitted": True,
                    "fullResolutionPayloadIncluded": False,
                    "rawMaskIncluded": False,
                    "credentialValueIncluded": False,
                },
                "documentMutation": "none",
                "historyMutation": "none",
            }
        )
    )


def strip_canvas_agent_payloads(value: Any) -> Any:
    """Remove prompt, secret-shaped, and binary payload keys from public artifacts."""

    return _strip_payloads(value)


def _canvas_overlay(
    changeset: Mapping[str, Any],
    context: Mapping[str, Any],
    preview: Mapping[str, Any],
) -> dict[str, Any]:
    target_layer_id = _target_layer_id(changeset, context)
    layers = _context_layers(context)
    return {
        "ghostLayers": _strip_payloads(preview.get("ghostLayers") or []),
        "targetHighlights": _target_highlights(target_layer_id, layers, context),
        "selectionHighlights": _selection_highlights(preview),
        "nonTargetLayerIds": [
            layer_id
            for layer_id in (_layer_id(layer) for layer in layers)
            if layer_id and layer_id != target_layer_id
        ],
    }


def _target_highlights(
    target_layer_id: str | None,
    layers: Sequence[Mapping[str, Any]],
    context: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if not target_layer_id:
        return []
    layer = next((item for item in layers if _layer_id(item) == target_layer_id), None)
    bounds = _layer_bounds(layer, context)
    return [
        {
            "layerId": target_layer_id,
            "mode": "target_scope",
            "bounds": bounds,
            "visible": bool((layer or {}).get("visible", True)),
            "locked": bool((layer or {}).get("locked", False)),
            "documentMutation": "none",
        }
    ]


def _selection_highlights(preview: Mapping[str, Any]) -> list[dict[str, Any]]:
    selection_preview = _mapping(preview.get("selectionPreview"))
    if not selection_preview:
        return []
    bounds = _bounds_tuple(selection_preview.get("bounds"))
    if bounds is None:
        return []
    return [
        {
            "selectionId": selection_preview.get("selectionId"),
            "mode": "scope_highlight",
            "bounds": bounds,
            "targetLayerId": selection_preview.get("targetLayerId"),
            "rawMaskIncluded": False,
            "documentMutation": "none",
        }
    ]


def _draw_stack_thumbnail(
    stack: Mapping[str, Any],
    context: Mapping[str, Any],
    max_dimension: int,
) -> QImage:
    width, height = _thumbnail_size(context, max_dimension)
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(250, 250, 250, 255))
    painter = QPainter(image)
    try:
        _draw_checkerboard(painter, width, height)
        layers = [
            layer
            for layer in stack.get("layers", [])
            if isinstance(layer, Mapping) and bool(layer.get("visible", True))
        ]
        if not layers:
            return image
        step = max(3, min(width, height) // (len(layers) + 3))
        for index, layer in enumerate(layers):
            inset = step * (len(layers) - index)
            rect = QRectF(
                inset,
                inset,
                max(4, width - inset * 2),
                max(4, height - inset * 2),
            )
            color = _layer_color(layer)
            opacity = max(0.08, min(1.0, float(layer.get("opacity", 1.0))))
            color.setAlphaF(min(0.9, opacity * (0.32 if layer.get("ghost") else 0.58)))
            painter.fillRect(rect, color)
            pen = QPen(QColor(38, 38, 38, 160))
            if layer.get("ghost") or layer.get("ghostMutation"):
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawRect(rect)
    finally:
        painter.end()
    return image


def _draw_diff_thumbnail(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    context: Mapping[str, Any],
    max_dimension: int,
) -> QImage:
    width, height = _thumbnail_size(context, max_dimension)
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(245, 245, 245, 255))
    painter = QPainter(image)
    try:
        _draw_checkerboard(painter, width, height)
        before_ids = {
            _layer_id(layer) for layer in before.get("layers", []) if isinstance(layer, Mapping)
        }
        after_layers = [
            layer
            for layer in after.get("layers", [])
            if isinstance(layer, Mapping) and _layer_id(layer)
        ]
        changed_layers = [
            layer
            for layer in after_layers
            if _layer_id(layer) not in before_ids
            or bool(layer.get("ghost"))
            or bool(layer.get("ghostMutation"))
        ]
        if not changed_layers:
            changed_layers = after_layers[-1:]
        margin = max(4, min(width, height) // 9)
        rect = QRectF(margin, margin, max(4, width - margin * 2), max(4, height - margin * 2))
        painter.fillRect(rect, QColor(255, 235, 120, 128))
        painter.setPen(QPen(QColor(90, 70, 20, 180), 2, Qt.PenStyle.DashLine))
        painter.drawRect(rect)
        for index, layer in enumerate(changed_layers[:4]):
            color = _layer_color(layer)
            color.setAlpha(180)
            stripe_height = max(3, height // 10)
            y = height - margin - (index + 1) * stripe_height
            painter.fillRect(QRectF(margin, y, width - margin * 2, stripe_height), color)
    finally:
        painter.end()
    return image


def _draw_checkerboard(painter: QPainter, width: int, height: int) -> None:
    square = max(4, min(width, height) // 8)
    colors = (QColor(235, 235, 235, 255), QColor(248, 248, 248, 255))
    for y in range(0, height, square):
        for x in range(0, width, square):
            painter.fillRect(x, y, square, square, colors[((x // square) + (y // square)) % 2])


def _thumbnail_size(context: Mapping[str, Any], max_dimension: int) -> tuple[int, int]:
    canvas = _mapping(context.get("canvas"))
    source_width = max(1, _int(canvas.get("width"), 1024))
    source_height = max(1, _int(canvas.get("height"), 768))
    scale = min(max_dimension / source_width, max_dimension / source_height, 1.0)
    width = max(MIN_VISUAL_PREVIEW_DIMENSION, round(source_width * scale))
    height = max(MIN_VISUAL_PREVIEW_DIMENSION, round(source_height * scale))
    return min(max_dimension, int(width)), min(max_dimension, int(height))


def _layer_color(layer: Mapping[str, Any]) -> QColor:
    text = str(layer.get("layerId") or layer.get("name") or "layer").encode("utf-8")
    digest = hashlib.sha256(text).digest()
    return QColor(80 + digest[0] % 140, 80 + digest[1] % 140, 80 + digest[2] % 140, 255)


def _layer_bounds(
    layer: Mapping[str, Any] | None,
    context: Mapping[str, Any],
) -> list[int]:
    if layer is not None:
        bounds = _bounds_tuple(layer.get("bounds"))
        if bounds is not None:
            return bounds
    canvas = _mapping(context.get("canvas"))
    return [0, 0, max(1, _int(canvas.get("width"), 1)), max(1, _int(canvas.get("height"), 1))]


def _target_layer_id(
    changeset: Mapping[str, Any],
    context: Mapping[str, Any],
) -> str | None:
    target = _mapping(changeset.get("target"))
    layer_id = str(target.get("layerId") or "").strip()
    if layer_id:
        return layer_id
    active = _mapping(context.get("activeLayer"))
    layer_id = str(active.get("layerId") or "").strip()
    return layer_id or None


def _context_layers(context: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    layers = context.get("layers")
    if not isinstance(layers, Sequence) or isinstance(layers, (str, bytes)):
        return []
    return [layer for layer in layers if isinstance(layer, Mapping)]


def _layer_id(layer: Mapping[str, Any]) -> str:
    return str(layer.get("layerId") or layer.get("id") or "").strip()


def _bounds_tuple(value: Any) -> list[int] | None:
    if isinstance(value, Mapping):
        try:
            return [
                int(value.get("x", 0)),
                int(value.get("y", 0)),
                int(value.get("width", 0)),
                int(value.get("height", 0)),
            ]
        except (TypeError, ValueError):
            return None
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) >= 4:
        try:
            return [int(value[0]), int(value[1]), int(value[2]), int(value[3])]
        except (TypeError, ValueError):
            return None
    return None


def _strip_payloads(value: Any) -> Any:  # noqa: PLR0911
    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            normalized_key = key_text.replace("-", "").replace("_", "").casefold()
            if normalized_key in _PAYLOAD_KEYS:
                continue
            cleaned[key_text] = _strip_payloads(item)
        return cleaned
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_strip_payloads(item) for item in value]
    if isinstance(value, bytes | bytearray | memoryview):
        return {"payloadOmitted": True}
    return value


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


__all__ = [
    "VISUAL_PREVIEW_SCHEMA_VERSION",
    "CanvasAgentVisualPreviewer",
    "build_visual_preview_metadata_fallback",
    "strip_canvas_agent_payloads",
]
