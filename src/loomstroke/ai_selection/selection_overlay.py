"""Pure candidate overlay projection for SAM3 AI Select sessions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, cast

from .data_model import OverlayOptions, Sam3Warning, SourceSnapshotRef
from .selection_session import Sam3SelectionSession

Sam3OverlayColorMode = Literal["single", "instance", "heat"]
Sam3OverlayItemState = Literal["selected", "available", "rejected"]

SAM3_SELECTION_OVERLAY_SCHEMA_VERSION = "sam3.selection_overlay.v1"

_COLOR_MODES = {"single", "instance", "heat"}
_SINGLE_COLOR = (54, 190, 220)
_INSTANCE_PALETTE: tuple[tuple[int, int, int], ...] = (
    (54, 190, 220),
    (234, 112, 112),
    (129, 201, 107),
    (246, 179, 77),
    (169, 132, 232),
    (239, 123, 190),
    (96, 153, 245),
    (215, 209, 87),
)
_REJECTED_COLOR = (132, 132, 132)


@dataclass(frozen=True, slots=True)
class Sam3CandidateOverlayStyle:
    """Display style for one SAM3 candidate overlay item."""

    color_mode: Sam3OverlayColorMode
    fill_rgba: tuple[int, int, int, int]
    outline_rgba: tuple[int, int, int, int]
    opacity: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "colorMode": self.color_mode,
            "fillRgba": list(self.fill_rgba),
            "outlineRgba": list(self.outline_rgba),
            "opacity": self.opacity,
        }


@dataclass(frozen=True, slots=True)
class Sam3CandidateOverlayItem:
    """Candidate list and overlay projection for a single SAM3 mask candidate."""

    candidate_id: str
    label: str
    score: float | None
    rank: int
    bbox: tuple[int, int, int, int]
    clipped_bbox: tuple[int, int, int, int] | None
    area_px: int
    mask_asset_id: str
    preview_asset_id: str | None
    instance_index: int
    group_id: str | None
    state: Sam3OverlayItemState
    visible: bool
    style: Sam3CandidateOverlayStyle

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidateId": self.candidate_id,
            "label": self.label,
            "score": self.score,
            "rank": self.rank,
            "bbox": list(self.bbox),
            "clippedBbox": list(self.clipped_bbox) if self.clipped_bbox is not None else None,
            "areaPx": self.area_px,
            "maskAssetId": self.mask_asset_id,
            "previewAssetId": self.preview_asset_id,
            "instanceIndex": self.instance_index,
            "groupId": self.group_id,
            "state": self.state,
            "visible": self.visible,
            "style": self.style.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class Sam3SelectionOverlayModel:
    """JSON-safe projection for SAM3 candidate list and overlay preview UI."""

    candidate_set_id: str
    source_ref: SourceSnapshotRef
    selected_candidate_id: str | None
    available_candidate_ids: tuple[str, ...]
    rejected_candidate_ids: tuple[str, ...]
    items: tuple[Sam3CandidateOverlayItem, ...]
    warnings: tuple[Sam3Warning, ...]
    schema_version: str = SAM3_SELECTION_OVERLAY_SCHEMA_VERSION

    @property
    def active_item(self) -> Sam3CandidateOverlayItem | None:
        selected_id = self.selected_candidate_id
        if selected_id is None:
            return None
        for item in self.items:
            if item.candidate_id == selected_id:
                return item
        return None

    @property
    def visible_items(self) -> tuple[Sam3CandidateOverlayItem, ...]:
        return tuple(item for item in self.items if item.visible)

    def to_dict(self) -> dict[str, Any]:
        active = self.active_item
        return {
            "schemaVersion": self.schema_version,
            "candidateSetId": self.candidate_set_id,
            "sourceRef": self.source_ref.to_dict(),
            "selectedCandidateId": self.selected_candidate_id,
            "activeCandidateId": active.candidate_id if active is not None else None,
            "availableCandidateIds": list(self.available_candidate_ids),
            "rejectedCandidateIds": list(self.rejected_candidate_ids),
            "visibleCandidateIds": [item.candidate_id for item in self.visible_items],
            "items": [item.to_dict() for item in self.items],
            "warnings": [warning.to_dict() for warning in self.warnings],
        }


def build_selection_overlay_model(
    session: Sam3SelectionSession,
    options: OverlayOptions | None = None,
) -> Sam3SelectionOverlayModel:
    overlay_options = options or OverlayOptions()
    color_mode = _normalize_color_mode(overlay_options.color_mode)
    opacity = _clamp_float(float(overlay_options.opacity), 0.0, 1.0)
    rejected_ids = set(session.rejected_candidate_ids)
    warnings: list[Sam3Warning] = list(session.candidate_set.warnings)
    items: list[Sam3CandidateOverlayItem] = []
    source_ref = session.candidate_set.source_ref

    for rank, candidate in enumerate(session.candidate_set.candidates, start=1):
        candidate_id = candidate.candidate_id
        state = _item_state(
            candidate_id,
            selected_candidate_id=session.selected_candidate_id,
            rejected_candidate_ids=rejected_ids,
        )
        clipped_bbox, clipped = _clip_bbox(candidate.bbox, source_ref.width, source_ref.height)
        if clipped_bbox is None:
            warnings.append(
                Sam3Warning(
                    code="candidate_overlay_bounds_invalid",
                    message="SAM3 candidate overlay bounds do not intersect the source canvas.",
                    severity="warning",
                    action=f"Inspect or regenerate candidate {candidate_id}.",
                )
            )
        elif clipped:
            warnings.append(
                Sam3Warning(
                    code="candidate_overlay_bounds_clipped",
                    message="SAM3 candidate overlay bounds were clipped to the source canvas.",
                    severity="warning",
                    action=f"Inspect candidate {candidate_id} before applying.",
                )
            )

        visible = clipped_bbox is not None and (
            state != "rejected" or bool(overlay_options.include_rejected)
        )
        style = _style_for_candidate(
            rank=rank,
            instance_index=candidate.instance_index,
            score=candidate.score,
            state=state,
            color_mode=color_mode,
            opacity=opacity,
        )
        items.append(
            Sam3CandidateOverlayItem(
                candidate_id=candidate_id,
                label=candidate.label,
                score=candidate.score,
                rank=rank,
                bbox=tuple(int(value) for value in candidate.bbox),
                clipped_bbox=clipped_bbox,
                area_px=int(candidate.area_px),
                mask_asset_id=candidate.mask_asset_id,
                preview_asset_id=candidate.preview_asset_id,
                instance_index=int(candidate.instance_index),
                group_id=candidate.group_id,
                state=state,
                visible=visible,
                style=style,
            )
        )

    return Sam3SelectionOverlayModel(
        candidate_set_id=session.candidate_set.set_id,
        source_ref=source_ref,
        selected_candidate_id=session.selected_candidate_id,
        available_candidate_ids=session.available_candidate_ids,
        rejected_candidate_ids=session.rejected_candidate_ids,
        items=tuple(items),
        warnings=tuple(warnings),
    )


def _item_state(
    candidate_id: str,
    *,
    selected_candidate_id: str | None,
    rejected_candidate_ids: set[str],
) -> Sam3OverlayItemState:
    if candidate_id in rejected_candidate_ids:
        return "rejected"
    if candidate_id == selected_candidate_id:
        return "selected"
    return "available"


def _style_for_candidate(
    *,
    rank: int,
    instance_index: int,
    score: float | None,
    state: Sam3OverlayItemState,
    color_mode: Sam3OverlayColorMode,
    opacity: float,
) -> Sam3CandidateOverlayStyle:
    if state == "rejected":
        base_color = _REJECTED_COLOR
    elif color_mode == "single":
        base_color = _SINGLE_COLOR
    elif color_mode == "heat":
        base_color = _heat_color(score)
    else:
        palette_index = instance_index if instance_index > 0 else rank - 1
        base_color = _INSTANCE_PALETTE[palette_index % len(_INSTANCE_PALETTE)]

    fill_alpha = _alpha(opacity)
    outline_alpha = _alpha(min(1.0, opacity + 0.22))
    if state == "selected":
        outline_alpha = 255
    elif state == "rejected":
        fill_alpha = _alpha(opacity * 0.35)
        outline_alpha = _alpha(opacity * 0.5)
    else:
        # Dim unselected candidates so the refine/apply target is
        # unambiguous on the canvas (2026-07-13 selection clarity).
        fill_alpha = _alpha(opacity * 0.45)

    return Sam3CandidateOverlayStyle(
        color_mode=color_mode,
        fill_rgba=(*base_color, fill_alpha),
        outline_rgba=(*base_color, outline_alpha),
        opacity=opacity,
    )


def _clip_bbox(
    bbox: tuple[int, int, int, int],
    source_width: int,
    source_height: int,
) -> tuple[tuple[int, int, int, int] | None, bool]:
    if source_width <= 0 or source_height <= 0:
        return None, False
    x, y, width, height = (int(value) for value in bbox)
    if width <= 0 or height <= 0:
        return None, False

    x0 = max(0, x)
    y0 = max(0, y)
    x1 = min(int(source_width), x + width)
    y1 = min(int(source_height), y + height)
    if x1 <= x0 or y1 <= y0:
        return None, False

    clipped_bbox = (x0, y0, x1 - x0, y1 - y0)
    return clipped_bbox, clipped_bbox != (x, y, width, height)


def _normalize_color_mode(value: str) -> Sam3OverlayColorMode:
    raw = str(value).strip()
    if raw in _COLOR_MODES:
        return cast("Sam3OverlayColorMode", raw)
    return "instance"


def _heat_color(score: float | None) -> tuple[int, int, int]:
    normalized = _clamp_float(0.5 if score is None else float(score), 0.0, 1.0)
    if normalized <= 0.5:
        ratio = normalized / 0.5
        return (
            _lerp(55, 247, ratio),
            _lerp(130, 206, ratio),
            _lerp(230, 83, ratio),
        )
    ratio = (normalized - 0.5) / 0.5
    return (
        _lerp(247, 232, ratio),
        _lerp(206, 83, ratio),
        _lerp(83, 76, ratio),
    )


def _alpha(opacity: float) -> int:
    return round(_clamp_float(opacity, 0.0, 1.0) * 255)


def _clamp_float(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _lerp(start: int, end: int, ratio: float) -> int:
    return round(start + (end - start) * ratio)


__all__ = [
    "SAM3_SELECTION_OVERLAY_SCHEMA_VERSION",
    "Sam3CandidateOverlayItem",
    "Sam3CandidateOverlayStyle",
    "Sam3OverlayColorMode",
    "Sam3OverlayItemState",
    "Sam3SelectionOverlayModel",
    "build_selection_overlay_model",
]
