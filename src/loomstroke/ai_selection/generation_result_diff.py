"""Create SAM3 mask assets from preview generation image diffs."""

from __future__ import annotations

import hashlib
import io
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .assets import Sam3AssetStore
from .data_model import MaskAsset
from .runtime_imports import load_numpy, load_pillow_image

SAM3_GENERATION_RESULT_DIFF_QA_SCHEMA_VERSION = "sam3.generation_result_diff_qa.v1"
_PROTECTED_DIFF_WARNING_RATIO = 0.01


class Sam3GenerationResultDiffError(ValueError):
    """Raised when a generation result diff mask cannot be created."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class Sam3GenerationResultDiffQAError(ValueError):
    """Raised when a generation result diff mask cannot be analyzed."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Sam3GenerationResultDiffQAWarning:
    """One local warning for generated result changes."""

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
class Sam3GenerationResultDiffQA:
    """Local quality summary for a generation result diff mask."""

    ok: bool
    warnings: tuple[Sam3GenerationResultDiffQAWarning, ...]
    changed_area_ratio: float
    protected_region_coverage: float | None
    protected_overlap_area_ratio: float | None
    recommended_actions: tuple[str, ...]
    schema_version: str = SAM3_GENERATION_RESULT_DIFF_QA_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "ok": self.ok,
            "warnings": [warning.to_dict() for warning in self.warnings],
            "changedAreaRatio": self.changed_area_ratio,
            "protectedRegionCoverage": self.protected_region_coverage,
            "protectedOverlapAreaRatio": self.protected_overlap_area_ratio,
            "recommendedActions": list(self.recommended_actions),
        }


