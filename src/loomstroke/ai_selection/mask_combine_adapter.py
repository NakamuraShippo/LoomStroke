"""Canvas-aware adapter for saving combined SAM3 mask assets."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .assets import Sam3AssetStore
from .data_model import MaskAsset
from .mask_combine import MaskCombineMode, Sam3MaskCombineError, combine_mask_arrays
from .mask_refine_adapter import (
    Sam3MaskRefineAdapterError,
    _asset_alpha_to_canvas,
    _encode_alpha_png,
    _load_mask_asset,
    _load_mask_asset_alpha,
    _load_numpy,
    _nonzero_bbox,
    _validate_canvas_size,
)


class Sam3MaskCombineAdapterError(ValueError):
    """Raised when SAM3 mask assets cannot be combined into a new asset."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def combine_mask_assets(
    asset_store: Sam3AssetStore,
    asset_ids: Sequence[str],
    *,
    output_asset_id: str,
    canvas_width: int,
    canvas_height: int,
    mode: MaskCombineMode | str,
    source_candidate_id: str | None = None,
    metadata: Mapping[str, object] | None = None,
) -> MaskAsset:
    """Combine existing SAM3 mask assets and save the result as a new asset."""

    input_asset_ids = _asset_id_items(asset_ids)
    refined_asset_id = str(output_asset_id).strip()
    if refined_asset_id in input_asset_ids:
        raise Sam3MaskCombineAdapterError(
            "combined_asset_id_conflict",
            "SAM3 combined mask asset id must be different from input asset ids.",
        )

    target_width, target_height = _map_asset_error(
        _validate_canvas_size,
        canvas_width,
        canvas_height,
    )
    np = _map_asset_error(_load_numpy)
    assets: list[MaskAsset] = []
    canvas_masks = []
    for asset_id in input_asset_ids:
        asset = _map_asset_error(_load_mask_asset, asset_store, asset_id)
        alpha = _map_asset_error(_load_mask_asset_alpha, asset_store, asset, np)
        canvas_mask = _map_asset_error(
            _asset_alpha_to_canvas,
            alpha,
            asset,
            canvas_width=target_width,
            canvas_height=target_height,
            np=np,
        )
        assets.append(asset)
        canvas_masks.append(canvas_mask)

    try:
        combined_alpha = combine_mask_arrays(canvas_masks, mode=mode)
    except Sam3MaskCombineError as exc:
        raise Sam3MaskCombineAdapterError(exc.code, str(exc)) from exc

    bbox = _nonzero_bbox(combined_alpha, np)
    if bbox is None:
        raise Sam3MaskCombineAdapterError(
            "combined_mask_empty",
            "SAM3 mask combine produced an empty mask.",
        )

    x, y, width, height = bbox
    cropped_alpha = combined_alpha[y : y + height, x : x + width]
    png_bytes = _map_asset_error(_encode_alpha_png, cropped_alpha, np)
    output_metadata = _combined_metadata(assets, mode, metadata)

    try:
        return asset_store.save_mask_asset(
            png_bytes,
            asset_id=refined_asset_id,
            kind="alpha_mask",
            width=width,
            height=height,
            bbox=bbox,
            source_candidate_id=source_candidate_id,
            metadata=output_metadata,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise Sam3MaskCombineAdapterError(
            "combined_mask_asset_save_failed",
            f"SAM3 combined mask asset could not be saved: {refined_asset_id!r}.",
        ) from exc


def _asset_id_items(asset_ids: Sequence[str]) -> list[str]:
    if isinstance(asset_ids, (str, bytes, bytearray)):
        raise Sam3MaskCombineAdapterError(
            "mask_combine_input_missing",
            "SAM3 mask asset combine requires at least two input asset ids.",
        )
    try:
        items = [str(item).strip() for item in asset_ids]
    except TypeError as exc:
        raise Sam3MaskCombineAdapterError(
            "mask_combine_input_missing",
            "SAM3 mask asset combine requires an asset id sequence.",
        ) from exc
    if len(items) < 2:
        raise Sam3MaskCombineAdapterError(
            "mask_combine_input_missing",
            "SAM3 mask asset combine requires at least two input asset ids.",
        )
    return items


def _map_asset_error(function: object, *args: object, **kwargs: object):
    try:
        return function(*args, **kwargs)
    except Sam3MaskRefineAdapterError as exc:
        raise Sam3MaskCombineAdapterError(exc.code, str(exc)) from exc


def _combined_metadata(
    assets: Sequence[MaskAsset],
    mode: MaskCombineMode | str,
    metadata: Mapping[str, object] | None,
) -> dict[str, object]:
    payload = dict(metadata or {}) if isinstance(metadata, Mapping) else {}
    payload.update(
        {
            "source": "sam3_mask_combine_adapter",
            "baseMaskAssetIds": [asset.asset_id for asset in assets],
            "mode": str(mode).strip(),
        }
    )
    return payload


__all__ = [
    "Sam3MaskCombineAdapterError",
    "combine_mask_assets",
]
