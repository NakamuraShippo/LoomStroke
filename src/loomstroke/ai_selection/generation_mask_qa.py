"""Local preflight QA for SAM3 generation masks."""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .assets import Sam3AssetStore
from .layer_mask_adapter import (
    Sam3LayerMaskAdapterError,
    build_layer_mask_image_from_asset,
)
from .runtime_imports import load_numpy

SAM3_GENERATION_MASK_QA_SCHEMA_VERSION = "sam3.generation_mask_qa.v1"

_SMALL_MASK_RATIO = 0.001
_LARGE_MASK_RATIO = 0.95
_PROTECTED_COVERAGE_WARNING_RATIO = 0.01


class Sam3GenerationMaskQAError(ValueError):
    """Raised when a SAM3 generation mask cannot be analyzed."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Sam3GenerationMaskQAWarning:
    """One local preflight warning for a SAM3 generation mask."""

    code: str
    message: str
    severity: str = "warning"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
        }


@dataclass(frozen=True, slots=True)
class Sam3GenerationMaskQA:
    """Local quality summary for a SAM3 mask before generation submit."""

    ok: bool
    warnings: tuple[Sam3GenerationMaskQAWarning, ...]
    mask_area_ratio: float
    touches_canvas_border: bool
    holes_count: int
    likely_text_overlap: bool
    protected_region_coverage: float | None
    recommended_actions: tuple[str, ...]
    schema_version: str = SAM3_GENERATION_MASK_QA_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "ok": self.ok,
            "warnings": [warning.to_dict() for warning in self.warnings],
            "maskAreaRatio": self.mask_area_ratio,
            "touchesCanvasBorder": self.touches_canvas_border,
            "holesCount": self.holes_count,
            "likelyTextOverlap": self.likely_text_overlap,
            "protectedRegionCoverage": self.protected_region_coverage,
            "recommendedActions": list(self.recommended_actions),
        }


def analyze_generation_mask_asset(
    asset_store: Sam3AssetStore,
    asset_id: str,
    *,
    canvas_width: int,
    canvas_height: int,
    text_regions: Sequence[tuple[int, int, int, int]] = (),
    protected_mask_asset_id: str | None = None,
) -> Sam3GenerationMaskQA:
    """Analyze a SAM3 mask asset before using it for generation."""

    target_width, target_height = _validate_canvas_size(canvas_width, canvas_height)
    mask_alpha = _load_canvas_mask_alpha(
        asset_store,
        asset_id,
        canvas_width=target_width,
        canvas_height=target_height,
    )
    protected_alpha = (
        _load_canvas_mask_alpha(
            asset_store,
            protected_mask_asset_id,
            canvas_width=target_width,
            canvas_height=target_height,
        )
        if protected_mask_asset_id
        else None
    )

    return _analyze_mask_alpha(
        mask_alpha,
        canvas_width=target_width,
        canvas_height=target_height,
        text_regions=text_regions,
        protected_alpha=protected_alpha,
    )


def _analyze_mask_alpha(
    mask_alpha: Any,
    *,
    canvas_width: int,
    canvas_height: int,
    text_regions: Sequence[tuple[int, int, int, int]],
    protected_alpha: Any | None,
) -> Sam3GenerationMaskQA:
    np = _load_numpy()
    alpha = np.asarray(mask_alpha, dtype=np.uint8)
    total_alpha = float(np.sum(alpha, dtype=np.float64))
    canvas_area = max(1, int(canvas_width) * int(canvas_height))
    mask_area_ratio = total_alpha / float(255 * canvas_area)
    positive = alpha > 0
    touches_canvas_border = _touches_border(positive)
    holes_count = _count_holes(positive, np)
    likely_text_overlap = _has_text_overlap(positive, text_regions)
    protected_region_coverage = _protected_coverage(alpha, protected_alpha, np)

    warnings: list[Sam3GenerationMaskQAWarning] = []
    actions: list[str] = []
    _append_warning_if(
        warnings,
        actions,
        mask_area_ratio == 0.0,
        code="mask_empty",
        message="SAM3 generation mask is empty.",
        action="select_candidate",
    )
    _append_warning_if(
        warnings,
        actions,
        0.0 < mask_area_ratio < _SMALL_MASK_RATIO,
        code="mask_too_small",
        message="SAM3 generation mask covers a very small area.",
        action="refine_expand",
    )
    _append_warning_if(
        warnings,
        actions,
        mask_area_ratio > _LARGE_MASK_RATIO,
        code="mask_too_large",
        message="SAM3 generation mask covers almost the entire canvas.",
        action="refine_contract_or_reselect",
    )
    _append_warning_if(
        warnings,
        actions,
        touches_canvas_border,
        code="mask_touches_canvas_border",
        message="SAM3 generation mask touches the canvas border.",
        action="review_canvas_border",
    )
    _append_warning_if(
        warnings,
        actions,
        holes_count > 0,
        code="mask_has_holes",
        message="SAM3 generation mask contains enclosed holes.",
        action="refine_fill_holes",
    )
    _append_warning_if(
        warnings,
        actions,
        likely_text_overlap,
        code="mask_overlaps_text",
        message="SAM3 generation mask overlaps a text region.",
        action="protect_text",
    )
    _append_warning_if(
        warnings,
        actions,
        protected_region_coverage is not None
        and protected_region_coverage > _PROTECTED_COVERAGE_WARNING_RATIO,
        code="protected_region_covered",
        message="SAM3 generation mask overlaps a protected region.",
        action="subtract_protected_region",
    )

    return Sam3GenerationMaskQA(
        ok=mask_area_ratio > 0.0,
        warnings=tuple(warnings),
        mask_area_ratio=mask_area_ratio,
        touches_canvas_border=touches_canvas_border,
        holes_count=holes_count,
        likely_text_overlap=likely_text_overlap,
        protected_region_coverage=protected_region_coverage,
        recommended_actions=tuple(actions),
    )


def _append_warning_if(
    warnings: list[Sam3GenerationMaskQAWarning],
    actions: list[str],
    condition: bool,
    *,
    code: str,
    message: str,
    action: str,
) -> None:
    if not condition:
        return
    warnings.append(Sam3GenerationMaskQAWarning(code=code, message=message))
    if action not in actions:
        actions.append(action)


def _load_canvas_mask_alpha(
    asset_store: Sam3AssetStore,
    asset_id: str,
    *,
    canvas_width: int,
    canvas_height: int,
) -> Any:
    try:
        image = build_layer_mask_image_from_asset(
            asset_store,
            asset_id,
            canvas_width=canvas_width,
            canvas_height=canvas_height,
        )
    except Sam3LayerMaskAdapterError as exc:
        raise Sam3GenerationMaskQAError(exc.code, str(exc)) from exc
    return _layer_mask_image_to_alpha_array(image)


def _layer_mask_image_to_alpha_array(image: Any) -> Any:
    from PySide6.QtGui import QImage  # noqa: PLC0415

    np = _load_numpy()
    source = image.convertToFormat(QImage.Format.Format_ARGB32)
    width = int(source.width())
    height = int(source.height())
    bytes_per_line = int(source.bytesPerLine())
    byte_count = bytes_per_line * height
    bits = source.bits()
    if hasattr(bits, "setsize"):
        bits.setsize(byte_count)
    data = np.frombuffer(bits, dtype=np.uint8, count=byte_count).reshape(
        height,
        bytes_per_line,
    )
    color_bytes = data[:, : width * 4].reshape(height, width, 4)
    return np.ascontiguousarray(np.max(color_bytes[:, :, :3], axis=2).astype(np.uint8))


def _touches_border(positive: Any) -> bool:
    if positive.size == 0:
        return False
    return bool(
        positive[0, :].any()
        or positive[-1, :].any()
        or positive[:, 0].any()
        or positive[:, -1].any()
    )


def _count_holes(positive: Any, np: Any) -> int:
    if not bool(positive.any()):
        return 0
    y_coords, x_coords = np.nonzero(positive)
    y0 = int(y_coords.min())
    y1 = int(y_coords.max()) + 1
    x0 = int(x_coords.min())
    x1 = int(x_coords.max()) + 1
    background = ~positive[y0:y1, x0:x1]
    if not bool(background.any()):
        return 0

    height, width = background.shape
    visited = np.zeros(background.shape, dtype=bool)
    holes = 0
    for start_y in range(height):
        for start_x in range(width):
            if visited[start_y, start_x] or not bool(background[start_y, start_x]):
                continue
            touches_bbox_edge = False
            queue: deque[tuple[int, int]] = deque([(start_y, start_x)])
            visited[start_y, start_x] = True
            while queue:
                y, x = queue.popleft()
                if y == 0 or x == 0 or y == height - 1 or x == width - 1:
                    touches_bbox_edge = True
                for next_y, next_x in (
                    (y - 1, x),
                    (y + 1, x),
                    (y, x - 1),
                    (y, x + 1),
                ):
                    if (
                        0 <= next_y < height
                        and 0 <= next_x < width
                        and not visited[next_y, next_x]
                        and bool(background[next_y, next_x])
                    ):
                        visited[next_y, next_x] = True
                        queue.append((next_y, next_x))
            if not touches_bbox_edge:
                holes += 1
    return holes


def _has_text_overlap(
    positive: Any,
    text_regions: Sequence[tuple[int, int, int, int]],
) -> bool:
    height, width = positive.shape
    for raw_rect in text_regions:
        clipped = _clip_rect(raw_rect, canvas_width=width, canvas_height=height)
        if clipped is None:
            continue
        x, y, rect_width, rect_height = clipped
        if bool(positive[y : y + rect_height, x : x + rect_width].any()):
            return True
    return False


def _protected_coverage(alpha: Any, protected_alpha: Any | None, np: Any) -> float | None:
    if protected_alpha is None:
        return None
    protected = np.asarray(protected_alpha, dtype=np.uint8)
    protected_total = float(np.sum(protected, dtype=np.float64))
    if protected_total <= 0.0:
        return 0.0
    overlap = np.minimum(alpha, protected)
    return float(np.sum(overlap, dtype=np.float64) / protected_total)


def _clip_rect(
    rect: tuple[int, int, int, int],
    *,
    canvas_width: int,
    canvas_height: int,
) -> tuple[int, int, int, int] | None:
    try:
        x, y, width, height = (int(item) for item in rect)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    x0 = max(0, x)
    y0 = max(0, y)
    x1 = min(canvas_width, x + width)
    y1 = min(canvas_height, y + height)
    if x0 >= x1 or y0 >= y1:
        return None
    return (x0, y0, x1 - x0, y1 - y0)


def _validate_canvas_size(canvas_width: int, canvas_height: int) -> tuple[int, int]:
    try:
        width = int(canvas_width)
        height = int(canvas_height)
    except (TypeError, ValueError) as exc:
        raise Sam3GenerationMaskQAError(
            "invalid_canvas_size",
            "SAM3 generation mask QA requires a valid canvas size.",
        ) from exc
    if width <= 0 or height <= 0:
        raise Sam3GenerationMaskQAError(
            "invalid_canvas_size",
            "SAM3 generation mask QA requires a positive canvas size.",
        )
    return width, height


def _load_numpy() -> Any:
    try:
        return load_numpy()
    except ImportError as exc:
        raise Sam3GenerationMaskQAError(
            "generation_mask_qa_runtime_unavailable",
            "SAM3 generation mask QA requires numpy.",
        ) from exc


__all__ = [
    "SAM3_GENERATION_MASK_QA_SCHEMA_VERSION",
    "Sam3GenerationMaskQA",
    "Sam3GenerationMaskQAError",
    "Sam3GenerationMaskQAWarning",
    "analyze_generation_mask_asset",
]