def create_generation_result_diff_mask_asset(
    asset_store: Sam3AssetStore,
    before_png: bytes,
    after_png: bytes,
    *,
    output_asset_id: str,
    canvas_width: int,
    canvas_height: int,
    constrain_mask_asset_id: str | None = None,
    threshold: int = 16,
    source_candidate_id: str | None = None,
    metadata: Mapping[str, object] | None = None,
) -> MaskAsset:
    """Create a cropped alpha mask from before/after preview image differences."""

    target_width, target_height = _validate_canvas_size(canvas_width, canvas_height)
    output_id = str(output_asset_id).strip()
    if not output_id:
        raise Sam3GenerationResultDiffError(
            "diff_mask_asset_id_missing",
            "SAM3 generation diff mask requires an output asset id.",
        )
    threshold_value = _validate_threshold(threshold)

    np = _load_numpy()
    Image = _load_pillow_image()
    constrain_id = _clean_optional_text(constrain_mask_asset_id)
    diff_alpha = _build_generation_diff_alpha(
        before_png,
        after_png,
        canvas_width=target_width,
        canvas_height=target_height,
        threshold=threshold_value,
        asset_store=asset_store,
        constrain_mask_asset_id=constrain_id,
        np=np,
        image_module=Image,
    )

    bbox = _nonzero_bbox(diff_alpha, np)
    if bbox is None:
        raise Sam3GenerationResultDiffError(
            "generation_diff_mask_empty",
            "SAM3 generation diff did not produce a non-empty mask.",
        )

    x, y, width, height = bbox
    cropped_alpha = diff_alpha[y : y + height, x : x + width]
    png_bytes = _encode_alpha_png(cropped_alpha, image_module=Image)
    output_metadata = _diff_metadata(
        metadata,
        threshold=threshold_value,
        constrain_mask_asset_id=constrain_id,
    )

    try:
        return asset_store.save_mask_asset(
            png_bytes,
            asset_id=output_id,
            kind="alpha_mask",
            width=width,
            height=height,
            bbox=bbox,
            source_candidate_id=source_candidate_id,
            metadata=output_metadata,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise Sam3GenerationResultDiffError(
            "generation_diff_mask_save_failed",
            f"SAM3 generation diff mask could not be saved: {output_id!r}.",
        ) from exc


def analyze_generation_result_diff_mask(
    asset_store: Sam3AssetStore,
    diff_mask_asset_id: str,
    *,
    canvas_width: int,
    canvas_height: int,
    protected_mask_asset_id: str | None = None,
) -> Sam3GenerationResultDiffQA:
    """Analyze a generated result diff mask against an optional protected mask."""

    target_width, target_height = _validate_diff_qa_canvas_size(canvas_width, canvas_height)
    diff_id = str(diff_mask_asset_id).strip()
    if not diff_id:
        raise Sam3GenerationResultDiffQAError(
            "generation_diff_qa_mask_missing",
            "SAM3 generation diff QA requires a diff mask asset id.",
        )

    np = _load_numpy_for_diff_qa()
    image_module = _load_pillow_image_for_diff_qa()
    diff_alpha = _load_mask_asset_alpha_to_canvas(
        asset_store,
        diff_id,
        canvas_width=target_width,
        canvas_height=target_height,
        np=np,
        image_module=image_module,
        missing_code="generation_diff_qa_mask_missing",
        invalid_code="generation_diff_qa_mask_invalid",
        message_prefix="SAM3 generation diff QA mask",
        error_type=Sam3GenerationResultDiffQAError,
    )

    protected_id = _clean_optional_text(protected_mask_asset_id)
    protected_alpha = (
        _load_mask_asset_alpha_to_canvas(
            asset_store,
            protected_id,
            canvas_width=target_width,
            canvas_height=target_height,
            np=np,
            image_module=image_module,
            missing_code="generation_diff_qa_protected_mask_missing",
            invalid_code="generation_diff_qa_protected_mask_invalid",
            message_prefix="SAM3 generation diff QA protected mask",
            error_type=Sam3GenerationResultDiffQAError,
        )
        if protected_id is not None
        else None
    )

    return _analyze_generation_result_diff_alpha(
        diff_alpha,
        canvas_width=target_width,
        canvas_height=target_height,
        protected_alpha=protected_alpha,
        np=np,
    )


def run_generation_result_diff_qa(
    before_png: bytes,
    after_png: bytes,
    *,
    canvas_width: int,
    canvas_height: int,
    asset_store: Sam3AssetStore | None = None,
    protected_mask_asset_id: str | None = None,
    constrain_mask_asset_id: str | None = None,
    threshold: int = 16,
) -> Sam3GenerationResultDiffQA:
    """Run the generation result diff QA fully in memory.

    Unlike ``create_generation_result_diff_mask_asset`` +
    ``analyze_generation_result_diff_mask``, nothing is persisted to the
    asset store, and an empty diff is reported through the regular QA
    result (``ok: False`` + ``generation_diff_empty``) instead of raising.
    """

    target_width, target_height = _validate_diff_qa_canvas_size(canvas_width, canvas_height)
    threshold_value = _validate_threshold(threshold)
    protected_id = _clean_optional_text(protected_mask_asset_id)
    constrain_id = _clean_optional_text(constrain_mask_asset_id)
    if asset_store is None and protected_id is not None:
        raise Sam3GenerationResultDiffQAError(
            "generation_diff_qa_protected_mask_missing",
            "SAM3 generation diff QA protected mask requires an asset store.",
        )
    if asset_store is None and constrain_id is not None:
        raise Sam3GenerationResultDiffError(
            "generation_diff_constrain_mask_missing",
            "SAM3 generation diff constrain mask requires an asset store.",
        )

    np = _load_numpy_for_diff_qa()
    image_module = _load_pillow_image_for_diff_qa()
    diff_alpha = _build_generation_diff_alpha(
        before_png,
        after_png,
        canvas_width=target_width,
        canvas_height=target_height,
        threshold=threshold_value,
        asset_store=asset_store,
        constrain_mask_asset_id=constrain_id,
        np=np,
        image_module=image_module,
    )

    protected_alpha = (
        _load_mask_asset_alpha_to_canvas(
            asset_store,
            protected_id,
            canvas_width=target_width,
            canvas_height=target_height,
            np=np,
            image_module=image_module,
            missing_code="generation_diff_qa_protected_mask_missing",
            invalid_code="generation_diff_qa_protected_mask_invalid",
            message_prefix="SAM3 generation diff QA protected mask",
            error_type=Sam3GenerationResultDiffQAError,
        )
        if protected_id is not None
        else None
    )

    return _analyze_generation_result_diff_alpha(
        diff_alpha,
        canvas_width=target_width,
        canvas_height=target_height,
        protected_alpha=protected_alpha,
        np=np,
    )


def _build_generation_diff_alpha(
    before_png: bytes,
    after_png: bytes,
    *,
    canvas_width: int,
    canvas_height: int,
    threshold: int,
    asset_store: Sam3AssetStore | None,
    constrain_mask_asset_id: str | None,
    np: Any,
    image_module: Any,
) -> Any:
    before_rgba = _decode_canvas_rgba(
        before_png,
        image_label="before",
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        image_module=image_module,
        np=np,
    )
    after_rgba = _decode_canvas_rgba(
        after_png,
        image_label="after",
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        image_module=image_module,
        np=np,
    )

    channel_diff = np.abs(after_rgba.astype(np.int16) - before_rgba.astype(np.int16))
    diff_alpha = np.where(np.max(channel_diff, axis=2) > threshold, 255, 0).astype(np.uint8)

    if constrain_mask_asset_id is not None:
        constrain_alpha = _load_constrain_mask_alpha(
            asset_store,
            constrain_mask_asset_id,
            canvas_width=canvas_width,
            canvas_height=canvas_height,
            np=np,
        )
        diff_alpha = np.minimum(diff_alpha, constrain_alpha)
    return diff_alpha


def _validate_canvas_size(canvas_width: int, canvas_height: int) -> tuple[int, int]:
    try:
        width = int(canvas_width)
        height = int(canvas_height)
    except (TypeError, ValueError) as exc:
        raise Sam3GenerationResultDiffError(
            "invalid_canvas_size",
            "SAM3 generation diff requires a valid canvas size.",
        ) from exc
    if width <= 0 or height <= 0:
        raise Sam3GenerationResultDiffError(
            "invalid_canvas_size",
            "SAM3 generation diff requires a positive canvas size.",
        )
    return (width, height)


def _validate_diff_qa_canvas_size(canvas_width: int, canvas_height: int) -> tuple[int, int]:
    try:
        width = int(canvas_width)
        height = int(canvas_height)
    except (TypeError, ValueError) as exc:
        raise Sam3GenerationResultDiffQAError(
            "invalid_canvas_size",
            "SAM3 generation diff QA requires a valid canvas size.",
        ) from exc
    if width <= 0 or height <= 0:
        raise Sam3GenerationResultDiffQAError(
            "invalid_canvas_size",
            "SAM3 generation diff QA requires a positive canvas size.",
        )
    return (width, height)


def _validate_threshold(threshold: int) -> int:
    if isinstance(threshold, bool):
        raise Sam3GenerationResultDiffError(
            "invalid_threshold",
            "SAM3 generation diff threshold must be an integer from 0 to 255.",
        )
    try:
        value = int(threshold)
    except (TypeError, ValueError) as exc:
        raise Sam3GenerationResultDiffError(
            "invalid_threshold",
            "SAM3 generation diff threshold must be an integer from 0 to 255.",
        ) from exc
    if value < 0 or value > 255:
        raise Sam3GenerationResultDiffError(
            "invalid_threshold",
            "SAM3 generation diff threshold must be an integer from 0 to 255.",
        )
    return value


def _load_numpy() -> Any:
    try:
        return load_numpy()
    except ImportError as exc:
        raise Sam3GenerationResultDiffError(
            "generation_diff_runtime_unavailable",
            "SAM3 generation diff mask requires numpy.",
        ) from exc


def _load_numpy_for_diff_qa() -> Any:
    try:
        return _load_numpy()
    except Sam3GenerationResultDiffError as exc:
        raise Sam3GenerationResultDiffQAError(
            "generation_diff_qa_runtime_unavailable",
            "SAM3 generation diff QA requires numpy.",
        ) from exc


def _load_pillow_image() -> Any:
    try:
        return load_pillow_image()
    except ImportError as exc:
        raise Sam3GenerationResultDiffError(
            "generation_diff_runtime_unavailable",
            "SAM3 generation diff mask requires Pillow.",
        ) from exc


def _load_pillow_image_for_diff_qa() -> Any:
    try:
        return _load_pillow_image()
    except Sam3GenerationResultDiffError as exc:
        raise Sam3GenerationResultDiffQAError(
            "generation_diff_qa_runtime_unavailable",
            "SAM3 generation diff QA requires Pillow.",
        ) from exc


def _decode_canvas_rgba(
    payload: bytes,
    *,
    image_label: str,
    canvas_width: int,
    canvas_height: int,
    image_module: Any,
    np: Any,
) -> Any:
    if not isinstance(payload, bytes | bytearray) or not payload:
        raise Sam3GenerationResultDiffError(
            "generation_diff_input_missing",
            f"SAM3 generation diff requires {image_label} PNG bytes.",
        )
    try:
        with image_module.open(io.BytesIO(bytes(payload))) as image:
            if image.size != (canvas_width, canvas_height):
                raise Sam3GenerationResultDiffError(
                    "generation_diff_size_mismatch",
                    "SAM3 generation diff input image size must match the canvas size.",
                )
            return np.ascontiguousarray(np.asarray(image.convert("RGBA"), dtype=np.uint8))
    except Sam3GenerationResultDiffError:
        raise
    except Exception as exc:
        raise Sam3GenerationResultDiffError(
            "generation_diff_input_unreadable",
            f"SAM3 generation diff {image_label} image is not readable.",
        ) from exc


def _load_constrain_mask_alpha(
    asset_store: Sam3AssetStore,
    asset_id: str,
    *,
    canvas_width: int,
    canvas_height: int,
    np: Any,
) -> Any:
    return _load_mask_asset_alpha_to_canvas(
        asset_store,
        asset_id,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        np=np,
        image_module=_load_pillow_image(),
        missing_code="generation_diff_constrain_mask_missing",
        invalid_code="generation_diff_constrain_mask_invalid",
        message_prefix="SAM3 generation diff constrain mask",
        error_type=Sam3GenerationResultDiffError,
    )


def _load_mask_asset_alpha_to_canvas(
    asset_store: Sam3AssetStore,
    asset_id: str,
    *,
    canvas_width: int,
    canvas_height: int,
    np: Any,
    image_module: Any,
    missing_code: str,
    invalid_code: str,
    message_prefix: str,
    error_type: type[Sam3GenerationResultDiffError] | type[Sam3GenerationResultDiffQAError],
) -> Any:
    try:
        asset = asset_store.load_mask_asset(asset_id)
    except (OSError, ValueError) as exc:
        raise error_type(
            missing_code,
            f"{message_prefix} cannot be loaded: {asset_id!r}.",
        ) from exc

    path = asset_store.mask_asset_path(asset)
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise error_type(
            missing_code,
            f"{message_prefix} artifact is missing: {asset_id!r}.",
        ) from exc
    expected_hash = str(asset.content_hash or "").strip()
    actual_hash = hashlib.sha256(payload).hexdigest()
    if expected_hash and actual_hash != expected_hash:
        raise error_type(
            invalid_code,
            f"{message_prefix} hash mismatch: {asset_id!r}.",
        )

    try:
        with image_module.open(io.BytesIO(payload)) as image:
            if image.size != (int(asset.width), int(asset.height)):
                raise error_type(
                    invalid_code,
                    f"{message_prefix} size does not match its manifest.",
                )
            alpha = image.getchannel("A") if "A" in image.getbands() else image.convert("L")
            cropped_alpha = np.ascontiguousarray(np.asarray(alpha, dtype=np.uint8))
    except (Sam3GenerationResultDiffError, Sam3GenerationResultDiffQAError):
        raise
    except Exception as exc:
        raise error_type(
            invalid_code,
            f"{message_prefix} is not readable: {asset_id!r}.",
        ) from exc

    return _asset_alpha_to_canvas(
        cropped_alpha,
        asset,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        np=np,
        invalid_code=invalid_code,
        message_prefix=message_prefix,
        error_type=error_type,
    )


def _asset_alpha_to_canvas(
    alpha: Any,
    asset: MaskAsset,
    *,
    canvas_width: int,
    canvas_height: int,
    np: Any,
    invalid_code: str = "generation_diff_constrain_mask_invalid",
    message_prefix: str = "SAM3 generation diff constrain mask",
    error_type: type[Sam3GenerationResultDiffError] | type[Sam3GenerationResultDiffQAError] = (
        Sam3GenerationResultDiffError
    ),
) -> Any:
    x, y, width, height = (int(item) for item in asset.bbox)
    if (
        x < 0
        or y < 0
        or width <= 0
        or height <= 0
        or x + width > canvas_width
        or y + height > canvas_height
        or int(asset.width) != width
        or int(asset.height) != height
        or int(alpha.shape[1]) != width
        or int(alpha.shape[0]) != height
    ):
        raise error_type(
            invalid_code,
            f"{message_prefix} bbox is outside the canvas.",
        )
    canvas_alpha = np.zeros((canvas_height, canvas_width), dtype=np.uint8)
    canvas_alpha[y : y + height, x : x + width] = alpha
    return canvas_alpha


def _analyze_generation_result_diff_alpha(
    diff_alpha: Any,
    *,
    canvas_width: int,
    canvas_height: int,
    protected_alpha: Any | None,
    np: Any,
) -> Sam3GenerationResultDiffQA:
    diff = np.asarray(diff_alpha, dtype=np.uint8)
    canvas_area = max(1, int(canvas_width) * int(canvas_height))
    changed_total = float(np.sum(diff, dtype=np.float64))
    changed_area_ratio = changed_total / float(255 * canvas_area)
    protected_region_coverage, protected_overlap_area_ratio = _protected_diff_coverage(
        diff,
        protected_alpha,
        canvas_area=canvas_area,
        np=np,
    )

    warnings: list[Sam3GenerationResultDiffQAWarning] = []
    actions: list[str] = []
    _append_diff_qa_warning_if(
        warnings,
        actions,
        changed_area_ratio == 0.0,
        code="generation_diff_empty",
        message="SAM3 generation result diff mask is empty.",
        action="review_preview_result",
    )
    _append_diff_qa_warning_if(
        warnings,
        actions,
        protected_region_coverage is not None
        and protected_region_coverage > _PROTECTED_DIFF_WARNING_RATIO,
        code="protected_region_changed",
        message="SAM3 generation result changed a protected region.",
        action="review_or_regenerate_with_protection",
    )

    return Sam3GenerationResultDiffQA(
        ok=not warnings,
        warnings=tuple(warnings),
        changed_area_ratio=changed_area_ratio,
        protected_region_coverage=protected_region_coverage,
        protected_overlap_area_ratio=protected_overlap_area_ratio,
        recommended_actions=tuple(actions),
    )


def _append_diff_qa_warning_if(
    warnings: list[Sam3GenerationResultDiffQAWarning],
    actions: list[str],
    condition: bool,
    *,
    code: str,
    message: str,
    action: str,
) -> None:
    if not condition:
        return
    warnings.append(Sam3GenerationResultDiffQAWarning(code=code, message=message))
    if action not in actions:
        actions.append(action)


def _protected_diff_coverage(
    diff_alpha: Any,
    protected_alpha: Any | None,
    *,
    canvas_area: int,
    np: Any,
) -> tuple[float | None, float | None]:
    if protected_alpha is None:
        return (None, None)
    protected = np.asarray(protected_alpha, dtype=np.uint8)
    protected_total = float(np.sum(protected, dtype=np.float64))
    if protected_total <= 0.0:
        return (0.0, 0.0)
    overlap = np.minimum(diff_alpha, protected)
    overlap_total = float(np.sum(overlap, dtype=np.float64))
    return (
        overlap_total / protected_total,
        overlap_total / float(255 * canvas_area),
    )


def _nonzero_bbox(alpha: Any, np: Any) -> tuple[int, int, int, int] | None:
    ys, xs = np.nonzero(alpha)
    if len(xs) == 0 or len(ys) == 0:
        return None
    x0 = int(xs.min())
    y0 = int(ys.min())
    x1 = int(xs.max()) + 1
    y1 = int(ys.max()) + 1
    return (x0, y0, x1 - x0, y1 - y0)


def _encode_alpha_png(alpha: Any, *, image_module: Any) -> bytes:
    buffer = io.BytesIO()
    image_module.fromarray(alpha).save(buffer, format="PNG")
    return buffer.getvalue()


def _diff_metadata(
    metadata: Mapping[str, object] | None,
    *,
    threshold: int,
    constrain_mask_asset_id: str | None,
) -> dict[str, object]:
    payload = dict(metadata or {}) if isinstance(metadata, Mapping) else {}
    payload.update(
        {
            "operation": "generation_result_diff_mask",
            "threshold": int(threshold),
        }
    )
    if constrain_mask_asset_id is not None:
        payload["constrainMaskAssetId"] = constrain_mask_asset_id
    return payload


def _clean_optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "SAM3_GENERATION_RESULT_DIFF_QA_SCHEMA_VERSION",
    "Sam3GenerationResultDiffError",
    "Sam3GenerationResultDiffQA",
    "Sam3GenerationResultDiffQAError",
    "Sam3GenerationResultDiffQAWarning",
    "analyze_generation_result_diff_mask",
    "create_generation_result_diff_mask_asset",
    "run_generation_result_diff_qa",
]
