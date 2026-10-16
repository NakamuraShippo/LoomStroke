"""Read-only speech bubble and text protection planning for SAM3 manga masks."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from .assets import Sam3AssetStore
from .data_model import MaskAsset
from .manga_mask_categories import MangaMaskCategory, manga_mask_category_definition
from .manga_mask_package import Sam3ComicMaskCandidateRef, Sam3ComicMaskPackage
from .mask_combine import Sam3MaskCombineError, combine_mask_arrays
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

SAM3_SPEECH_BUBBLE_PROTECT_PLAN_SCHEMA_VERSION = "sam3.speech_bubble_protect_plan.v1"
SAM3_SPEECH_BUBBLE_PROTECT_MASK_RESULT_SCHEMA_VERSION = "sam3.speech_bubble_protect_mask_result.v1"

_NO_CANDIDATE_REASON = "speech_bubble_protect_candidate_missing"
_NO_ACCEPTED_REASON = "speech_bubble_protect_no_accepted_candidates"
_MASK_ASSET_MISSING_REASON = "speech_bubble_protect_mask_asset_missing"
_PLAN_NOT_READY_REASON = "speech_bubble_protect_plan_not_ready"


class Sam3SpeechBubbleProtectMaskAssetError(ValueError):
    """Raised when a speech bubble protect mask cannot be saved as an asset."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Sam3SpeechBubbleProtectPlan:
    ready: bool
    mask_asset_ids: tuple[str, ...] = field(default_factory=tuple)
    candidate_ids: tuple[str, ...] = field(default_factory=tuple)
    categories: tuple[MangaMaskCategory, ...] = field(default_factory=tuple)
    blocked_reason_codes: tuple[str, ...] = field(default_factory=tuple)
    requires_user_confirmation: bool = True
    schema_version: str = SAM3_SPEECH_BUBBLE_PROTECT_PLAN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "mask_asset_ids", _string_tuple(self.mask_asset_ids))
        object.__setattr__(self, "candidate_ids", _string_tuple(self.candidate_ids))
        object.__setattr__(self, "categories", tuple(self.categories))
        object.__setattr__(
            self,
            "blocked_reason_codes",
            _string_tuple(self.blocked_reason_codes),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "ready": self.ready,
            "maskAssetIds": list(self.mask_asset_ids),
            "candidateIds": list(self.candidate_ids),
            "categories": list(self.categories),
            "blockedReasonCodes": list(self.blocked_reason_codes),
            "requiresUserConfirmation": self.requires_user_confirmation,
        }


@dataclass(frozen=True, slots=True)
class Sam3SpeechBubbleProtectMaskResult:
    ready: bool
    mask: object | None = None
    mask_asset_ids: tuple[str, ...] = field(default_factory=tuple)
    blocked_reason_codes: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = SAM3_SPEECH_BUBBLE_PROTECT_MASK_RESULT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "mask_asset_ids", _string_tuple(self.mask_asset_ids))
        object.__setattr__(
            self,
            "blocked_reason_codes",
            _string_tuple(self.blocked_reason_codes),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "ready": self.ready,
            "maskAvailable": self.mask is not None,
            "maskAssetIds": list(self.mask_asset_ids),
            "blockedReasonCodes": list(self.blocked_reason_codes),
        }


def build_speech_bubble_protect_plan(
    package: Sam3ComicMaskPackage,
    *,
    require_accepted: bool = True,
    include_caption: bool = True,
    include_sound_effect_text: bool = True,
) -> Sam3SpeechBubbleProtectPlan:
    target_categories = _target_categories(
        include_caption=include_caption,
        include_sound_effect_text=include_sound_effect_text,
    )
    candidates = tuple(
        candidate
        for candidate in _iter_package_candidates(package)
        if _is_text_protection_candidate(candidate, target_categories)
    )
    if not candidates:
        return _blocked_plan(_NO_CANDIDATE_REASON)

    selected = (
        tuple(candidate for candidate in candidates if candidate.accepted)
        if require_accepted
        else candidates
    )
    if not selected:
        return _blocked_plan(_NO_ACCEPTED_REASON)

    return Sam3SpeechBubbleProtectPlan(
        ready=True,
        mask_asset_ids=_unique_values(candidate.mask_asset_id for candidate in selected),
        candidate_ids=_unique_values(candidate.candidate_id for candidate in selected),
        categories=_unique_categories(candidate.category for candidate in selected),
        blocked_reason_codes=(),
        requires_user_confirmation=True,
    )


