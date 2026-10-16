"""Host-side source image resolution for SAM3 AI Select runs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from .page_source import PageSourceResolver
from .sam3_source_mapping import Sam3SourceMapping
from .sam3_source_readiness import Sam3SourceReadiness
from .selection_bounds_source import SelectionBoundsSourceResolver
from .selection_bridge import Sam3SelectionScope

SAM3_AI_SELECT_SOURCE_IMAGE_SCHEMA_VERSION = "sam3.ai_select_source_image.v1"

_ACTIVE_LAYER_SOURCE_SCOPE = "active_layer"
_COMPOSITE_SOURCE_SCOPE = "visible_composite"
_SELECTION_BOUNDS_SOURCE_SCOPE = "selection_bounds"
_PAGE_SOURCE_SCOPE = "page"
# 後方互換 alias (旧名は composite 1 種しかなかった頃の名残)
_SUPPORTED_SOURCE_SCOPE = _COMPOSITE_SOURCE_SCOPE
# CT-23 Phase A/B: selection_bounds (AABB + padding) と explicit page を追加。
SAM3_AI_SELECT_SUPPORTED_SOURCE_SCOPES = (
    _ACTIVE_LAYER_SOURCE_SCOPE,
    _COMPOSITE_SOURCE_SCOPE,
    _SELECTION_BOUNDS_SOURCE_SCOPE,
    _PAGE_SOURCE_SCOPE,
)
SAM3_AI_SELECT_MAX_SOURCE_PIXELS = 64 * 1024 * 1024


class Sam3AiSelectSourceImageError(ValueError):
    """Raised when a SAM3 AI Select source image cannot be resolved."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Sam3AiSelectResolvedSourceImage:
    """Detached source image plus metadata for one AI Select run."""

    image: object
    scope: Sam3SelectionScope
    width: int
    height: int
    document_id: str | None = None
    page_id: str | None = None
    layer_id: str | None = None
    dpi: int | None = None
    available_layer_ids: tuple[str, ...] = ()
    schema_version: str = SAM3_AI_SELECT_SOURCE_IMAGE_SCHEMA_VERSION
    mapping: Sam3SourceMapping | None = None

    @property
    def crop_bounds(self) -> tuple[int, int, int, int] | None:
        return self.mapping.crop_bounds if self.mapping is not None else None

    def to_run_kwargs(self) -> dict[str, object]:
        return {
            "image": self.image,
            "document_id": self.document_id,
            "page_id": self.page_id,
            "layer_id": self.layer_id,
            "dpi": self.dpi,
            "available_layer_ids": self.available_layer_ids,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "scope": self.scope,
            "width": self.width,
            "height": self.height,
            "documentId": self.document_id,
            "pageId": self.page_id,
            "layerId": self.layer_id,
            "dpi": self.dpi,
            "availableLayerIds": list(self.available_layer_ids),
            "mapping": self.mapping.to_dict() if self.mapping is not None else None,
        }


def resolve_ai_select_source_image(
    host_api: object,
    *,
    scope: str = _SUPPORTED_SOURCE_SCOPE,
    page_id: str | None = None,
    padding: int = 0,
    render_page: object | None = None,
) -> Sam3AiSelectResolvedSourceImage:
    """Resolve the run source image as a detached QImage.

    ``active_layer`` reads only the active layer's pixels onto a transparent
    canvas-size image (2026-07-13: SAM3 の対象は選択レイヤーに限定);
    ``visible_composite`` keeps the legacy whole-composite readback. The
    resolver intentionally uses the existing canvas readback surface by
    duck-typing instead of expanding HostAPI in this slice.
    """

    normalized_scope = _normalize_source_scope(scope)
    canvas = getattr(host_api, "canvas", None)
    if canvas is None:
        raise Sam3AiSelectSourceImageError(
            "ai_select_canvas_missing",
            "SAM3 AI Select source resolver requires a host canvas.",
        )

    layer_id: str | None = None
    mapping: Sam3SourceMapping | None = None
    resolved_page_id = _resolve_page_id(host_api)
    if normalized_scope == _ACTIVE_LAYER_SOURCE_SCOPE:
        image, layer_id = _resolve_active_layer_qimage(canvas)
    elif normalized_scope == _SELECTION_BOUNDS_SOURCE_SCOPE:
        composite = _resolve_visible_composite_qimage(canvas)
        image, mapping = SelectionBoundsSourceResolver(Sam3AiSelectSourceImageError).resolve(
            canvas, composite, padding=padding
        )
    elif normalized_scope == _PAGE_SOURCE_SCOPE:
        image, resolved_page_id = PageSourceResolver(
            Sam3AiSelectSourceImageError, render_page=render_page  # type: ignore[arg-type]
        ).resolve(host_api, page_id, _resolve_visible_composite_qimage)
    else:
        image = _resolve_visible_composite_qimage(canvas)
    if int(image.width()) * int(image.height()) > SAM3_AI_SELECT_MAX_SOURCE_PIXELS:
        raise Sam3AiSelectSourceImageError(
            "ai_select_source_oversize",
            "SAM3 AI Select source image exceeds the pixel cap.",
        )
    if mapping is None:
        mapping = Sam3SourceMapping(0, 0, int(image.width()), int(image.height()))
    return Sam3AiSelectResolvedSourceImage(
        image=image,
        scope=normalized_scope,
        width=int(image.width()),
        height=int(image.height()),
        document_id=_resolve_document_id(canvas),
        page_id=resolved_page_id,
        layer_id=layer_id,
        dpi=_resolve_dpi(canvas),
        available_layer_ids=_resolve_available_layer_ids(canvas),
        mapping=mapping,
    )


