"""Lazy adapter from SAM3 mask assets to layer mask commands."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .assets import Sam3AssetStore
from .layer_mask_adapter import (
    Sam3LayerMaskAdapterError,
    build_layer_mask_image_from_asset,
)
from .mask_combine import Sam3MaskCombineError, combine_mask_arrays
from .runtime_imports import load_numpy

if TYPE_CHECKING:
    from loomstroke.core.commands import SetLayerMaskCommand


class Sam3LayerMaskCommandAdapterError(ValueError):
    """Raised when a SAM3 mask asset cannot be converted to a command."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def build_layer_mask_apply_command(
    asset_store: Sam3AssetStore,
    asset_id: str,
    *,
    layer_id: str,
    canvas_width: int,
    canvas_height: int,
    mask_enabled: bool = True,
) -> SetLayerMaskCommand:
    """Build a layer-mask command from a stored SAM3 mask asset."""

    target_layer_id = _require_target_layer_id(layer_id)
    mask_image = _build_layer_mask_image(
        asset_store,
        asset_id,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
    )

    from loomstroke.core.commands import SetLayerMaskCommand  # noqa: PLC0415

    return SetLayerMaskCommand(
        target_layer_id,
        mask_image,
        mask_enabled=mask_enabled,
    )


def build_layer_mask_add_candidate_command(
    asset_store: Sam3AssetStore,
    asset_id: str,
    *,
    layer_id: str,
    base_mask: Any,
    canvas_width: int,
    canvas_height: int,
    mask_enabled: bool = True,
) -> SetLayerMaskCommand:
    """Build a command that unions a SAM3 mask asset into an existing layer mask."""

    target_layer_id = _require_target_layer_id(layer_id)
    candidate_mask = _build_layer_mask_image(
        asset_store,
        asset_id,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
    )
    combined_mask = _combine_layer_masks(
        base_mask,
        candidate_mask,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
    )

    from loomstroke.core.commands import SetLayerMaskCommand  # noqa: PLC0415

    return SetLayerMaskCommand(
        target_layer_id,
        combined_mask,
        mask_enabled=mask_enabled,
    )


def build_clipping_mask_apply_command(
    asset_store: Sam3AssetStore,
    asset_id: str,
    *,
    layer_id: str,
    canvas_width: int,
    canvas_height: int,
    mask_enabled: bool = True,
) -> SetLayerMaskCommand:
    """Build a routed layer-mask command that also enables clipping."""

    target_layer_id = _require_target_layer_id(layer_id)
    mask_image = _build_layer_mask_image(
        asset_store,
        asset_id,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
    )

    from loomstroke.core.command_layer_access import _resolve_layer_by_id  # noqa: PLC0415
    from loomstroke.core.commands import (  # noqa: PLC0415
        SetLayerMaskCommand,
    )

    class _Sam3ClippingMaskApplyCommand(SetLayerMaskCommand):
        def __init__(self) -> None:
            super().__init__(
                target_layer_id,
                mask_image,
                mask_enabled=mask_enabled,
            )
            self._old_clipping: bool | None = None

        def apply(self, canvas: object) -> bool:
            layer = _resolve_layer_by_id(canvas, target_layer_id)
            if layer is None:
                return False
            self._old_clipping = bool(getattr(layer, "clipping", False))
            if not super().apply(canvas):
                self._old_clipping = None
                return False
            set_clipping = getattr(canvas, "set_clipping", None)
            if not callable(set_clipping) or not bool(set_clipping(target_layer_id, True)):
                super().revert(canvas)
                self._old_clipping = None
                return False
            return True

        def revert(self, canvas: object) -> bool:
            if self._old_clipping is None:
                return False
            set_clipping = getattr(canvas, "set_clipping", None)
            if not callable(set_clipping) or not bool(
                set_clipping(target_layer_id, self._old_clipping)
            ):
                return False
            return super().revert(canvas)

    return _Sam3ClippingMaskApplyCommand()


def _require_target_layer_id(layer_id: str) -> str:
    target_layer_id = str(layer_id).strip()
    if not target_layer_id:
        raise Sam3LayerMaskCommandAdapterError(
            "target_layer_missing",
            "SAM3 layer mask apply requires a target layer id.",
        )
    return target_layer_id