def combine_speech_bubble_protect_masks(
    plan: Sam3SpeechBubbleProtectPlan,
    masks_by_asset_id: Mapping[str, object],
) -> Sam3SpeechBubbleProtectMaskResult:
    if not plan.ready:
        return _blocked_mask_result(
            plan,
            plan.blocked_reason_codes or (_PLAN_NOT_READY_REASON,),
        )
    if not plan.mask_asset_ids or not isinstance(masks_by_asset_id, Mapping):
        return _blocked_mask_result(plan, (_MASK_ASSET_MISSING_REASON,))

    masks: list[object] = []
    for asset_id in plan.mask_asset_ids:
        mask = masks_by_asset_id.get(asset_id)
        if mask is None:
            return _blocked_mask_result(plan, (_MASK_ASSET_MISSING_REASON,))
        masks.append(mask)

    try:
        combine_inputs = tuple(masks) if len(masks) > 1 else (masks[0], masks[0])
        combined = combine_mask_arrays(combine_inputs, mode="union")
    except Sam3MaskCombineError as exc:
        return _blocked_mask_result(plan, (exc.code,))

    return Sam3SpeechBubbleProtectMaskResult(
        ready=True,
        mask=combined,
        mask_asset_ids=plan.mask_asset_ids,
        blocked_reason_codes=(),
    )


def combine_speech_bubble_protect_mask_assets(
    asset_store: Sam3AssetStore,
    plan: Sam3SpeechBubbleProtectPlan,
    *,
    canvas_width: int,
    canvas_height: int,
) -> Sam3SpeechBubbleProtectMaskResult:
    """Load plan mask assets, place them on the canvas, and union them."""

    if not plan.ready:
        return _blocked_mask_result(
            plan,
            plan.blocked_reason_codes or (_PLAN_NOT_READY_REASON,),
        )

    try:
        target_width, target_height = _map_refine_error(
            _validate_canvas_size,
            canvas_width,
            canvas_height,
        )
        np = _map_refine_error(_load_numpy)
        masks_by_asset_id: dict[str, object] = {}
        for asset_id in plan.mask_asset_ids:
            asset = _map_refine_error(_load_mask_asset, asset_store, asset_id)
            alpha = _map_refine_error(_load_mask_asset_alpha, asset_store, asset, np)
            masks_by_asset_id[asset_id] = _map_refine_error(
                _asset_alpha_to_canvas,
                alpha,
                asset,
                canvas_width=target_width,
                canvas_height=target_height,
                np=np,
            )
    except Sam3SpeechBubbleProtectMaskAssetError as exc:
        return _blocked_mask_result(
            plan,
            (_protect_materialize_block_reason(exc.code),),
        )

    return combine_speech_bubble_protect_masks(plan, masks_by_asset_id)


