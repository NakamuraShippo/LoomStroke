"""Lazy adapter from SAM3 mask assets to cut-to-new-layer commands."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, cast

from .assets import Sam3AssetStore
from .layer_mask_adapter import (
    Sam3LayerMaskAdapterError,
    build_layer_mask_image_from_asset,
)

if TYPE_CHECKING:
    from PySide6.QtGui import QImage

    from loomstroke.core.commands import AddLayerCommand

Sam3ExtractLayerMode = Literal["foreground", "background"]
_EXTRACT_LAYER_MODES = frozenset({"foreground", "background"})


class Sam3CutToLayerAdapterError(ValueError):
    """Raised when a SAM3 mask asset cannot be converted to a layer command."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class Sam3ExtractLayerAdapterError(Sam3CutToLayerAdapterError):
    """Raised when a SAM3 mask asset cannot be converted to an extraction command."""


def build_cut_to_new_layer_command(
    asset_store: Sam3AssetStore,
    asset_id: str,
    *,
    source_layer_id: str,
    canvas_width: int,
    canvas_height: int,
    layer_name: str | None = None,
) -> AddLayerCommand:
    """Build a non-destructive cut-to-new-layer command from a SAM3 mask asset."""

    cut_layer_name = str(layer_name or "").strip() or "SAM3 Cut"
    return build_extract_to_new_layer_command(
        asset_store,
        asset_id,
        source_layer_id=source_layer_id,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        mode="foreground",
        layer_name=cut_layer_name,
    )


def build_extract_to_new_layer_command(
    asset_store: Sam3AssetStore,
    asset_id: str,
    *,
    source_layer_id: str,
    canvas_width: int,
    canvas_height: int,
    mode: Sam3ExtractLayerMode,
    layer_name: str | None = None,
) -> AddLayerCommand:
    """Build a non-destructive foreground/background extraction command."""

    target_source_layer_id = _require_source_layer_id(source_layer_id)
    extract_mode = _require_extract_mode(mode)
    mask_image = _build_canvas_mask_image(
        asset_store,
        asset_id,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
    )

    from loomstroke.core.commands import AddLayerCommand  # noqa: PLC0415

    command_layer_name = str(layer_name or "").strip() or _default_extract_layer_name(extract_mode)

    class _Sam3ExtractToNewLayerCommand(AddLayerCommand):
        def __init__(self) -> None:
            super().__init__(command_layer_name, layer_type="raster")
            self._created_layer_id: str | None = None

        @property
        def created_layer_id(self) -> str | None:
            return self._created_layer_id

        def apply(self, canvas: object) -> bool:
            new_layer_id = _apply_extract_to_new_layer(
                canvas,
                source_layer_id=target_source_layer_id,
                mask_image=mask_image,
                mode=extract_mode,
                layer_name=command_layer_name,
            )
            if new_layer_id is None:
                return False
            self._layer_id = new_layer_id
            self._created_layer_id = new_layer_id
            return True

        def revert(self, canvas: object) -> bool:
            if self._created_layer_id is None:
                return False
            removed = _remove_layer(canvas, self._created_layer_id)
            if removed:
                self._created_layer_id = None
                self._layer_id = None
            return removed

    return _Sam3ExtractToNewLayerCommand()


def _require_source_layer_id(source_layer_id: str) -> str:
    target_source_layer_id = str(source_layer_id).strip()
    if not target_source_layer_id:
        raise Sam3ExtractLayerAdapterError(
            "source_layer_missing",
            "SAM3 layer extraction requires a source layer id.",
        )
    return target_source_layer_id


def _require_extract_mode(mode: object) -> Sam3ExtractLayerMode:
    clean_mode = str(mode).strip()
    if clean_mode not in _EXTRACT_LAYER_MODES:
        raise Sam3ExtractLayerAdapterError(
            "extract_mode_invalid",
            f"SAM3 layer extraction mode is invalid: {mode!r}.",
        )
    return cast("Sam3ExtractLayerMode", clean_mode)


def _default_extract_layer_name(mode: Sam3ExtractLayerMode) -> str:
    if mode == "background":
        return "SAM3 Background"
    return "SAM3 Foreground"


def _build_canvas_mask_image(
    asset_store: Sam3AssetStore,
    asset_id: str,
    *,
    canvas_width: int,
    canvas_height: int,
) -> QImage:
    try:
        return build_layer_mask_image_from_asset(
            asset_store,
            asset_id,
            canvas_width=canvas_width,
            canvas_height=canvas_height,
        )
    except Sam3LayerMaskAdapterError as exc:
        raise Sam3ExtractLayerAdapterError(exc.code, str(exc)) from exc


def _build_masked_layer_image(
    source_layer: Any,
    canvas_mask_image: QImage,
    *,
    mode: Sam3ExtractLayerMode,
) -> QImage | None:
    source_image = getattr(source_layer, "image", None)
    if source_image is None or not hasattr(source_image, "isNull") or source_image.isNull():
        return None
    width = int(source_image.width())
    height = int(source_image.height())
    if width <= 0 or height <= 0:
        return None

    from PySide6.QtGui import QImage  # noqa: PLC0415

    source = source_image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    local_mask = _canvas_mask_to_layer_local(source_layer, canvas_mask_image, width, height)
    mask_alpha = _effective_mask_alpha(local_mask, mode=mode)
    if not bool(mask_alpha.any()):
        return None

    import numpy as np  # noqa: PLC0415

    source_data = _qimage_bgra_data(source)
    masked = (
        (source_data.astype(np.uint16) * mask_alpha[:, :, None].astype(np.uint16) + 127) // 255
    ).astype(np.uint8)
    return QImage(
        masked.data,
        width,
        height,
        width * 4,
        QImage.Format.Format_ARGB32_Premultiplied,
    ).copy()


