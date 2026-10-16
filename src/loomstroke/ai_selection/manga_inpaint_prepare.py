"""Manga inpaint preparation metadata helpers for SAM3 mask candidates."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from .generation_mask_qa import Sam3GenerationMaskQA
from .generation_preview_request import (
    Sam3AIEffectPreviewRequestError,
    build_ai_effect_preview_generation_request,
)
from .manga_mask_categories import MangaMaskCategory
from .manga_mask_package import Sam3ComicMaskCandidateRef

SAM3_MANGA_INPAINT_PREPARATION_SCHEMA_VERSION = "sam3.manga_inpaint_preparation.v1"

_CANDIDATE_NOT_ACCEPTED_REASON = "manga_inpaint_candidate_not_accepted"
_MASK_ASSET_MISSING_REASON = "manga_inpaint_mask_asset_missing"
_PROTECTED_MASK_MISSING_REASON = "manga_inpaint_protected_mask_missing"


class Sam3MangaInpaintPreviewRequestError(ValueError):
    """Raised when a manga inpaint preview request cannot be built."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Sam3MangaInpaintPreparation:
    ready: bool
    candidate_id: str | None = None
    category: MangaMaskCategory | None = None
    inpaint_mask_asset_id: str | None = None
    protected_mask_asset_id: str | None = None
    blocked_reason_codes: tuple[str, ...] = field(default_factory=tuple)
    requires_user_confirmation: bool = True
    schema_version: str = SAM3_MANGA_INPAINT_PREPARATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_id", _optional_text(self.candidate_id))
        object.__setattr__(
            self,
            "inpaint_mask_asset_id",
            _optional_text(self.inpaint_mask_asset_id),
        )
        object.__setattr__(
            self,
            "protected_mask_asset_id",
            _optional_text(self.protected_mask_asset_id),
        )
        object.__setattr__(
            self,
            "blocked_reason_codes",
            _string_tuple(self.blocked_reason_codes),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "ready": self.ready,
            "candidateId": self.candidate_id,
            "category": self.category,
            "inpaintMaskAssetId": self.inpaint_mask_asset_id,
            "protectedMaskAssetId": self.protected_mask_asset_id,
            "blockedReasonCodes": list(self.blocked_reason_codes),
            "requiresUserConfirmation": self.requires_user_confirmation,
        }

    def to_metadata(self) -> dict[str, object]:
        return self.to_dict()


def build_manga_inpaint_preparation(
    candidate: Sam3ComicMaskCandidateRef,
    *,
    protected_mask_asset_id: str | None = None,
    require_accepted: bool = True,
    require_protected_mask: bool = True,
) -> Sam3MangaInpaintPreparation:
    candidate_id = _optional_text(candidate.candidate_id)
    inpaint_mask_asset_id = _optional_text(candidate.mask_asset_id)
    protected_id = _optional_text(protected_mask_asset_id)
    blocked_reasons: list[str] = []

    if require_accepted and not candidate.accepted:
        blocked_reasons.append(_CANDIDATE_NOT_ACCEPTED_REASON)
    if inpaint_mask_asset_id is None:
        blocked_reasons.append(_MASK_ASSET_MISSING_REASON)
    if require_protected_mask and protected_id is None:
        blocked_reasons.append(_PROTECTED_MASK_MISSING_REASON)

    return Sam3MangaInpaintPreparation(
        ready=not blocked_reasons,
        candidate_id=candidate_id,
        category=candidate.category,
        inpaint_mask_asset_id=inpaint_mask_asset_id,
        protected_mask_asset_id=protected_id,
        blocked_reason_codes=tuple(blocked_reasons),
        requires_user_confirmation=True,
    )


def build_manga_inpaint_preview_request(  # noqa: PLR0913
    layer_id: str,
    preparation: Sam3MangaInpaintPreparation,
    *,
    candidate_set_id: str | None = None,
    qa: Sam3GenerationMaskQA | Mapping[str, object] | None = None,
    force_regenerate: bool = False,
    param_values: Mapping[str, object] | None = None,
    slot_refs: Mapping[str, object] | None = None,
    param_ui_state: Mapping[str, object] | None = None,
    replace_param_values: bool = False,
    replace_slot_refs: bool = False,
    replace_param_ui_state: bool = False,
    trigger: str | None = None,
    target_server_id: str | None = None,
    output_mode_override: str | None = None,
    metadata: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build a preview-only AI Effect request from a ready manga inpaint payload."""

    if not preparation.ready:
        raise Sam3MangaInpaintPreviewRequestError(
            "manga_inpaint_preparation_not_ready",
            "SAM3 manga inpaint preview requires a ready preparation payload.",
        )
    candidate_id = _optional_text(preparation.candidate_id)
    if candidate_id is None:
        raise Sam3MangaInpaintPreviewRequestError(
            "manga_inpaint_candidate_id_missing",
            "SAM3 manga inpaint preview requires a candidate id.",
        )
    inpaint_mask_asset_id = _optional_text(preparation.inpaint_mask_asset_id)
    if inpaint_mask_asset_id is None:
        raise Sam3MangaInpaintPreviewRequestError(
            "manga_inpaint_mask_asset_missing",
            "SAM3 manga inpaint preview requires an inpaint mask asset id.",
        )
    preview_metadata = _preview_metadata(preparation, metadata)
    try:
        return build_ai_effect_preview_generation_request(
            layer_id,
            candidate_set_id=candidate_set_id,
            candidate_id=candidate_id,
            mask_asset_id=inpaint_mask_asset_id,
            qa=qa,
            force_regenerate=force_regenerate,
            param_values=param_values,
            slot_refs=slot_refs,
            param_ui_state=param_ui_state,
            replace_param_values=replace_param_values,
            replace_slot_refs=replace_slot_refs,
            replace_param_ui_state=replace_param_ui_state,
            trigger=trigger,
            target_server_id=target_server_id,
            output_mode_override=output_mode_override,
            metadata=preview_metadata,
        )
    except Sam3AIEffectPreviewRequestError as exc:
        raise Sam3MangaInpaintPreviewRequestError(exc.code, str(exc)) from exc


def _preview_metadata(
    preparation: Sam3MangaInpaintPreparation,
    metadata: Mapping[str, object] | None,
) -> dict[str, object]:
    if metadata is not None and not isinstance(metadata, Mapping):
        raise Sam3MangaInpaintPreviewRequestError(
            "ai_effect_preview_metadata_invalid",
            "SAM3 manga inpaint preview metadata must be a mapping.",
        )
    preview_metadata = dict(metadata or {})
    preview_metadata["mangaInpaint"] = preparation.to_metadata()
    return preview_metadata


def _optional_text(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_tuple(values: object) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)):
        return ()
    return tuple(text for value in values if (text := str(value).strip()))


__all__ = [
    "SAM3_MANGA_INPAINT_PREPARATION_SCHEMA_VERSION",
    "Sam3MangaInpaintPreparation",
    "Sam3MangaInpaintPreviewRequestError",
    "build_manga_inpaint_preparation",
    "build_manga_inpaint_preview_request",
]