def describe_ai_select_source(
    host_api: object,
    *,
    scope: str,
    page_id: str | None = None,
    padding: int = 0,
) -> Sam3SourceReadiness:
    """Pre-flight a scope without encoding a source PNG (CT-23 §7.1).

    Unsupported scopes are reported, never silently mapped to the composite.
    """
    raw = str(scope).strip()
    if raw not in SAM3_AI_SELECT_SUPPORTED_SOURCE_SCOPES:
        return Sam3SourceReadiness(raw, False, ("source_scope_unavailable",))
    canvas = getattr(host_api, "canvas", None)
    if canvas is None:
        return Sam3SourceReadiness(raw, False, ("ai_select_canvas_missing",))
    try:
        resolved = resolve_ai_select_source_image(
            host_api, scope=raw, page_id=page_id, padding=padding
        )
    except Sam3AiSelectSourceImageError as exc:
        return Sam3SourceReadiness(raw, False, (exc.code,), page_id=page_id)
    return Sam3SourceReadiness(
        raw,
        True,
        (),
        resolved.width,
        resolved.height,
        resolved.page_id,
        resolved.layer_id,
        resolved.crop_bounds,
    )


def normalize_ai_select_supported_source_scope(scope: str) -> Sam3SelectionScope:
    """Return the currently executable host-source scope.

    AI Select settings preserve future scope values, but the host readback path
    only has a real implementation for the visible composite today.
    """

    raw = str(scope).strip()
    if raw in SAM3_AI_SELECT_SUPPORTED_SOURCE_SCOPES:
        return cast("Sam3SelectionScope", raw)
    return cast("Sam3SelectionScope", _SUPPORTED_SOURCE_SCOPE)


def _normalize_source_scope(scope: str) -> Sam3SelectionScope:
    raw = str(scope).strip()
    if raw in SAM3_AI_SELECT_SUPPORTED_SOURCE_SCOPES:
        return cast("Sam3SelectionScope", raw)
    raise Sam3AiSelectSourceImageError(
        "ai_select_source_scope_unsupported",
        "SAM3 AI Select source resolver supports active_layer, visible_composite, "
        "selection_bounds and page only.",
    )


def _resolve_active_layer_qimage(canvas: object) -> tuple[Any, str]:
    """Render only the active layer onto a transparent canvas-size image."""

    from loomstroke.core.raster_source_image import placed_source_image  # noqa: PLC0415

    layer_id = _resolve_active_layer_id(canvas)
    if layer_id is None:
        raise Sam3AiSelectSourceImageError(
            "ai_select_active_layer_missing",
            "SAM3 AI Select requires an active layer.",
        )
    layer = _resolve_layer_by_id(canvas, layer_id)
    layer_image = getattr(layer, "image", None)
    if (
        layer_image is None
        or not hasattr(layer_image, "isNull")
        or layer_image.isNull()
        or layer_image.width() <= 0
        or layer_image.height() <= 0
    ):
        raise Sam3AiSelectSourceImageError(
            "ai_select_active_layer_image_missing",
            "SAM3 AI Select requires a raster active layer with image pixels.",
        )

    canvas_size = _resolve_canvas_size(canvas)
    if canvas_size is None:
        canvas_size = (
            int(layer_image.width()) + max(0, int(getattr(layer, "offset_x", 0) or 0)),
            int(layer_image.height()) + max(0, int(getattr(layer, "offset_y", 0) or 0)),
        )
    width, height = canvas_size
    composed = placed_source_image(canvas, layer, layer_image, width, height)
    if composed is None or composed.isNull():
        raise Sam3AiSelectSourceImageError(
            "ai_select_source_image_empty",
            "SAM3 AI Select active layer image is empty.",
        )
    return composed, layer_id


def _resolve_active_layer_id(canvas: object) -> str | None:
    current_layer = getattr(canvas, "current_layer", None)
    if callable(current_layer):
        try:
            layer_id = _optional_id(current_layer())
        except Exception:
            layer_id = None
        if layer_id is not None:
            return layer_id
    return _optional_id(getattr(canvas, "active_layer", None))


