"""Package-safe manga mask manifest models for SAM3 workflows."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from .data_model import AssetRef, MaskCandidate, MaskCandidateSet
from .manga_mask_categories import MangaMaskCategory, normalize_manga_mask_category

SAM3_COMIC_MASK_CANDIDATE_REF_SCHEMA_VERSION = "sam3.comic_mask_candidate_ref.v1"
SAM3_COMIC_PANEL_MASK_SET_SCHEMA_VERSION = "sam3.comic_panel_mask_set.v1"
SAM3_COMIC_MASK_PACKAGE_SCHEMA_VERSION = "sam3.comic_mask_package.v1"

_WARNING_CODE_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyz" "ABCDEFGHIJKLMNOPQRSTUVWXYZ" "0123456789" "_.:-"
)


class Sam3ComicMaskPackageError(ValueError):
    """Raised when a comic mask package cannot be represented safely."""


@dataclass(frozen=True, slots=True)
class Sam3ComicMaskCandidateRef:
    candidate_id: str
    category: MangaMaskCategory
    mask_asset_id: str
    bbox: tuple[int, int, int, int]
    area_px: int
    score: float | None = None
    preview_asset_id: str | None = None
    panel_id: str | None = None
    panel_local_bbox: tuple[int, int, int, int] | None = None
    accepted: bool = False
    warnings: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = SAM3_COMIC_MASK_CANDIDATE_REF_SCHEMA_VERSION

    def __post_init__(self) -> None:
        category = normalize_manga_mask_category(self.category)
        if category is None:
            raise Sam3ComicMaskPackageError("Unknown comic mask category.")
        object.__setattr__(self, "category", category)
        object.__setattr__(self, "candidate_id", _required_id(self.candidate_id, "candidate_id"))
        object.__setattr__(self, "mask_asset_id", _required_id(self.mask_asset_id, "mask_asset_id"))
        object.__setattr__(self, "bbox", _int_tuple4(self.bbox, "bbox"))
        object.__setattr__(self, "area_px", max(0, int(self.area_px or 0)))
        object.__setattr__(self, "score", _optional_float(self.score))
        object.__setattr__(self, "preview_asset_id", _optional_id(self.preview_asset_id))
        object.__setattr__(self, "panel_id", _optional_id(self.panel_id))
        object.__setattr__(
            self,
            "panel_local_bbox",
            _optional_int_tuple4(self.panel_local_bbox, "panel_local_bbox"),
        )
        object.__setattr__(self, "warnings", _warning_codes(self.warnings))

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "candidateId": self.candidate_id,
            "category": self.category,
            "maskAssetId": self.mask_asset_id,
            "bbox": list(self.bbox),
            "areaPx": self.area_px,
            "score": self.score,
            "previewAssetId": self.preview_asset_id,
            "panelId": self.panel_id,
            "panelLocalBbox": (
                list(self.panel_local_bbox) if self.panel_local_bbox is not None else None
            ),
            "accepted": self.accepted,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class Sam3PanelMaskSet:
    panel_id: str
    panel_bbox: tuple[int, int, int, int]
    candidates: tuple[Sam3ComicMaskCandidateRef, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = SAM3_COMIC_PANEL_MASK_SET_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "panel_id", _required_id(self.panel_id, "panel_id"))
        object.__setattr__(self, "panel_bbox", _int_tuple4(self.panel_bbox, "panel_bbox"))
        object.__setattr__(self, "candidates", tuple(self.candidates))
        object.__setattr__(self, "warnings", _warning_codes(self.warnings))

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "panelId": self.panel_id,
            "panelBbox": list(self.panel_bbox),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class Sam3ComicMaskPackage:
    package_id: str
    source_document_id: str
    source_page_id: str
    panel_masks: tuple[Sam3PanelMaskSet, ...] = field(default_factory=tuple)
    global_masks: tuple[Sam3ComicMaskCandidateRef, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    export_assets: tuple[AssetRef, ...] = field(default_factory=tuple)
    metadata_path: str = "comic_mask_package.json"
    schema_version: str = SAM3_COMIC_MASK_PACKAGE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "package_id", _required_id(self.package_id, "package_id"))
        object.__setattr__(
            self,
            "source_document_id",
            _required_id(self.source_document_id, "source_document_id"),
        )
        object.__setattr__(
            self,
            "source_page_id",
            _required_id(self.source_page_id, "source_page_id"),
        )
        object.__setattr__(self, "panel_masks", tuple(self.panel_masks))
        object.__setattr__(self, "global_masks", tuple(self.global_masks))
        object.__setattr__(self, "warnings", _warning_codes(self.warnings))
        object.__setattr__(self, "export_assets", tuple(self.export_assets))
        object.__setattr__(self, "metadata_path", _safe_relative_path(self.metadata_path))

    def to_manifest_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "packageId": self.package_id,
            "sourceDocumentId": self.source_document_id,
            "sourcePageId": self.source_page_id,
            "panelMasks": [panel.to_dict() for panel in self.panel_masks],
            "globalMasks": [candidate.to_dict() for candidate in self.global_masks],
            "warnings": list(self.warnings),
            "exportAssets": [_asset_ref_manifest_dict(asset) for asset in self.export_assets],
            "metadataPath": self.metadata_path,
        }

    def to_dict(self) -> dict[str, object]:
        return self.to_manifest_dict()


def comic_mask_candidate_ref_from_candidate(
    candidate: MaskCandidate,
    category: object,
    *,
    panel_id: str | None = None,
    panel_bbox: tuple[int, int, int, int] | None = None,
    accepted: bool = False,
) -> Sam3ComicMaskCandidateRef:
    bbox = _int_tuple4(candidate.bbox, "candidate.bbox")
    normalized_panel_bbox = _optional_int_tuple4(panel_bbox, "panel_bbox")
    return Sam3ComicMaskCandidateRef(
        candidate_id=candidate.candidate_id,
        category=_required_category(category),
        mask_asset_id=candidate.mask_asset_id,
        bbox=bbox,
        area_px=candidate.area_px,
        score=candidate.score,
        preview_asset_id=candidate.preview_asset_id,
        panel_id=panel_id,
        panel_local_bbox=(
            _panel_local_bbox(bbox, normalized_panel_bbox)
            if normalized_panel_bbox is not None
            else None
        ),
        accepted=accepted,
    )


def build_comic_mask_package_from_candidate_set(
    candidate_set: MaskCandidateSet,
    category: object,
    *,
    panel_bboxes: Mapping[str, object] | None = None,
    category_by_candidate_id: Mapping[str, object] | None = None,
    accepted_candidate_ids: Iterable[object] = (),
    package_id: str | None = None,
    source_document_id: str | None = None,
    source_page_id: str | None = None,
    warnings: object = (),
    metadata_path: str = "comic_mask_package.json",
) -> Sam3ComicMaskPackage:
    """Build a prompt-free comic mask package from a SAM3 candidate set."""

    if not isinstance(candidate_set, MaskCandidateSet):
        raise Sam3ComicMaskPackageError("Comic mask package requires a candidate set.")

    panels = _panel_bbox_items(panel_bboxes)
    category_map = _category_map(category_by_candidate_id)
    accepted_ids = _id_set(accepted_candidate_ids)
    panel_candidates: dict[str, list[Sam3ComicMaskCandidateRef]] = {
        panel_id: [] for panel_id, _panel_bbox in panels
    }
    global_candidates: list[Sam3ComicMaskCandidateRef] = []

    for candidate in candidate_set.candidates:
        candidate_category = category_map.get(candidate.candidate_id, category)
        accepted = candidate.candidate_id in accepted_ids
        panel = _best_intersecting_panel(candidate.bbox, panels)
        if panel is None:
            global_candidates.append(
                comic_mask_candidate_ref_from_candidate(
                    candidate,
                    candidate_category,
                    accepted=accepted,
                )
            )
        else:
            panel_id, panel_bbox = panel
            panel_candidates[panel_id].append(
                comic_mask_candidate_ref_from_candidate(
                    candidate,
                    candidate_category,
                    panel_id=panel_id,
                    panel_bbox=panel_bbox,
                    accepted=accepted,
                )
            )

    source_ref = candidate_set.source_ref
    fallback_source_id = _required_id(
        _optional_id(getattr(source_ref, "snapshot_id", None)) or candidate_set.set_id,
        "source_ref.snapshot_id",
    )
    return Sam3ComicMaskPackage(
        package_id=(
            _optional_id(package_id)
            or f"{_required_id(candidate_set.set_id, 'candidate_set.set_id')}_comic_mask_package"
        ),
        source_document_id=(
            _optional_id(source_document_id)
            or _optional_id(getattr(source_ref, "document_id", None))
            or fallback_source_id
        ),
        source_page_id=(
            _optional_id(source_page_id)
            or _optional_id(getattr(source_ref, "page_id", None))
            or fallback_source_id
        ),
        panel_masks=tuple(
            Sam3PanelMaskSet(
                panel_id=panel_id,
                panel_bbox=panel_bbox,
                candidates=tuple(panel_candidates[panel_id]),
            )
            for panel_id, panel_bbox in panels
        ),
        global_masks=tuple(global_candidates),
        warnings=_candidate_set_warning_codes(candidate_set, warnings),
        metadata_path=metadata_path,
    )


def with_comic_mask_candidate_acceptance(
    package: Sam3ComicMaskPackage,
    candidate_id: object,
    accepted: object,
    *,
    scope: object | None = None,
    panel_id: object | None = None,
) -> Sam3ComicMaskPackage:
    """Return a copy of ``package`` with matching candidates' acceptance updated."""

    if not isinstance(package, Sam3ComicMaskPackage):
        raise Sam3ComicMaskPackageError("Comic mask package acceptance requires a package.")

    target_candidate_id = _required_id(candidate_id, "candidate_id")
    target_scope = _scope_filter(scope)
    target_panel_id = _optional_id(panel_id)
    normalized_accepted = bool(accepted)
    matched = False

    panel_masks: list[Sam3PanelMaskSet] = []
    for panel in package.panel_masks:
        candidates: list[Sam3ComicMaskCandidateRef] = []
        for candidate in panel.candidates:
            if _candidate_acceptance_matches(
                candidate,
                target_candidate_id,
                scope="panel",
                current_panel_id=panel.panel_id,
                target_scope=target_scope,
                target_panel_id=target_panel_id,
            ):
                matched = True
                candidates.append(replace(candidate, accepted=normalized_accepted))
            else:
                candidates.append(candidate)
        panel_masks.append(replace(panel, candidates=tuple(candidates)))

    global_masks: list[Sam3ComicMaskCandidateRef] = []
    for candidate in package.global_masks:
        if _candidate_acceptance_matches(
            candidate,
            target_candidate_id,
            scope="global",
            current_panel_id=candidate.panel_id,
            target_scope=target_scope,
            target_panel_id=target_panel_id,
        ):
            matched = True
            global_masks.append(replace(candidate, accepted=normalized_accepted))
        else:
            global_masks.append(candidate)

    if not matched:
        raise Sam3ComicMaskPackageError("Comic mask package candidate not found.")

    return replace(
        package,
        panel_masks=tuple(panel_masks),
        global_masks=tuple(global_masks),
    )


