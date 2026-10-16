"""Preparation helpers for SAM3 generation mask assets."""

from __future__ import annotations

from collections.abc import Mapping

from .assets import Sam3AssetStore
from .data_model import MaskAsset
from .mask_refine import MaskRefineOptions
from .mask_refine_adapter import Sam3MaskRefineAdapterError, refine_mask_asset


class Sam3GenerationMaskPrepareError(ValueError):
    """Raised when a SAM3 generation mask preparation step fails."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def create_background_generation_mask_asset(
    asset_store: Sam3AssetStore,
    subject_mask_asset_id: str,
    *,
    output_asset_id: str,
    canvas_width: int,
    canvas_height: int,
    source_candidate_id: str | None = None,
    metadata: Mapping[str, object] | None = None,
) -> MaskAsset:
    """Create a background-only generation mask from a subject mask asset."""

    subject_asset_id = str(subject_mask_asset_id).strip()
    if not subject_asset_id:
        raise Sam3GenerationMaskPrepareError(
            "mask_asset_missing",
            "SAM3 background generation mask requires a subject mask asset.",
        )
    background_asset_id = str(output_asset_id).strip()
    if not background_asset_id:
        raise Sam3GenerationMaskPrepareError(
            "background_mask_asset_id_missing",
            "SAM3 background generation mask requires an output asset id.",
        )

    output_metadata = _background_metadata(subject_asset_id, metadata)
    try:
        return refine_mask_asset(
            asset_store,
            subject_asset_id,
            output_asset_id=background_asset_id,
            canvas_width=canvas_width,
            canvas_height=canvas_height,
            options=MaskRefineOptions(invert=True),
            source_candidate_id=source_candidate_id,
            metadata=output_metadata,
        )
    except Sam3MaskRefineAdapterError as exc:
        raise Sam3GenerationMaskPrepareError(exc.code, str(exc)) from exc


def create_selection_inpaint_mask_asset(
    asset_store: Sam3AssetStore,
    selection_array: object,
    *,
    bounds: tuple[int, int, int, int],
    metadata: Mapping[str, object] | None = None,
) -> MaskAsset:
    """Persist a canvas selection as a deterministic inpaint mask asset.

    ``selection_array`` is the canvas-normalized uint8 alpha array (stride
    already stripped). The asset id derives from the cropped pixel bytes plus
    the bounds, so the same selection always maps to the same id and rewrites
    idempotently; a different selection gets a fresh asset.
    """

    import hashlib  # noqa: PLC0415

    import numpy as np  # noqa: PLC0415

    from .mask_refine_adapter import _encode_alpha_png  # noqa: PLC0415

    x, y, width, height = (int(value) for value in bounds)
    if width <= 0 or height <= 0:
        raise Sam3GenerationMaskPrepareError(
            "selection_missing",
            "SAM3 selection inpaint mask requires non-empty selection bounds.",
        )
    array = np.asarray(selection_array, dtype=np.uint8)
    cropped = array[y : y + height, x : x + width]
    if cropped.shape != (height, width):
        # A mask smaller than the canvas silently clips the crop, producing a
        # PNG whose real size disagrees with the manifest bbox.
        raise Sam3GenerationMaskPrepareError(
            "selection_mask_invalid",
            "SAM3 selection mask does not cover the selection bounds.",
        )
    if not np.any(cropped):
        raise Sam3GenerationMaskPrepareError(
            "selection_empty",
            "SAM3 selection inpaint mask requires selected pixels.",
        )

    digest = hashlib.sha256()
    digest.update(cropped.tobytes())
    digest.update(f"{x},{y},{width},{height}".encode("ascii"))
    asset_id = f"sam3-selection-inpaint-{digest.hexdigest()[:16]}"

    png_bytes = _encode_alpha_png(np.ascontiguousarray(cropped), np)
    output_metadata = dict(metadata or {}) if isinstance(metadata, Mapping) else {}
    output_metadata.update(
        {
            "operation": "selection_inpaint_mask",
            "generationPurpose": "inpaint",
        }
    )
    return asset_store.save_mask_asset(
        png_bytes,
        asset_id=asset_id,
        kind="alpha_mask",
        width=width,
        height=height,
        bbox=(x, y, width, height),
        source_candidate_id=None,
        metadata=output_metadata,
    )


def _background_metadata(
    subject_mask_asset_id: str,
    metadata: Mapping[str, object] | None,
) -> dict[str, object]:
    payload = dict(metadata or {}) if isinstance(metadata, Mapping) else {}
    payload.update(
        {
            "operation": "background_generation_mask",
            "generationPurpose": "background",
            "protectedSubjectMaskAssetId": subject_mask_asset_id,
        }
    )
    return payload


__all__ = [
    "Sam3GenerationMaskPrepareError",
    "create_background_generation_mask_asset",
    "create_selection_inpaint_mask_asset",
]
