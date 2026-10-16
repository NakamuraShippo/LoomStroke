"""UI-independent SAM3 mask combine helpers."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal, cast

from .runtime_imports import load_numpy

SAM3_MASK_COMBINE_SCHEMA_VERSION = "sam3.mask_combine.v1"
MaskCombineMode = Literal["union", "subtract", "intersect", "xor"]

_MASK_COMBINE_MODES = frozenset({"union", "subtract", "intersect", "xor"})


class Sam3MaskCombineError(ValueError):
    """Raised when SAM3 mask arrays cannot be combined."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def normalize_mask_combine_mode(mode: object) -> MaskCombineMode:
    """Normalize a raw SAM3 mask combine mode."""

    value = str(mode).strip()
    if value not in _MASK_COMBINE_MODES:
        raise Sam3MaskCombineError(
            "mask_combine_mode_invalid",
            "SAM3 mask combine mode must be union, subtract, intersect, or xor.",
        )
    return cast("MaskCombineMode", value)


def combine_mask_arrays(
    masks: Sequence[object],
    *,
    mode: MaskCombineMode | str,
) -> object:
    """Combine 2D alpha masks using SAM3 local mask combine semantics."""

    combine_mode = normalize_mask_combine_mode(mode)
    items = _mask_items(masks)
    np = _load_numpy()
    normalized = [_as_uint8_mask(mask, np) for mask in items]
    _validate_shapes(normalized)

    if combine_mode == "union":
        return np.maximum.reduce(normalized).astype(np.uint8, copy=False)
    if combine_mode == "subtract":
        return _subtract_masks(normalized, np)
    if combine_mode == "intersect":
        return np.minimum.reduce(normalized).astype(np.uint8, copy=False)
    return _xor_masks(normalized, np)


def _mask_items(masks: Sequence[object]) -> list[object]:
    if isinstance(masks, (str, bytes, bytearray)):
        raise Sam3MaskCombineError(
            "mask_combine_input_missing",
            "SAM3 mask combine requires at least two masks.",
        )
    try:
        items = list(masks)
    except TypeError as exc:
        raise Sam3MaskCombineError(
            "mask_combine_input_missing",
            "SAM3 mask combine requires a mask sequence.",
        ) from exc
    if len(items) < 2:
        raise Sam3MaskCombineError(
            "mask_combine_input_missing",
            "SAM3 mask combine requires at least two masks.",
        )
    return items


def _load_numpy() -> Any:
    try:
        return load_numpy()
    except ImportError as exc:
        raise Sam3MaskCombineError(
            "mask_combine_runtime_unavailable",
            "SAM3 mask combine requires numpy.",
        ) from exc


def _as_uint8_mask(mask: object, np: Any) -> Any:
    try:
        array = np.asarray(mask)
    except Exception as exc:
        raise Sam3MaskCombineError(
            "mask_combine_invalid_mask",
            "SAM3 mask combine requires 2D mask-like inputs.",
        ) from exc

    if array.ndim != 2 or array.size <= 0:
        raise Sam3MaskCombineError(
            "mask_combine_invalid_mask",
            "SAM3 mask combine requires non-empty 2D masks.",
        )

    if array.dtype == np.bool_:
        return np.ascontiguousarray(array.astype(np.uint8) * 255)

    try:
        numeric = array.astype(np.float32, copy=False)
    except (TypeError, ValueError) as exc:
        raise Sam3MaskCombineError(
            "mask_combine_invalid_mask",
            "SAM3 mask combine mask values must be numeric.",
        ) from exc

    numeric = np.nan_to_num(numeric, nan=0.0, posinf=255.0, neginf=0.0)
    return np.ascontiguousarray(np.clip(numeric, 0.0, 255.0).astype(np.uint8))


def _validate_shapes(masks: Sequence[Any]) -> None:
    first_shape = masks[0].shape
    if any(mask.shape != first_shape for mask in masks[1:]):
        raise Sam3MaskCombineError(
            "mask_combine_shape_mismatch",
            "SAM3 mask combine requires all masks to share the same shape.",
        )


def _subtract_masks(masks: Sequence[Any], np: Any) -> Any:
    result = masks[0].copy()
    for mask in masks[1:]:
        result = np.where(mask > 0, 0, result).astype(np.uint8)
    return result


def _xor_masks(masks: Sequence[Any], np: Any) -> Any:
    stack = np.stack(masks, axis=0)
    coverage = stack > 0
    coverage_count = np.count_nonzero(coverage, axis=0)
    single_alpha = np.max(stack, axis=0)
    return np.where(coverage_count == 1, single_alpha, 0).astype(np.uint8)


__all__ = [
    "SAM3_MASK_COMBINE_SCHEMA_VERSION",
    "MaskCombineMode",
    "Sam3MaskCombineError",
    "combine_mask_arrays",
    "normalize_mask_combine_mode",
]