def _build_layer_mask_image(
    asset_store: Sam3AssetStore,
    asset_id: str,
    *,
    canvas_width: int,
    canvas_height: int,
):
    try:
        return build_layer_mask_image_from_asset(
            asset_store,
            asset_id,
            canvas_width=canvas_width,
            canvas_height=canvas_height,
        )
    except Sam3LayerMaskAdapterError as exc:
        raise Sam3LayerMaskCommandAdapterError(exc.code, str(exc)) from exc


def _combine_layer_masks(
    base_mask: Any,
    candidate_mask: Any,
    *,
    canvas_width: int,
    canvas_height: int,
):
    _validate_layer_mask_image(
        base_mask,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        missing_code="target_layer_mask_missing",
        mismatch_code="target_layer_mask_size_mismatch",
    )
    _validate_layer_mask_image(
        candidate_mask,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        missing_code="candidate_layer_mask_missing",
        mismatch_code="candidate_layer_mask_size_mismatch",
    )
    np = _load_numpy()
    base_alpha = _layer_mask_to_alpha_array(base_mask, np)
    candidate_alpha = _layer_mask_to_alpha_array(candidate_mask, np)
    try:
        combined_alpha = combine_mask_arrays([base_alpha, candidate_alpha], mode="union")
    except Sam3MaskCombineError as exc:
        raise Sam3LayerMaskCommandAdapterError(exc.code, str(exc)) from exc
    return _alpha_array_to_layer_mask_image(combined_alpha, np)


def _validate_layer_mask_image(
    mask: Any,
    *,
    canvas_width: int,
    canvas_height: int,
    missing_code: str,
    mismatch_code: str,
) -> None:
    is_null = getattr(mask, "isNull", None)
    if mask is None or (callable(is_null) and bool(is_null())):
        raise Sam3LayerMaskCommandAdapterError(
            missing_code,
            "SAM3 layer mask add requires an existing layer mask.",
        )
    width_fn = getattr(mask, "width", None)
    height_fn = getattr(mask, "height", None)
    if not callable(width_fn) or not callable(height_fn):
        raise Sam3LayerMaskCommandAdapterError(
            mismatch_code,
            "SAM3 layer mask add requires mask images with canvas size.",
        )
    if int(width_fn()) != int(canvas_width) or int(height_fn()) != int(canvas_height):
        raise Sam3LayerMaskCommandAdapterError(
            mismatch_code,
            "SAM3 layer mask add requires mask images with canvas size.",
        )


def _load_numpy() -> Any:
    try:
        return load_numpy()
    except ImportError as exc:
        raise Sam3LayerMaskCommandAdapterError(
            "mask_combine_runtime_unavailable",
            "SAM3 layer mask add requires numpy.",
        ) from exc


def _layer_mask_to_alpha_array(mask: Any, np: Any) -> Any:
    from PySide6.QtGui import QImage  # noqa: PLC0415

    image = mask.convertToFormat(QImage.Format.Format_ARGB32)
    width = int(image.width())
    height = int(image.height())
    bytes_per_line = int(image.bytesPerLine())
    byte_count = bytes_per_line * height
    bits = image.bits()
    if hasattr(bits, "setsize"):
        bits.setsize(byte_count)
    data = np.frombuffer(bits, dtype=np.uint8, count=byte_count).reshape(
        height,
        bytes_per_line,
    )
    color_bytes = data[:, : width * 4].reshape(height, width, 4)
    return np.ascontiguousarray(np.max(color_bytes[:, :, :3], axis=2).astype(np.uint8))


def _alpha_array_to_layer_mask_image(alpha_mask: Any, np: Any):
    from PySide6.QtGui import QImage  # noqa: PLC0415

    alpha = np.asarray(alpha_mask, dtype=np.uint8)
    height, width = alpha.shape
    mask_data = np.zeros((height, width, 4), dtype=np.uint8)
    mask_data[:, :, 0] = alpha
    mask_data[:, :, 1] = alpha
    mask_data[:, :, 2] = alpha
    mask_data[:, :, 3] = 255
    return QImage(
        mask_data.data,
        width,
        height,
        width * 4,
        QImage.Format.Format_ARGB32_Premultiplied,
    ).copy()


__all__ = [
    "Sam3LayerMaskCommandAdapterError",
    "build_clipping_mask_apply_command",
    "build_layer_mask_add_candidate_command",
    "build_layer_mask_apply_command",
]
