"""Lazy adapter from SAM3 mask assets to layer mask images."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .assets import Sam3AssetStore
from .data_model import MaskAsset
from .selection_command_adapter import (
    Sam3SelectionCommandAdapterError,
    load_selection_mask_qimage,
)

if TYPE_CHECKING:
    from PySide6.QtGui import QImage


class Sam3LayerMaskAdapterError(ValueError):
    """Raised when a SAM3 mask asset cannot be converted for layer masks."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def build_layer_mask_image_from_asset(
    asset_store: Sam3AssetStore,
    asset_id: str,
    *,
    canvas_width: int,
    canvas_height: int,
) -> QImage:
    """Build a canvas-size layer mask image from a stored SAM3 mask asset."""

    target_width, target_height = _validate_canvas_size(canvas_width, canvas_height)
    try:
        asset = asset_store.load_mask_asset(asset_id)
    except (OSError, ValueError) as exc:
        raise Sam3LayerMaskAdapterError(
            "mask_asset_missing",
            f"SAM3 mask asset cannot be loaded: {asset_id!r}",
        ) from exc

    try:
        alpha_mask = load_selection_mask_qimage(asset_store, asset)
    except Sam3SelectionCommandAdapterError as exc:
        raise Sam3LayerMaskAdapterError(exc.code, str(exc)) from exc

    if alpha_mask.width() == target_width and alpha_mask.height() == target_height:
        return _alpha_to_layer_mask_image(alpha_mask)

    x, y, _, _ = _validate_cropped_bounds(
        alpha_mask,
        asset,
        canvas_width=target_width,
        canvas_height=target_height,
    )

    from PySide6.QtGui import QImage, QPainter  # noqa: PLC0415

    canvas_alpha = QImage(target_width, target_height, QImage.Format.Format_Alpha8)
    canvas_alpha.fill(0)
    painter = QPainter(canvas_alpha)
    painter.drawImage(x, y, alpha_mask)
    painter.end()
    return _alpha_to_layer_mask_image(canvas_alpha)


def _validate_canvas_size(canvas_width: int, canvas_height: int) -> tuple[int, int]:
    try:
        width = int(canvas_width)
        height = int(canvas_height)
    except (TypeError, ValueError) as exc:
        raise Sam3LayerMaskAdapterError(
            "invalid_canvas_size",
            "SAM3 layer mask adapter requires a valid canvas size.",
        ) from exc
    if width <= 0 or height <= 0:
        raise Sam3LayerMaskAdapterError(
            "invalid_canvas_size",
            "SAM3 layer mask adapter requires a positive canvas size.",
        )
    return width, height


def _validate_cropped_bounds(
    image: QImage,
    asset: MaskAsset,
    *,
    canvas_width: int,
    canvas_height: int,
) -> tuple[int, int, int, int]:
    x, y, width, height = tuple(int(item) for item in asset.bbox)
    if width <= 0 or height <= 0:
        raise Sam3LayerMaskAdapterError(
            "mask_asset_bounds_invalid",
            "SAM3 mask asset bounds must be positive.",
        )
    if image.width() != width or image.height() != height:
        raise Sam3LayerMaskAdapterError(
            "mask_asset_size_mismatch",
            "SAM3 mask asset image size does not match its canvas-space bounds.",
        )
    if x < 0 or y < 0 or x + width > canvas_width or y + height > canvas_height:
        raise Sam3LayerMaskAdapterError(
            "mask_asset_outside_canvas",
            "SAM3 mask asset bounds fall outside the target canvas.",
        )
    return (x, y, width, height)


def _alpha_to_layer_mask_image(alpha_mask: QImage) -> QImage:
    # Lazy import: the sam3 package must stay import-safe without Qt/numpy.
    # Mask semantics live in the generic core coverage module, not here.
    from loomstroke.core.mask_coverage import coverage_to_mask_image  # noqa: PLC0415

    return coverage_to_mask_image(alpha_mask)


__all__ = [
    "Sam3LayerMaskAdapterError",
    "build_layer_mask_image_from_asset",
]