def _required_category(value: object) -> MangaMaskCategory:
    category = normalize_manga_mask_category(value)
    if category is None:
        raise Sam3ComicMaskPackageError("Unknown comic mask category.")
    return category


def _scope_filter(value: object | None) -> str | None:
    text = _optional_id(value)
    if text is None:
        return None
    if text not in {"panel", "global"}:
        raise Sam3ComicMaskPackageError("Comic mask package candidate scope is invalid.")
    return text


def _candidate_acceptance_matches(
    candidate: Sam3ComicMaskCandidateRef,
    target_candidate_id: str,
    *,
    scope: str,
    current_panel_id: str | None,
    target_scope: str | None,
    target_panel_id: str | None,
) -> bool:
    if candidate.candidate_id != target_candidate_id:
        return False
    if target_scope is not None and scope != target_scope:
        return False
    return target_panel_id is None or current_panel_id == target_panel_id


def _required_id(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise Sam3ComicMaskPackageError(f"Comic mask package requires {field_name}.")
    return text


def _optional_id(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int_tuple4(value: object, field_name: str) -> tuple[int, int, int, int]:
    items = _sequence_items(value)
    if len(items) != 4:
        raise Sam3ComicMaskPackageError(f"{field_name} must contain four values.")
    try:
        return (int(items[0]), int(items[1]), int(items[2]), int(items[3]))
    except (TypeError, ValueError) as exc:
        raise Sam3ComicMaskPackageError(f"{field_name} must contain integer values.") from exc


def _optional_int_tuple4(
    value: object,
    field_name: str,
) -> tuple[int, int, int, int] | None:
    if value is None:
        return None
    return _int_tuple4(value, field_name)


def _sequence_items(value: object) -> tuple[Any, ...]:
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(value)
    return ()


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def _panel_local_bbox(
    bbox: tuple[int, int, int, int],
    panel_bbox: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    return (bbox[0] - panel_bbox[0], bbox[1] - panel_bbox[1], bbox[2], bbox[3])


def _panel_bbox_items(
    panel_bboxes: Mapping[str, object] | None,
) -> tuple[tuple[str, tuple[int, int, int, int]], ...]:
    if panel_bboxes is None:
        return ()
    if not isinstance(panel_bboxes, Mapping):
        raise Sam3ComicMaskPackageError("panel_bboxes must be a mapping.")
    return tuple(
        (
            _required_id(panel_id, "panel_id"),
            _int_tuple4(panel_bbox, "panel_bbox"),
        )
        for panel_id, panel_bbox in panel_bboxes.items()
    )


def _category_map(
    category_by_candidate_id: Mapping[str, object] | None,
) -> dict[str, object]:
    if category_by_candidate_id is None:
        return {}
    if not isinstance(category_by_candidate_id, Mapping):
        raise Sam3ComicMaskPackageError("category_by_candidate_id must be a mapping.")
    return {
        candidate_id: category
        for raw_candidate_id, category in category_by_candidate_id.items()
        if (candidate_id := _optional_id(raw_candidate_id)) is not None
    }


def _id_set(values: Iterable[object]) -> frozenset[str]:
    return frozenset(
        candidate_id for value in values if (candidate_id := _optional_id(value)) is not None
    )


def _best_intersecting_panel(
    bbox: object,
    panels: tuple[tuple[str, tuple[int, int, int, int]], ...],
) -> tuple[str, tuple[int, int, int, int]] | None:
    candidate_bbox = _int_tuple4(bbox, "candidate.bbox")
    best_panel: tuple[str, tuple[int, int, int, int]] | None = None
    best_area = 0
    for panel in panels:
        area = _intersection_area(candidate_bbox, panel[1])
        if area > best_area:
            best_area = area
            best_panel = panel
    return best_panel if best_area > 0 else None


def _intersection_area(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
) -> int:
    first_left, first_top, first_width, first_height = first
    second_left, second_top, second_width, second_height = second
    left = max(first_left, second_left)
    top = max(first_top, second_top)
    right = min(first_left + max(0, first_width), second_left + max(0, second_width))
    bottom = min(first_top + max(0, first_height), second_top + max(0, second_height))
    return max(0, right - left) * max(0, bottom - top)


def _candidate_set_warning_codes(
    candidate_set: MaskCandidateSet,
    warnings: object,
) -> tuple[str, ...]:
    values: list[object] = [
        warning.code for warning in candidate_set.warnings if getattr(warning, "code", None)
    ]
    if isinstance(warnings, (tuple, list)):
        values.extend(warnings)
    elif warnings is not None:
        values.append(warnings)
    return tuple(values)


def _warning_codes(values: object) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)):
        return ()
    codes: list[str] = []
    for value in values:
        code = _warning_code(value)
        if code:
            codes.append(code)
    return tuple(codes)


def _warning_code(value: object) -> str:
    text = "_".join(str(value or "").strip().split())
    return "".join(char for char in text if char in _WARNING_CODE_CHARS)[:80]


def _asset_ref_manifest_dict(asset: AssetRef) -> dict[str, object]:
    payload = asset.to_dict()
    payload["metadata"] = {}
    return payload


def _safe_relative_path(value: object) -> str:
    text = str(value or "").strip().replace("\\", "/")
    lowered = text.lower()
    if not text:
        raise Sam3ComicMaskPackageError("metadata_path is required.")
    if "://" in text or lowered.startswith(("http:", "https:", "file:")):
        raise Sam3ComicMaskPackageError("metadata_path must be relative.")
    if text.startswith("/") or text.startswith("//") or ":" in text:
        raise Sam3ComicMaskPackageError("metadata_path must be relative.")
    parts = text.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise Sam3ComicMaskPackageError("metadata_path contains unsafe segments.")
    return "/".join(parts)


__all__ = [
    "SAM3_COMIC_MASK_CANDIDATE_REF_SCHEMA_VERSION",
    "SAM3_COMIC_MASK_PACKAGE_SCHEMA_VERSION",
    "SAM3_COMIC_PANEL_MASK_SET_SCHEMA_VERSION",
    "Sam3ComicMaskCandidateRef",
    "Sam3ComicMaskPackage",
    "Sam3ComicMaskPackageError",
    "Sam3PanelMaskSet",
    "build_comic_mask_package_from_candidate_set",
    "comic_mask_candidate_ref_from_candidate",
    "with_comic_mask_candidate_acceptance",
]
