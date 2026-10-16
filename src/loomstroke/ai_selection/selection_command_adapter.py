"""Lazy adapter from SAM3 selection plans to core selection commands."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import TYPE_CHECKING

from .assets import Sam3AssetStore
from .data_model import MaskAsset
from .mask_refine import MaskRefineOptions, refine_mask_array
from .selection_bridge import (
    Sam3SelectionApplyPlan,
    Sam3SelectionBridgeError,
    normalize_selection_apply_operation,
)

if TYPE_CHECKING:
    from PySide6.QtGui import QImage

    from loomstroke.core.commands import ApplySelectionMaskCommand


class Sam3SelectionCommandAdapterError(ValueError):
    """Raised when a SAM3 apply plan cannot produce a selection command."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def load_selection_mask_qimage(asset_store: Sam3AssetStore, asset: MaskAsset) -> QImage:
    """Load and verify a SAM3 mask asset as a Qt alpha mask image."""

    path = asset_store.mask_asset_path(asset)
    _verify_content_hash(path, asset)

    from PySide6.QtGui import QImage  # noqa: PLC0415

    image = QImage(str(path))
    if image.isNull():
        raise Sam3SelectionCommandAdapterError(
            "mask_asset_unreadable",
            f"SAM3 mask asset is not a readable image: {asset.asset_id!r}",
        )
    if image.width() != int(asset.width) or image.height() != int(asset.height):
        raise Sam3SelectionCommandAdapterError(
            "mask_asset_manifest_size_mismatch",
            f"SAM3 mask asset image size does not match manifest: {asset.asset_id!r}",
        )
    if not image.hasAlphaChannel():
        # A grayscale/indexed mask (e.g. the local worker's "L" luminance PNG)
        # carries the shape in luminance, not alpha. Converting straight to
        # Alpha8 would read the (fully opaque) alpha and flatten the mask into a
        # solid rectangle, so reinterpret the single luminance channel as alpha.
        return _grayscale_to_alpha8(image, QImage)
    if image.format() != QImage.Format.Format_Alpha8:
        image = image.convertToFormat(QImage.Format.Format_Alpha8)
    return image.copy()


def build_selection_apply_command(
    plan: Sam3SelectionApplyPlan,
    asset_store: Sam3AssetStore,
    *,
    source: str | None = None,
    refine_options: MaskRefineOptions | None = None,
) -> ApplySelectionMaskCommand:
    """Build a core command for applying a ready SAM3 candidate as selection.

    ``refine_options`` applies an in-memory edge finish (feather / smooth)
    to the loaded mask before composition. It defaults to ``None`` so every
    existing caller (protect_text, manga row apply, MCP canvas agent) keeps
    byte-identical behaviour; only the interactive candidate apply path
    opts in.
    """

    _require_ready_plan(plan)
    mask_asset_id = str(plan.mask_asset_id or "").strip()
    if not mask_asset_id:
        raise Sam3SelectionCommandAdapterError(
            "mask_asset_missing",
            "SAM3 selection apply plan does not reference a mask asset.",
        )

    try:
        asset = asset_store.load_mask_asset(mask_asset_id)
    except (OSError, ValueError) as exc:
        raise Sam3SelectionCommandAdapterError(
            "mask_asset_missing",
            f"SAM3 mask asset cannot be loaded: {mask_asset_id!r}",
        ) from exc
    _validate_asset_candidate(plan, asset)
    source_width, source_height = _source_canvas_size(plan)
    mask_image = load_selection_mask_qimage(asset_store, asset)
    operation = _normalize_operation(plan.operation)

    if mask_image.width() == source_width and mask_image.height() == source_height:
        bounds = None
        full_bounds = None
    else:
        bounds = _validate_cropped_asset_bounds(mask_image, asset)
        full_bounds = bounds

    if refine_options is not None and not refine_options.is_noop():
        mask_image, bounds = _refine_selection_mask(
            mask_image,
            bounds,
            source_size=(source_width, source_height),
            options=refine_options,
        )
        full_bounds = bounds

    from loomstroke.core.commands import ApplySelectionMaskCommand  # noqa: PLC0415

    return ApplySelectionMaskCommand(
        mask_image,
        operation,
        bounds=bounds,
        source=source,
        full_bounds=full_bounds,
    )


def _require_ready_plan(plan: Sam3SelectionApplyPlan) -> None:
    if plan.ready:
        return
    codes = ", ".join(reason.code for reason in plan.blocked_reasons) or "unknown"
    raise Sam3SelectionCommandAdapterError(
        "apply_plan_blocked",
        f"SAM3 selection apply plan is blocked: {codes}",
    )


def _validate_asset_candidate(plan: Sam3SelectionApplyPlan, asset: MaskAsset) -> None:
    if asset.source_candidate_id and asset.source_candidate_id != plan.candidate_id:
        raise Sam3SelectionCommandAdapterError(
            "mask_asset_candidate_mismatch",
            "SAM3 mask asset belongs to a different candidate.",
        )


