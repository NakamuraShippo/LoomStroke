"""UI-independent SAM3 mask refinement helpers."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .runtime_imports import load_cv2, load_numpy

SAM3_MASK_REFINE_OPTIONS_SCHEMA_VERSION = "sam3.mask_refine_options.v1"
MAX_MASK_REFINE_RADIUS_PX = 128


class Sam3MaskRefineError(ValueError):
    """Raised when a SAM3 local mask refine operation cannot be performed."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class MaskRefineOptions:
    """Local mask refinement options for SAM3 mask assets."""

    feather_px: float = 0.0
    expand_px: int = 0
    contract_px: int = 0
    smooth_radius_px: float = 0.0
    fill_holes: bool = False
    remove_islands_below_px: int = 0
    invert: bool = False
    harden_threshold: float | None = None
    # Fill only enclosed holes smaller than this area (0 = off). Unlike
    # ``fill_holes`` it preserves large legitimate holes (e.g. background
    # visible between arms) while removing screentone pinholes.
    fill_holes_below_px: int = 0

    def normalized(self) -> MaskRefineOptions:
        """Return options clamped to the supported local refinement range."""

        return MaskRefineOptions(
            feather_px=_clamp_float(self.feather_px, 0.0, float(MAX_MASK_REFINE_RADIUS_PX)),
            expand_px=_clamp_int(self.expand_px, 0, MAX_MASK_REFINE_RADIUS_PX),
            contract_px=_clamp_int(self.contract_px, 0, MAX_MASK_REFINE_RADIUS_PX),
            smooth_radius_px=_clamp_float(
                self.smooth_radius_px, 0.0, float(MAX_MASK_REFINE_RADIUS_PX)
            ),
            fill_holes=bool(self.fill_holes),
            remove_islands_below_px=_clamp_int(self.remove_islands_below_px, 0, None),
            invert=bool(self.invert),
            harden_threshold=_normalize_threshold(self.harden_threshold),
            fill_holes_below_px=_clamp_int(self.fill_holes_below_px, 0, None),
        )

    def is_noop(self) -> bool:
        """Return True when the normalized options do not alter a mask."""

        options = self.normalized()
        return (
            options.feather_px <= 0.0
            and options.expand_px <= 0
            and options.contract_px <= 0
            and options.smooth_radius_px <= 0.0
            and not options.fill_holes
            and options.remove_islands_below_px <= 0
            and options.fill_holes_below_px <= 0
            and not options.invert
            and options.harden_threshold is None
        )

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe options payload using the SAM3 internal key style."""

        options = self.normalized()
        return {
            "schema_version": SAM3_MASK_REFINE_OPTIONS_SCHEMA_VERSION,
            "feather_px": options.feather_px,
            "expand_px": options.expand_px,
            "contract_px": options.contract_px,
            "smooth_radius_px": options.smooth_radius_px,
            "fill_holes": options.fill_holes,
            "remove_islands_below_px": options.remove_islands_below_px,
            "invert": options.invert,
            "harden_threshold": options.harden_threshold,
            "fill_holes_below_px": options.fill_holes_below_px,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object] | None) -> MaskRefineOptions:
        """Build normalized options from a mapping, ignoring unknown fields."""

        if not isinstance(value, Mapping):
            return cls()
        return cls(
            feather_px=_mapping_float(value, "feather_px", 0.0),
            expand_px=_mapping_int(value, "expand_px", 0),
            contract_px=_mapping_int(value, "contract_px", 0),
            smooth_radius_px=_mapping_float(value, "smooth_radius_px", 0.0),
            fill_holes=bool(value.get("fill_holes", False)),
            remove_islands_below_px=_mapping_int(value, "remove_islands_below_px", 0),
            invert=bool(value.get("invert", False)),
            harden_threshold=_mapping_threshold(value, "harden_threshold"),
            fill_holes_below_px=_mapping_int(value, "fill_holes_below_px", 0),
        ).normalized()


def refine_mask_array(
    mask: object,
    options: MaskRefineOptions | Mapping[str, object] | None = None,
) -> object:
    """Apply local SAM3 mask refinement options to a 2D alpha mask."""

    refine_options = _coerce_options(options).normalized()
    np, cv2 = _load_runtime()
    result = _as_uint8_mask(mask, np)

    if refine_options.is_noop():
        return result.copy()

    if refine_options.expand_px > 0:
        result = _binary_alpha(result, np)
        result = cv2.dilate(result, _ellipse_kernel(cv2, refine_options.expand_px))

    if refine_options.contract_px > 0:
        result = _binary_alpha(result, np)
        result = cv2.erode(result, _ellipse_kernel(cv2, refine_options.contract_px))

    if refine_options.fill_holes:
        result = _fill_holes(result, np, cv2)
    elif refine_options.fill_holes_below_px > 0:
        result = _fill_small_holes(result, refine_options.fill_holes_below_px, np, cv2)

    if refine_options.remove_islands_below_px > 0:
        result = _remove_small_islands(
            result,
            refine_options.remove_islands_below_px,
            np,
            cv2,
        )

    smooth_radius = _rounded_radius(refine_options.smooth_radius_px)
    if smooth_radius > 0:
        result = _smooth_binary(result, smooth_radius, np, cv2)

    feather_radius = _rounded_radius(refine_options.feather_px)
    if feather_radius > 0:
        kernel_size = feather_radius * 2 + 1
        sigma = max(feather_radius / 3.0, 0.1)
        result = cv2.GaussianBlur(
            result,
            (kernel_size, kernel_size),
            sigmaX=sigma,
            sigmaY=sigma,
            borderType=cv2.BORDER_REPLICATE,
        ).astype(np.uint8, copy=False)

    if refine_options.harden_threshold is not None:
        result = np.where(result >= refine_options.harden_threshold, 255, 0).astype(np.uint8)

    if refine_options.invert:
        result = (255 - result).astype(np.uint8, copy=False)

    return np.ascontiguousarray(result, dtype=np.uint8)


def _coerce_options(
    options: MaskRefineOptions | Mapping[str, object] | None,
) -> MaskRefineOptions:
    if isinstance(options, MaskRefineOptions):
        return options
    return MaskRefineOptions.from_dict(options)


def _load_runtime() -> tuple[Any, Any]:
    try:
        np = load_numpy()
    except ImportError as exc:
        raise Sam3MaskRefineError(
            "mask_refine_runtime_unavailable",
            "SAM3 local mask refine requires numpy.",
        ) from exc
    try:
        cv2 = load_cv2()
    except ImportError as exc:
        raise Sam3MaskRefineError(
            "mask_refine_runtime_unavailable",
            "SAM3 local mask refine requires opencv-python-headless.",
        ) from exc
    return np, cv2


def _as_uint8_mask(mask: object, np: Any) -> Any:
    try:
        array = np.asarray(mask)
    except Exception as exc:
        raise Sam3MaskRefineError(
            "mask_refine_invalid_mask",
            "SAM3 local mask refine requires a 2D mask-like input.",
        ) from exc

    if array.ndim != 2 or array.size <= 0:
        raise Sam3MaskRefineError(
            "mask_refine_invalid_mask",
            "SAM3 local mask refine requires a non-empty 2D mask.",
        )

    if array.dtype == np.bool_:
        return np.ascontiguousarray(array.astype(np.uint8) * 255)

    try:
        numeric = array.astype(np.float32, copy=False)
    except (TypeError, ValueError) as exc:
        raise Sam3MaskRefineError(
            "mask_refine_invalid_mask",
            "SAM3 local mask refine mask values must be numeric.",
        ) from exc

    numeric = np.nan_to_num(numeric, nan=0.0, posinf=255.0, neginf=0.0)
    return np.ascontiguousarray(np.clip(numeric, 0.0, 255.0).astype(np.uint8))


def _binary_alpha(mask: Any, np: Any) -> Any:
    return np.where(mask > 0, 255, 0).astype(np.uint8)


def _fill_holes(mask: Any, np: Any, cv2: Any) -> Any:
    binary = _binary_alpha(mask, np)
    background = (binary == 0).astype(np.uint8)
    component_count, labels, _stats, _centroids = cv2.connectedComponentsWithStats(
        background,
        connectivity=8,
    )
    if component_count <= 1:
        return binary

    # Label-indexed lookup instead of one full-image scan per component:
    # component counts can reach thousands on noisy 4K masks.
    fill_by_label = np.ones(component_count, dtype=bool)
    fill_by_label[0] = False
    for border in (labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1]):
        fill_by_label[np.unique(border)] = False

    return np.where(fill_by_label[labels], 255, binary).astype(np.uint8, copy=False)


def _fill_small_holes(mask: Any, max_area_px: int, np: Any, cv2: Any) -> Any:
    """Fill enclosed background components smaller than ``max_area_px``."""

    binary = _binary_alpha(mask, np)
    background = (binary == 0).astype(np.uint8)
    component_count, labels, stats, _centroids = cv2.connectedComponentsWithStats(
        background,
        connectivity=8,
    )
    if component_count <= 1:
        return binary

    fill_by_label = stats[:, cv2.CC_STAT_AREA] < max_area_px
    fill_by_label[0] = False
    for border in (labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1]):
        fill_by_label[np.unique(border)] = False

    return np.where(fill_by_label[labels], 255, binary).astype(np.uint8, copy=False)


def _remove_small_islands(mask: Any, min_area_px: int, np: Any, cv2: Any) -> Any:
    binary = _binary_alpha(mask, np)
    foreground = (binary > 0).astype(np.uint8)
    component_count, labels, stats, _centroids = cv2.connectedComponentsWithStats(
        foreground,
        connectivity=8,
    )
    if component_count <= 1:
        return binary

    keep_by_label = stats[:, cv2.CC_STAT_AREA] >= min_area_px
    keep_by_label[0] = False
    return np.where(keep_by_label[labels], 255, 0).astype(np.uint8)


def _smooth_binary(mask: Any, radius: int, np: Any, cv2: Any) -> Any:
    binary = _binary_alpha(mask, np)
    kernel = _ellipse_kernel(cv2, radius)
    opened = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    closed = cv2.morphologyEx(opened, cv2.MORPH_CLOSE, kernel)
    return closed.astype(np.uint8, copy=False)


def _ellipse_kernel(cv2: Any, radius: int) -> Any:
    radius = max(0, min(int(radius), MAX_MASK_REFINE_RADIUS_PX))
    size = radius * 2 + 1
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))


def _rounded_radius(value: float) -> int:
    return _clamp_int(round(_finite_float(value, 0.0)), 0, MAX_MASK_REFINE_RADIUS_PX)


def _mapping_float(value: Mapping[str, object], key: str, default: float) -> float:
    return _finite_float(value.get(key, default), default)


def _mapping_int(value: Mapping[str, object], key: str, default: int) -> int:
    return int(_finite_float(value.get(key, default), float(default)))


def _mapping_threshold(value: Mapping[str, object], key: str) -> float | None:
    if key not in value or value.get(key) is None:
        return None
    return _normalize_threshold(value.get(key))


def _normalize_threshold(value: object) -> float | None:
    if value is None:
        return None
    threshold = _finite_float(value, math.nan)
    if not math.isfinite(threshold):
        return None
    return _clamp_float(threshold, 0.0, 255.0)


def _clamp_float(value: object, minimum: float, maximum: float) -> float:
    number = _finite_float(value, minimum)
    return max(minimum, min(maximum, number))


def _clamp_int(value: object, minimum: int, maximum: int | None) -> int:
    number = int(_finite_float(value, float(minimum)))
    if maximum is None:
        return max(minimum, number)
    return max(minimum, min(maximum, number))


def _finite_float(value: object, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number


__all__ = [
    "MAX_MASK_REFINE_RADIUS_PX",
    "SAM3_MASK_REFINE_OPTIONS_SCHEMA_VERSION",
    "MaskRefineOptions",
    "Sam3MaskRefineError",
    "refine_mask_array",
]
