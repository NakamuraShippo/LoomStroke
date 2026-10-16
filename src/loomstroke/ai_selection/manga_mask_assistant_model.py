"""UI-independent row models for the SAM3 Manga Mask Assistant."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .manga_mask_package import Sam3ComicMaskCandidateRef, Sam3ComicMaskPackage

SAM3_MANGA_MASK_ASSISTANT_ROW_SCHEMA_VERSION = "sam3.manga_mask_assistant_row.v1"

MangaMaskAssistantAction = Literal[
    "apply_as_selection",
    "protect_text",
    "send_to_inpaint",
    "export_package",
    "create_changeset",
]
MangaMaskAssistantScope = Literal["panel", "global"]

_TEXT_PROTECTION_CATEGORIES = frozenset(
    {
        "speech_bubble",
        "caption_box",
        "sound_effect_text",
    }
)


class Sam3MangaMaskAssistantModelError(ValueError):
    """Raised when Manga Mask Assistant rows cannot be built."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Sam3MangaMaskAssistantRow:
    row_id: str
    scope: MangaMaskAssistantScope
    package_id: str
    source_page_id: str
    candidate_id: str
    category: str
    mask_asset_id: str
    bbox: tuple[int, int, int, int]
    area_px: int
    actions: tuple[MangaMaskAssistantAction, ...]
    panel_id: str | None = None
    panel_local_bbox: tuple[int, int, int, int] | None = None
    preview_asset_id: str | None = None
    score: float | None = None
    accepted: bool = False
    warnings: tuple[str, ...] = ()
    schema_version: str = SAM3_MANGA_MASK_ASSISTANT_ROW_SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "rowId": self.row_id,
            "scope": self.scope,
            "packageId": self.package_id,
            "sourcePageId": self.source_page_id,
            "panelId": self.panel_id,
            "candidateId": self.candidate_id,
            "category": self.category,
            "maskAssetId": self.mask_asset_id,
            "previewAssetId": self.preview_asset_id,
            "bbox": list(self.bbox),
            "panelLocalBbox": (
                list(self.panel_local_bbox) if self.panel_local_bbox is not None else None
            ),
            "areaPx": self.area_px,
            "score": self.score,
            "accepted": self.accepted,
            "warnings": list(self.warnings),
            "actions": list(self.actions),
        }


def build_manga_mask_assistant_rows(
    package: Sam3ComicMaskPackage,
) -> tuple[Sam3MangaMaskAssistantRow, ...]:
    """Build stable Manga Mask Assistant candidate rows from a comic mask package."""

    if not isinstance(package, Sam3ComicMaskPackage):
        raise Sam3MangaMaskAssistantModelError(
            "manga_mask_assistant_package_invalid",
            "SAM3 Manga Mask Assistant rows require a comic mask package.",
        )

    rows: list[Sam3MangaMaskAssistantRow] = []
    for panel in package.panel_masks:
        for candidate in panel.candidates:
            rows.append(
                _row_from_candidate(
                    package,
                    candidate,
                    scope="panel",
                    panel_id=panel.panel_id,
                    row_id=f"{panel.panel_id}:{candidate.candidate_id}",
                )
            )
    for candidate in package.global_masks:
        rows.append(
            _row_from_candidate(
                package,
                candidate,
                scope="global",
                panel_id=candidate.panel_id,
                row_id=f"global:{candidate.candidate_id}",
            )
        )
    return tuple(rows)


def _row_from_candidate(
    package: Sam3ComicMaskPackage,
    candidate: Sam3ComicMaskCandidateRef,
    *,
    scope: MangaMaskAssistantScope,
    panel_id: str | None,
    row_id: str,
) -> Sam3MangaMaskAssistantRow:
    return Sam3MangaMaskAssistantRow(
        row_id=row_id,
        scope=scope,
        package_id=package.package_id,
        source_page_id=package.source_page_id,
        panel_id=panel_id,
        candidate_id=candidate.candidate_id,
        category=candidate.category,
        mask_asset_id=candidate.mask_asset_id,
        preview_asset_id=candidate.preview_asset_id,
        bbox=candidate.bbox,
        panel_local_bbox=candidate.panel_local_bbox,
        area_px=candidate.area_px,
        score=candidate.score,
        accepted=candidate.accepted,
        warnings=tuple(candidate.warnings),
        actions=_actions_for_category(candidate.category),
    )


def _actions_for_category(category: str) -> tuple[MangaMaskAssistantAction, ...]:
    actions: list[MangaMaskAssistantAction] = ["apply_as_selection"]
    if category in _TEXT_PROTECTION_CATEGORIES:
        actions.append("protect_text")
    actions.extend(("send_to_inpaint", "export_package", "create_changeset"))
    return tuple(actions)


__all__ = [
    "SAM3_MANGA_MASK_ASSISTANT_ROW_SCHEMA_VERSION",
    "MangaMaskAssistantAction",
    "Sam3MangaMaskAssistantModelError",
    "Sam3MangaMaskAssistantRow",
    "build_manga_mask_assistant_rows",
]