def _resolve_layer_by_id(canvas: object, layer_id: str) -> object | None:
    finder = getattr(canvas, "_find_layer_by_id_tree", None)
    if callable(finder):
        try:
            layer: object | None = finder(layer_id)
        except Exception:
            layer = None
        if layer is not None:
            return layer
    layers_getter = getattr(canvas, "get_layers", None)
    if callable(layers_getter):
        try:
            for candidate_layer in layers_getter():
                found: object = candidate_layer
                if _optional_id(found) == layer_id:
                    return found
        except Exception:
            return None
    return None


def _resolve_canvas_size(canvas: object) -> tuple[int, int] | None:
    snapshot_getter = getattr(canvas, "get_canvas_state_snapshot", None)
    if callable(snapshot_getter):
        try:
            snapshot = snapshot_getter()
        except Exception:
            snapshot = None
        if snapshot is not None:
            width = getattr(snapshot, "width", None)
            height = getattr(snapshot, "height", None)
            if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
                return (width, height)
    size_getter = getattr(canvas, "get_canvas_size", None)
    if callable(size_getter):
        try:
            size = size_getter()
        except Exception:
            return None
        if isinstance(size, tuple) and len(size) == 2:
            width, height = int(size[0]), int(size[1])
            if width > 0 and height > 0:
                return (width, height)
    return None


def _resolve_visible_composite_qimage(canvas: object) -> Any:
    from PySide6.QtGui import QImage, QPixmap  # noqa: PLC0415

    getter = getattr(canvas, "get_composited_pixmap", None)
    if not callable(getter):
        raise Sam3AiSelectSourceImageError(
            "ai_select_source_pixmap_unavailable",
            "SAM3 AI Select visible composite readback is unavailable.",
        )
    try:
        pixmap = getter()
    except Exception as exc:
        raise Sam3AiSelectSourceImageError(
            "ai_select_source_readback_failed",
            "SAM3 AI Select visible composite readback failed.",
        ) from exc

    if not isinstance(pixmap, QPixmap) or pixmap.isNull():
        raise Sam3AiSelectSourceImageError(
            "ai_select_source_pixmap_unavailable",
            "SAM3 AI Select visible composite pixmap is unavailable.",
        )

    image = pixmap.toImage()
    if not isinstance(image, QImage) or image.isNull() or image.width() <= 0 or image.height() <= 0:
        raise Sam3AiSelectSourceImageError(
            "ai_select_source_image_empty",
            "SAM3 AI Select visible composite image is empty.",
        )
    detached = image.copy()
    if detached.isNull() or detached.width() <= 0 or detached.height() <= 0:
        raise Sam3AiSelectSourceImageError(
            "ai_select_source_image_empty",
            "SAM3 AI Select visible composite image is empty.",
        )
    return detached


def _resolve_document_id(canvas: object) -> str | None:
    document_getter = getattr(canvas, "current_document", None)
    if not callable(document_getter):
        return None
    try:
        return _optional_id(document_getter())
    except Exception:
        return None


def _resolve_page_id(host_api: object) -> str | None:
    pages = getattr(host_api, "pages", None)
    page_getter = getattr(pages, "get_active_page_id", None)
    if not callable(page_getter):
        return None
    try:
        return _optional_id(page_getter())
    except Exception:
        return None


def _resolve_dpi(canvas: object) -> int | None:
    snapshot_getter = getattr(canvas, "get_canvas_state_snapshot", None)
    if not callable(snapshot_getter):
        return None
    try:
        value = getattr(snapshot_getter(), "dpi", None)
    except Exception:
        return None
    if isinstance(value, int) and value > 0:
        return value
    return None


def _resolve_available_layer_ids(canvas: object) -> tuple[str, ...]:
    layers_getter = getattr(canvas, "get_layers", None)
    if not callable(layers_getter):
        return ()
    try:
        layers = layers_getter()
    except Exception:
        return ()
    layer_ids: list[str] = []
    for layer in layers:
        layer_id = _optional_id(layer)
        if layer_id is not None:
            layer_ids.append(layer_id)
    return tuple(layer_ids)


def _optional_id(value: object) -> str | None:
    raw = getattr(value, "id", value)
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


__all__ = [
    "SAM3_AI_SELECT_MAX_SOURCE_PIXELS",
    "SAM3_AI_SELECT_SOURCE_IMAGE_SCHEMA_VERSION",
    "SAM3_AI_SELECT_SUPPORTED_SOURCE_SCOPES",
    "Sam3AiSelectResolvedSourceImage",
    "Sam3AiSelectSourceImageError",
    "describe_ai_select_source",
    "normalize_ai_select_supported_source_scope",
    "resolve_ai_select_source_image",
]
