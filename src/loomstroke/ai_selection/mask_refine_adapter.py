"""Canvas-aware adapter for saving locally refined SAM3 mask assets."""

from __future__ import annotations

import hashlib
import io
from collections.abc import Mapping
from typing import Any

from .assets import Sam3AssetStore
from .data_model import MaskAsset
from .mask_refine import MaskRefineOptions, Sam3MaskRefineError, refine_mask_array
from .runtime_imports import load_numpy, load_pillow_image


class Sam3MaskRefineAdapterError(ValueError):
    """Raised when a SAM3 mask asset cannot be refined into a new asset."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def refine_mask_asset(
    asset_store: Sam3AssetStore,
    asset_id: str,
    *,
    output_asset_id: str,
    canvas_width: int,
    canvas_height: int,
    options: MaskRefineOptions | Mapping[str, object] | None = None,
    source_candidate_id: str | None = None,
    metadata: Mapping[str, object] | None = None,
) -> MaskAsset:
    """Refine an existing SAM3 mask asset and save the result as a new asset."""

    target_width, target_height = _validate_canvas_size(canvas_width, canvas_height)
    base_asset_id = str(asset_id).strip()
    refined_asset_id = str(output_asset_id).strip()
    if refined_asset_id == base_asset_id:
        raise Sam3MaskRefineAdapterError(
            "refined_asset_id_conflict",
            "SAM3 refined mask asset id must be different from the source asset id.",
        )

    asset = _load_mask_asset(asset_store, base_asset_id)
    np = _load_numpy()
    alpha = _load_mask_asset_alpha(asset_store, asset, np)
    canvas_alpha = _asset_alpha_to_canvas(
        alpha,
        asset,
        canvas_width=target_width,
        canvas_height=target_height,
        np=np,
    )

    try:
        refined_alpha = refine_mask_array(canvas_alpha, options)
    except Sam3MaskRefineError as exc:
        raise Sam3MaskRefineAdapterError(exc.code, str(exc)) from exc

    bbox = _nonzero_bbox(refined_alpha, np)
    if bbox is None:
        raise Sam3MaskRefineAdapterError(
            "refined_mask_empty",
            "SAM3 local mask refine produced an empty mask.",
        )

    x, y, width, height = bbox
    cropped_alpha = refined_alpha[y : y + height, x : x + width]
    png_bytes = _encode_alpha_png(cropped_alpha, np)
    output_metadata = _refined_metadata(asset, options, metadata)
    candidate_id = (
        source_candidate_id if source_candidate_id is not None else asset.source_candidate_id
    )

    try:
        return asset_store.save_mask_asset(
            png_bytes,
            asset_id=refined_asset_id,
            kind="alpha_mask",
            width=width,
            height=height,
            bbox=bbox,
            source_candidate_id=candidate_id,
            metadata=output_metadata,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise Sam3MaskRefineAdapterError(
            "refined_mask_asset_save_failed",
            f"SAM3 refined mask asset could not be saved: {refined_asset_id!r}.",
        ) from exc


def _validate_canvas_size(canvas_width: int, canvas_height: int) -> tuple[int, int]:
    try:
        width = int(canvas_width)
        height = int(canvas_height)
    except (TypeError, ValueError) as exc:
        raise Sam3MaskRefineAdapterError(
            "invalid_canvas_size",
            "SAM3 mask refine adapter requires a valid canvas size.",
        ) from exc
    if width <= 0 or height <= 0:
        raise Sam3MaskRefineAdapterError(
            "invalid_canvas_size",
            "SAM3 mask refine adapter requires a positive canvas size.",
        )
    return width, height


def _load_mask_asset(asset_store: Sam3AssetStore, asset_id: str) -> MaskAsset:
    try:
        return asset_store.load_mask_asset(asset_id)
    except (OSError, ValueError) as exc:
        raise Sam3MaskRefineAdapterError(
            "mask_asset_missing",
            f"SAM3 mask asset cannot be loaded: {asset_id!r}.",
        ) from exc


def _load_numpy() -> Any:
    try:
        return load_numpy()
    except ImportError as exc:
        raise Sam3MaskRefineAdapterError(
            "mask_refine_runtime_unavailable",
            "SAM3 local mask refine adapter requires numpy.",
        ) from exc


def _load_pillow_image() -> Any:
    try:
        return load_pillow_image()
    except ImportError as exc:
        raise Sam3MaskRefineAdapterError(
            "mask_refine_runtime_unavailable",
            "SAM3 local mask refine adapter requires Pillow.",
        ) from exc


def _load_mask_asset_alpha(asset_store: Sam3AssetStore, asset: MaskAsset, np: Any) -> Any:
    path = asset_store.mask_asset_path(asset)
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise Sam3MaskRefineAdapterError(
            "mask_asset_missing",
            f"SAM3 mask asset artifact cannot be loaded: {asset.asset_id!r}.",
        ) from exc

    expected_hash = str(asset.content_hash or "").strip()
    actual_hash = hashlib.sha256(payload).hexdigest()
    if expected_hash and actual_hash != expected_hash:
        raise Sam3MaskRefineAdapterError(
            "mask_asset_hash_mismatch",
            f"SAM3 mask asset hash mismatch: {asset.asset_id!r}.",
        )

    Image = _load_pillow_image()
    try:
        with Image.open(io.BytesIO(payload)) as image:
            width, height = image.size
            if width != int(asset.width) or height != int(asset.height):
                raise Sam3MaskRefineAdapterError(
                    "mask_asset_manifest_size_mismatch",
                    "SAM3 mask asset image size does not match its manifest.",
                )
            alpha = image.getchannel("A") if "A" in image.getbands() else image.convert("L")
            return np.ascontiguousarray(np.asarray(alpha, dtype=np.uint8))
    except Sam3MaskRefineAdapterError:
        raise
    except Exception as exc:
        raise Sam3MaskRefineAdapterError(
            "mask_asset_unreadable",
            f"SAM3 mask asset is not a readable image: {asset.asset_id!r}.",
        ) from exc


def _asset_alpha_to_canvas(
    alpha: Any,
    asset: MaskAsset,
    *,
    canvas_width: int,
    canvas_height: int,
    np: Any,
) -> Any:
    if int(asset.width) == canvas_width and int(asset.height) == canvas_height:
        return alpha.copy()

    x, y, width, height = tuple(int(item) for item in asset.bbox)
    if width <= 0 or height <= 0:
        raise Sam3MaskRefineAdapterError(
            "mask_asset_bounds_invalid",
            "SAM3 mask asset bounds must be positive.",
        )
    if x < 0 or y < 0 or x + width > canvas_width or y + height > canvas_height:
        raise Sam3MaskRefineAdapterError(
            "mask_asset_outside_canvas",
            "SAM3 mask asset bounds fall outside the target canvas.",
        )
    if alpha.shape != (height, width):
        raise Sam3MaskRefineAdapterError(
            "mask_asset_size_mismatch",
            "SAM3 mask asset image size does not match its canvas-space bounds.",
        )

    canvas_alpha = np.zeros((canvas_height, canvas_width), dtype=np.uint8)
    canvas_alpha[y : y + height, x : x + width] = alpha
    return canvas_alpha


def _nonzero_bbox(alpha: Any, np: Any) -> tuple[int, int, int, int] | None:
    ys, xs = np.where(alpha > 0)
    if xs.size == 0 or ys.size == 0:
        return None
    x0 = int(xs.min())
    x1 = int(xs.max()) + 1
    y0 = int(ys.min())
    y1 = int(ys.max()) + 1
    return (x0, y0, x1 - x0, y1 - y0)


def _encode_alpha_png(alpha: Any, np: Any) -> bytes:
    Image = _load_pillow_image()
    height, width = alpha.shape
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    rgba[:, :, 0:3] = 255
    rgba[:, :, 3] = alpha
    buffer = io.BytesIO()
    Image.fromarray(rgba).save(buffer, format="PNG")
    return buffer.getvalue()


def _refined_metadata(
    asset: MaskAsset,
    options: MaskRefineOptions | Mapping[str, object] | None,
    metadata: Mapping[str, object] | None,
) -> dict[str, object]:
    payload = dict(metadata or {}) if isinstance(metadata, Mapping) else {}
    refine_options = _coerce_options(options).normalized()
    payload.update(
        {
            "source": "sam3_mask_refine_adapter",
            "baseMaskAssetId": asset.asset_id,
            "options": refine_options.to_dict(),
        }
    )
    return payload


def _coerce_options(
    options: MaskRefineOptions | Mapping[str, object] | None,
) -> MaskRefineOptions:
    if isinstance(options, MaskRefineOptions):
        return options
    return MaskRefineOptions.from_dict(options)


__all__ = [
    "Sam3MaskRefineAdapterError",
    "refine_mask_asset",
]
