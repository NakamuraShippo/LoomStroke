"""Layer access helpers for commands."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from . import command_layer_helpers
from .command_image_utils import _is_qimage_layer
from .command_types import LayerTransform, SelectionRect
from .geometry import Affine2D

logger = logging.getLogger("loomstroke.core.commands")

if TYPE_CHECKING:
    from .canvas import Canvas


def _resolve_target_layer(canvas: Canvas, layer_id: str | None) -> Any | None:
    layer = None
    if layer_id:
        getter = getattr(canvas, "get_layer_by_id", None)
        if callable(getter):
            layer = getter(layer_id)
        else:
            getter = getattr(canvas, "_get_layer_by_id", None)
            if callable(getter):
                layer = getter(layer_id)
    if layer is None:
        layer = getattr(canvas, "active_layer", None)
    if layer is None:
        layers = getattr(canvas, "_layers", None)
        active_index = getattr(canvas, "_active_index", None)
        if (
            isinstance(layers, list)
            and isinstance(active_index, int)
            and 0 <= active_index < len(layers)
        ):
            layer = layers[active_index]
    return layer


def _layer_allows_edit(layer: Any) -> bool:
    if layer is None:
        return False
    if getattr(layer, "layer_type", "raster") == "vector":
        return False
    return not getattr(layer, "locked", False)


def _layer_offset(layer: Any) -> tuple[int, int]:
    return command_layer_helpers.layer_offset(layer)


def _coerce_layer_transform(value: Any, layer: Any | None = None) -> LayerTransform:
    if isinstance(value, LayerTransform):
        return value
    if isinstance(value, Affine2D):
        return LayerTransform.from_affine(value)
    if isinstance(value, Mapping):
        return LayerTransform(
            tx=float(value.get("tx", value.get("x", 0.0))),
            ty=float(value.get("ty", value.get("y", 0.0))),
            scale_x=float(value.get("scale_x", 1.0)),
            scale_y=float(value.get("scale_y", 1.0)),
            rotation=float(value.get("rotation", 0.0)),
            origin_x=float(value.get("origin_x", value.get("pivot_x", 0.0))),
            origin_y=float(value.get("origin_y", value.get("pivot_y", 0.0))),
        )
    if layer is not None:
        ox, oy = _layer_offset(layer)
        return LayerTransform.translation(ox, oy)
    return LayerTransform.identity()


def _layer_transform(layer: Any) -> LayerTransform:
    transform = getattr(layer, "transform", None)
    if transform is not None:
        try:
            result = _coerce_layer_transform(transform, layer)
            ox, oy = _layer_offset(layer)
            if result == LayerTransform.identity() and (ox != 0 or oy != 0):
                return result.with_translation(ox, oy)
            return result
        except Exception as exc:
            logger.debug("_layer_transform: invalid transform on layer: %s", exc)
    return _coerce_layer_transform(None, layer)


def _set_layer_transform(layer: Any, transform: LayerTransform) -> None:
    layer.transform = transform
    if hasattr(layer, "offset_x"):
        layer.offset_x = round(transform.tx)
    if hasattr(layer, "offset_y"):
        layer.offset_y = round(transform.ty)
    if hasattr(layer, "transform_version"):
        layer.transform_version = int(getattr(layer, "transform_version", 0)) + 1


def _sync_layer_transform_translation(layer: Any) -> None:
    ox, oy = _layer_offset(layer)
    _set_layer_transform(layer, _layer_transform(layer).with_translation(ox, oy))


def _supports_layer_expansion(canvas: Canvas, layer: Any) -> bool:
    return _is_qimage_layer(layer) and callable(getattr(canvas, "ensure_layer_bounds", None))


def _ensure_layer_bounds(canvas: Canvas, layer: Any, bounds: SelectionRect) -> None:
    ensure = getattr(canvas, "ensure_layer_bounds", None)
    if not callable(ensure):
        return
    layer_id = getattr(layer, "id", None)
    try:
        ensure(layer_id, bounds)
    except TypeError:
        try:
            ensure(layer, bounds)
        except Exception as exc:
            logger.debug("_ensure_layer_bounds: alt signature call failed: %s", exc)
            return


def _canvas_dpi(canvas: Canvas) -> int | None:
    dpi = getattr(canvas, "_dpi", None)
    if isinstance(dpi, int) and dpi > 0:
        return dpi
    config = getattr(canvas, "config", None)
    if config is not None and hasattr(config, "dpi"):
        try:
            value = int(config.dpi)
        except Exception as exc:
            logger.debug("_canvas_dpi: int(config.dpi) conversion failed: %s", exc)
            value = None
        if value and value > 0:
            return value
    return None


def _resolve_layer_by_id(canvas: Canvas, layer_id: str) -> Any | None:
    """Find a layer by ID, searching the full tree if available."""
    finder = getattr(canvas, "_find_layer_by_id_tree", None)
    if callable(finder):
        return finder(layer_id)
    get_layers = getattr(canvas, "_layers", None)
    if get_layers is None:
        return None
    for layer in get_layers:
        if getattr(layer, "id", None) == layer_id:
            return layer
    return None


def _bump_layer_mask_version(layer: Any) -> None:
    command_layer_helpers.bump_layer_mask_version(layer)


def _mask_size_matches_layer_image(layer: Any, mask: Any) -> bool:
    return command_layer_helpers.mask_size_matches_layer_image(layer, mask)


def _layer_mask_to_local(layer: Any, mask: Any, width: int, height: int) -> Any:
    """Return a layer-local mask matching the current image dimensions."""
    return command_layer_helpers.layer_mask_to_local(layer, mask, width, height)


def _shift_layer_mask(layer: Any, dx: int, dy: int) -> None:
    """Shift layer_mask pixels by (dx, dy) so the mask follows the layer."""
    command_layer_helpers.shift_layer_mask(layer, dx, dy)


def _canvas_size_tuple(canvas: Canvas) -> tuple[int, int]:
    """Get canvas (width, height) safely."""
    w = getattr(canvas, "_width", 0) or getattr(canvas, "width", 0)
    h = getattr(canvas, "_height", 0) or getattr(canvas, "height", 0)
    if callable(w):
        w = w()
    if callable(h):
        h = h()
    return int(w or 4096), int(h or 4096)