# Public alias for the asset export pipeline (sam3/asset_export.py). Same
# contract as the private builder: layer-local ARGB32_Premultiplied output,
# None when the layer has no image or the mask does not intersect it.
def build_masked_layer_image(
    source_layer: Any,
    canvas_mask_image: QImage,
    *,
    mode: Sam3ExtractLayerMode = "foreground",
) -> QImage | None:
    return _build_masked_layer_image(source_layer, canvas_mask_image, mode=mode)


def _apply_extract_to_new_layer(
    canvas: object,
    *,
    source_layer_id: str,
    mask_image: QImage,
    mode: Sam3ExtractLayerMode,
    layer_name: str,
) -> str | None:
    from loomstroke.core.command_layer_access import _resolve_layer_by_id  # noqa: PLC0415

    source_layer = _resolve_layer_by_id(canvas, source_layer_id)
    if source_layer is None:
        return None
    extracted_image = _build_masked_layer_image(source_layer, mask_image, mode=mode)
    if extracted_image is None:
        return None
    add_layer = getattr(canvas, "add_layer", None)
    if not callable(add_layer):
        return None

    ref = add_layer(layer_name, layer_type="raster")
    new_layer_id = str(getattr(ref, "id", "") if ref is not None else "").strip()
    if not new_layer_id:
        return None
    new_layer = _resolve_layer_by_id(canvas, new_layer_id)
    if new_layer is None:
        _remove_layer(canvas, new_layer_id)
        return None

    new_layer.image = extracted_image
    new_layer.offset_x = int(getattr(source_layer, "offset_x", 0))
    new_layer.offset_y = int(getattr(source_layer, "offset_y", 0))
    _bump_texture_version(new_layer)
    _refresh_canvas(canvas)
    return new_layer_id


def _canvas_mask_to_layer_local(
    source_layer: Any,
    canvas_mask_image: QImage,
    width: int,
    height: int,
) -> QImage:
    from PySide6.QtGui import QImage, QPainter  # noqa: PLC0415

    local = QImage(int(width), int(height), QImage.Format.Format_ARGB32_Premultiplied)
    local.fill(0)
    offset_x = int(getattr(source_layer, "offset_x", 0))
    offset_y = int(getattr(source_layer, "offset_y", 0))
    painter = QPainter(local)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
    painter.drawImage(-offset_x, -offset_y, canvas_mask_image)
    painter.end()
    return local


def _effective_mask_alpha(image: QImage, *, mode: Sam3ExtractLayerMode):
    mask_alpha = _grayscale_qimage_channel(image)
    if mode == "background":
        return 255 - mask_alpha
    return mask_alpha


def _grayscale_qimage_channel(image: QImage):
    import numpy as np  # noqa: PLC0415
    from PySide6.QtGui import QImage  # noqa: PLC0415

    image = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    width = int(image.width())
    height = int(image.height())
    byte_count = int(image.bytesPerLine()) * height
    bits = image.bits()
    if hasattr(bits, "setsize"):
        bits.setsize(byte_count)
    data = np.frombuffer(bits, dtype=np.uint8, count=byte_count)
    rows = data.reshape(height, int(image.bytesPerLine()))
    return rows[:, : width * 4].reshape(height, width, 4)[:, :, 0].copy()


def _qimage_bgra_data(image: QImage):
    import numpy as np  # noqa: PLC0415

    width = int(image.width())
    height = int(image.height())
    byte_count = int(image.bytesPerLine()) * height
    bits = image.bits()
    if hasattr(bits, "setsize"):
        bits.setsize(byte_count)
    data = np.frombuffer(bits, dtype=np.uint8, count=byte_count)
    rows = data.reshape(height, int(image.bytesPerLine()))
    return rows[:, : width * 4].reshape(height, width, 4).copy()


def _remove_layer(canvas: object, layer_id: str) -> bool:
    remove_layer = getattr(canvas, "remove_layer", None)
    if not callable(remove_layer):
        return False
    return bool(remove_layer(layer_id))


def _bump_texture_version(layer: Any) -> None:
    if not hasattr(layer, "texture_version"):
        return
    try:
        layer.texture_version = int(getattr(layer, "texture_version", 0)) + 1
    except Exception:
        return


def _refresh_canvas(canvas: object) -> None:
    invalidate = getattr(canvas, "_invalidate_layer_cache", None)
    if callable(invalidate):
        invalidate()
    recompose = getattr(canvas, "_recompose", None)
    if callable(recompose):
        recompose()


__all__ = [
    "Sam3CutToLayerAdapterError",
    "Sam3ExtractLayerAdapterError",
    "Sam3ExtractLayerMode",
    "build_cut_to_new_layer_command",
    "build_extract_to_new_layer_command",
    "build_masked_layer_image",
]