def _source_canvas_size(plan: Sam3SelectionApplyPlan) -> tuple[int, int]:
    width = int(plan.source_ref.width)
    height = int(plan.source_ref.height)
    if width <= 0 or height <= 0:
        raise Sam3SelectionCommandAdapterError(
            "invalid_source_canvas_size",
            "SAM3 selection apply plan has an invalid source canvas size.",
        )
    return width, height


def _validate_cropped_asset_bounds(image: QImage, asset: MaskAsset) -> tuple[int, int, int, int]:
    x, y, width, height = tuple(int(item) for item in asset.bbox)
    if width <= 0 or height <= 0:
        raise Sam3SelectionCommandAdapterError(
            "mask_asset_bounds_invalid",
            "SAM3 mask asset has invalid bounds.",
        )
    if image.width() != width or image.height() != height:
        raise Sam3SelectionCommandAdapterError(
            "mask_asset_size_mismatch",
            "SAM3 mask asset image size does not match its canvas-space bounds.",
        )
    return (x, y, width, height)


def _refine_selection_mask(
    mask_image: QImage,
    bounds: tuple[int, int, int, int] | None,
    *,
    source_size: tuple[int, int],
    options: MaskRefineOptions,
) -> tuple[QImage, tuple[int, int, int, int] | None]:
    """Apply an edge finish to the mask before selection composition.

    Cropped masks are zero-padded by the refine radius (clamped to the
    canvas) so feather can grow past the tight bbox instead of being
    clipped at the crop edge. An all-empty refine result falls back to the
    unrefined mask so tiny candidates never turn into a confusing
    canvas_submit_rejected.
    """

    import numpy as np  # noqa: PLC0415
    from PySide6.QtGui import QImage  # noqa: PLC0415

    normalized = options.normalized()
    # Sum (not max): each enabled operation can push the mask outward by its
    # own radius, so combined options never outgrow the padded window.
    pad = (
        math.ceil(float(normalized.feather_px))
        + math.ceil(float(normalized.smooth_radius_px))
        + int(normalized.expand_px)
    )
    array = _alpha8_to_array(mask_image, np)

    if bounds is None:
        padded = array
        origin = (0, 0)
    else:
        x, y, width, height = bounds
        source_width, source_height = source_size
        left = min(pad, x)
        top = min(pad, y)
        right = min(pad, max(0, source_width - (x + width)))
        bottom = min(pad, max(0, source_height - (y + height)))
        padded = np.zeros((height + top + bottom, width + left + right), dtype=np.uint8)
        padded[top : top + height, left : left + width] = array
        origin = (x - left, y - top)

    try:
        refined = np.asarray(refine_mask_array(padded, normalized), dtype=np.uint8)
    except Exception:
        # The edge finish is cosmetic: a refine failure must never block the
        # apply itself, so fall back to the unrefined mask.
        return mask_image, bounds
    if not np.any(refined):
        return mask_image, bounds

    refined_image = _array_to_alpha8(refined, QImage)
    if bounds is None:
        return refined_image, None
    return refined_image, (origin[0], origin[1], refined.shape[1], refined.shape[0])


def _alpha8_to_array(image: QImage, np: object) -> object:
    width, height = image.width(), image.height()
    bytes_per_line = image.bytesPerLine()
    pointer = image.bits()
    if hasattr(pointer, "setsize"):
        pointer.setsize(bytes_per_line * height)
    raw = bytes(pointer)
    return np.frombuffer(raw, dtype=np.uint8).reshape(height, bytes_per_line)[:, :width].copy()


def _array_to_alpha8(array: object, qimage_cls: type) -> QImage:
    height, width = array.shape
    data = array.tobytes()
    image = qimage_cls(data, width, height, width, qimage_cls.Format.Format_Alpha8)
    return image.copy()


def _grayscale_to_alpha8(image: QImage, qimage_cls: type) -> QImage:
    """Reinterpret an 8-bit grayscale image's luminance as an alpha mask.

    The single luminance channel is copied byte-for-byte into an Alpha8 image
    of the same layout (bytesPerLine preserved), so the shape survives instead
    of being flattened by a luminance-discarding Alpha8 conversion.
    """

    gray = image.convertToFormat(qimage_cls.Format.Format_Grayscale8)
    width = gray.width()
    height = gray.height()
    bytes_per_line = gray.bytesPerLine()
    pointer = gray.bits()
    if hasattr(pointer, "setsize"):
        pointer.setsize(bytes_per_line * height)
    raw = bytes(pointer)
    alpha = qimage_cls(raw, width, height, bytes_per_line, qimage_cls.Format.Format_Alpha8)
    return alpha.copy()


def _normalize_operation(value: str) -> str:
    try:
        return normalize_selection_apply_operation(value)
    except Sam3SelectionBridgeError as exc:
        raise Sam3SelectionCommandAdapterError(exc.code, str(exc)) from exc


def _verify_content_hash(path: Path, asset: MaskAsset) -> None:
    expected = str(asset.content_hash or "").strip()
    if not expected:
        return
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise Sam3SelectionCommandAdapterError(
            "mask_asset_hash_mismatch",
            f"SAM3 mask asset hash mismatch: {asset.asset_id!r}",
        )


__all__ = [
    "Sam3SelectionCommandAdapterError",
    "build_selection_apply_command",
    "load_selection_mask_qimage",
]