def save_speech_bubble_protect_mask_asset(
    asset_store: Sam3AssetStore,
    result: Sam3SpeechBubbleProtectMaskResult,
    *,
    output_asset_id: str,
    canvas_width: int,
    canvas_height: int,
    source_candidate_id: str | None = None,
    metadata: Mapping[str, object] | None = None,
) -> MaskAsset:
    if not result.ready or result.mask is None:
        raise Sam3SpeechBubbleProtectMaskAssetError(
            "speech_bubble_protect_mask_not_ready",
            "SAM3 speech bubble protect mask result is not ready.",
        )
    asset_id = str(output_asset_id or "").strip()
    if not asset_id:
        raise Sam3SpeechBubbleProtectMaskAssetError(
            "speech_bubble_protect_mask_asset_id_missing",
            "SAM3 speech bubble protect mask save requires an output asset id.",
        )
    if asset_id in result.mask_asset_ids:
        raise Sam3SpeechBubbleProtectMaskAssetError(
            "speech_bubble_protect_asset_id_conflict",
            "SAM3 speech bubble protect mask output id conflicts with a source mask.",
        )

    target_width, target_height = _map_refine_error(
        _validate_canvas_size,
        canvas_width,
        canvas_height,
    )
    np = _map_refine_error(_load_numpy)
    alpha = _protect_result_alpha(
        result.mask,
        canvas_width=target_width,
        canvas_height=target_height,
        np=np,
    )
    bbox = _nonzero_bbox(alpha, np)
    if bbox is None:
        raise Sam3SpeechBubbleProtectMaskAssetError(
            "speech_bubble_protect_mask_empty",
            "SAM3 speech bubble protect mask is empty.",
        )

    x, y, width, height = bbox
    cropped_alpha = alpha[y : y + height, x : x + width]
    png_bytes = _map_refine_error(_encode_alpha_png, cropped_alpha, np)
    output_metadata = _protect_mask_metadata(result, metadata)
    try:
        return asset_store.save_mask_asset(
            png_bytes,
            asset_id=asset_id,
            kind="alpha_mask",
            width=width,
            height=height,
            bbox=bbox,
            source_candidate_id=source_candidate_id,
            metadata=output_metadata,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise Sam3SpeechBubbleProtectMaskAssetError(
            "speech_bubble_protect_mask_asset_save_failed",
            "SAM3 speech bubble protect mask asset could not be saved.",
        ) from exc


def _target_categories(
    *,
    include_caption: bool,
    include_sound_effect_text: bool,
) -> tuple[MangaMaskCategory, ...]:
    values: list[MangaMaskCategory] = ["speech_bubble"]
    if include_caption:
        values.append("caption_box")
    if include_sound_effect_text:
        values.append("sound_effect_text")
    return tuple(values)


def _iter_package_candidates(
    package: Sam3ComicMaskPackage,
) -> tuple[Sam3ComicMaskCandidateRef, ...]:
    candidates: list[Sam3ComicMaskCandidateRef] = []
    for panel in package.panel_masks:
        candidates.extend(panel.candidates)
    candidates.extend(package.global_masks)
    return tuple(candidates)


def _is_text_protection_candidate(
    candidate: Sam3ComicMaskCandidateRef,
    target_categories: tuple[MangaMaskCategory, ...],
) -> bool:
    if candidate.category not in target_categories:
        return False
    definition = manga_mask_category_definition(candidate.category)
    return bool(definition and definition.is_text_protection_target)


def _blocked_plan(reason: str) -> Sam3SpeechBubbleProtectPlan:
    return Sam3SpeechBubbleProtectPlan(
        ready=False,
        blocked_reason_codes=(reason,),
        requires_user_confirmation=True,
    )


def _blocked_mask_result(
    plan: Sam3SpeechBubbleProtectPlan,
    reasons: Iterable[str],
) -> Sam3SpeechBubbleProtectMaskResult:
    return Sam3SpeechBubbleProtectMaskResult(
        ready=False,
        mask=None,
        mask_asset_ids=plan.mask_asset_ids,
        blocked_reason_codes=tuple(reasons),
    )


def _protect_result_alpha(
    mask: object,
    *,
    canvas_width: int,
    canvas_height: int,
    np: object,
) -> object:
    try:
        array = np.asarray(mask)
    except Exception as exc:
        raise Sam3SpeechBubbleProtectMaskAssetError(
            "speech_bubble_protect_mask_invalid",
            "SAM3 speech bubble protect mask must be a 2D mask-like value.",
        ) from exc
    if array.ndim != 2 or array.size <= 0:
        raise Sam3SpeechBubbleProtectMaskAssetError(
            "speech_bubble_protect_mask_invalid",
            "SAM3 speech bubble protect mask must be a non-empty 2D mask.",
        )
    if array.shape != (canvas_height, canvas_width):
        raise Sam3SpeechBubbleProtectMaskAssetError(
            "speech_bubble_protect_mask_size_mismatch",
            "SAM3 speech bubble protect mask must match the canvas size.",
        )
    if array.dtype == np.bool_:
        return np.ascontiguousarray(array.astype(np.uint8) * 255)
    try:
        numeric = array.astype(np.float32, copy=False)
    except (TypeError, ValueError) as exc:
        raise Sam3SpeechBubbleProtectMaskAssetError(
            "speech_bubble_protect_mask_invalid",
            "SAM3 speech bubble protect mask values must be numeric.",
        ) from exc
    numeric = np.nan_to_num(numeric, nan=0.0, posinf=255.0, neginf=0.0)
    return np.ascontiguousarray(np.clip(numeric, 0.0, 255.0).astype(np.uint8))


def _protect_mask_metadata(
    result: Sam3SpeechBubbleProtectMaskResult,
    metadata: Mapping[str, object] | None,
) -> dict[str, object]:
    payload = dict(metadata or {}) if isinstance(metadata, Mapping) else {}
    payload.update(
        {
            "source": "sam3_speech_bubble_protect",
            "operation": "speech_bubble_protect_mask",
            "sourceMaskAssetIds": list(result.mask_asset_ids),
        }
    )
    return payload


def _map_refine_error(function: object, *args: object, **kwargs: object):
    try:
        return function(*args, **kwargs)
    except Sam3MaskRefineAdapterError as exc:
        raise Sam3SpeechBubbleProtectMaskAssetError(exc.code, str(exc)) from exc


def _protect_materialize_block_reason(code: str) -> str:
    if code == "mask_asset_missing":
        return _MASK_ASSET_MISSING_REASON
    return code


def _unique_values(values: Iterable[object]) -> tuple[str, ...]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        unique.append(text)
    return tuple(unique)


def _unique_categories(values: Iterable[MangaMaskCategory]) -> tuple[MangaMaskCategory, ...]:
    seen: set[MangaMaskCategory] = set()
    unique: list[MangaMaskCategory] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return tuple(unique)


def _string_tuple(values: object) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)):
        return ()
    return tuple(str(value) for value in values)


__all__ = [
    "SAM3_SPEECH_BUBBLE_PROTECT_MASK_RESULT_SCHEMA_VERSION",
    "SAM3_SPEECH_BUBBLE_PROTECT_PLAN_SCHEMA_VERSION",
    "Sam3SpeechBubbleProtectMaskAssetError",
    "Sam3SpeechBubbleProtectMaskResult",
    "Sam3SpeechBubbleProtectPlan",
    "build_speech_bubble_protect_plan",
    "combine_speech_bubble_protect_mask_assets",
    "combine_speech_bubble_protect_masks",
    "save_speech_bubble_protect_mask_asset",
]
