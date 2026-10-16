"""Read-only view models for the SAM3 Manga Mask Assistant."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from .manga_mask_assistant_actions import (
    SAM3_MANGA_MASK_ASSISTANT_ACTION_REQUEST_SCHEMA_VERSION,
    build_manga_mask_assistant_action_requests,
)
from .manga_mask_assistant_model import (
    SAM3_MANGA_MASK_ASSISTANT_ROW_SCHEMA_VERSION,
    Sam3MangaMaskAssistantRow,
    build_manga_mask_assistant_rows,
)
from .manga_mask_categories import (
    SAM3_MANGA_MASK_PRESET_VALUES,
    MangaMaskPreset,
    categories_for_manga_mask_preset,
)
from .manga_mask_package import (
    SAM3_COMIC_MASK_PACKAGE_SCHEMA_VERSION,
    Sam3ComicMaskPackage,
)

SAM3_MANGA_MASK_ASSISTANT_VIEW_MODEL_SCHEMA_VERSION = "sam3.manga_mask_assistant_view_model.v1"
SAM3_MANGA_MASK_ASSISTANT_FILTER_RESULT_SCHEMA_VERSION = (
    "sam3.manga_mask_assistant_filter_result.v1"
)


class Sam3MangaMaskAssistantViewModelError(ValueError):
    """Raised when a Manga Mask Assistant view model cannot be built."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Sam3MangaMaskAssistantViewModel:
    package_id: str
    source_document_id: str
    source_page_id: str
    rows: tuple[Sam3MangaMaskAssistantRow, ...]
    package_warning_codes: tuple[str, ...] = ()
    warning_codes: tuple[str, ...] = ()
    schema_version: str = SAM3_MANGA_MASK_ASSISTANT_VIEW_MODEL_SCHEMA_VERSION
    package_schema_version: str = SAM3_COMIC_MASK_PACKAGE_SCHEMA_VERSION
    row_schema_version: str = SAM3_MANGA_MASK_ASSISTANT_ROW_SCHEMA_VERSION
    action_request_schema_version: str = SAM3_MANGA_MASK_ASSISTANT_ACTION_REQUEST_SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        category_counts = _category_counts(self.rows)
        return {
            "schemaVersion": self.schema_version,
            "packageSchemaVersion": self.package_schema_version,
            "rowSchemaVersion": self.row_schema_version,
            "actionRequestSchemaVersion": self.action_request_schema_version,
            "packageId": self.package_id,
            "sourceDocumentId": self.source_document_id,
            "sourcePageId": self.source_page_id,
            "rowCount": len(self.rows),
            "panelRowCount": sum(1 for row in self.rows if row.scope == "panel"),
            "globalRowCount": sum(1 for row in self.rows if row.scope == "global"),
            "acceptedRowCount": sum(1 for row in self.rows if row.accepted),
            "warningCodes": list(self.warning_codes),
            "categoryCounts": dict(category_counts),
            "presetCounts": _preset_counts(category_counts),
            "rows": [_row_payload(row) for row in self.rows],
        }


@dataclass(frozen=True, slots=True)
class Sam3MangaMaskAssistantFilterResult:
    view_model: Sam3MangaMaskAssistantViewModel
    active_preset: MangaMaskPreset
    rows: tuple[Sam3MangaMaskAssistantRow, ...]
    schema_version: str = SAM3_MANGA_MASK_ASSISTANT_FILTER_RESULT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "viewModelSchemaVersion": self.view_model.schema_version,
            "rowSchemaVersion": self.view_model.row_schema_version,
            "actionRequestSchemaVersion": self.view_model.action_request_schema_version,
            "packageId": self.view_model.package_id,
            "sourceDocumentId": self.view_model.source_document_id,
            "sourcePageId": self.view_model.source_page_id,
            "activePreset": self.active_preset,
            "totalRowCount": len(self.view_model.rows),
            "visibleRowCount": len(self.rows),
            "hiddenRowCount": len(self.view_model.rows) - len(self.rows),
            "warningCodes": list(_warning_codes(self.view_model.package_warning_codes, self.rows)),
            "rows": [_row_payload(row) for row in self.rows],
        }


def build_manga_mask_assistant_view_model(
    package: Sam3ComicMaskPackage,
) -> Sam3MangaMaskAssistantViewModel:
    """Build a JSON-safe Manga Mask Assistant view model from a comic mask package."""

    if not isinstance(package, Sam3ComicMaskPackage):
        raise Sam3MangaMaskAssistantViewModelError(
            "manga_mask_assistant_package_invalid",
            "SAM3 Manga Mask Assistant view models require a comic mask package.",
        )

    rows = build_manga_mask_assistant_rows(package)
    return Sam3MangaMaskAssistantViewModel(
        package_id=package.package_id,
        source_document_id=package.source_document_id,
        source_page_id=package.source_page_id,
        rows=rows,
        package_warning_codes=tuple(package.warnings),
        warning_codes=_warning_codes(package.warnings, rows),
    )


def filter_manga_mask_assistant_view_model(
    view_model: Sam3MangaMaskAssistantViewModel,
    preset: MangaMaskPreset,
) -> Sam3MangaMaskAssistantFilterResult:
    """Filter a Manga Mask Assistant view model by an existing manga mask preset."""

    if not isinstance(view_model, Sam3MangaMaskAssistantViewModel):
        raise Sam3MangaMaskAssistantViewModelError(
            "manga_mask_assistant_view_model_invalid",
            "SAM3 Manga Mask Assistant preset filters require a view model.",
        )
    active_preset = _require_preset(preset)
    categories = frozenset(categories_for_manga_mask_preset(active_preset))
    rows = tuple(row for row in view_model.rows if row.category in categories)
    return Sam3MangaMaskAssistantFilterResult(
        view_model=view_model,
        active_preset=active_preset,
        rows=rows,
    )


def _row_payload(row: Sam3MangaMaskAssistantRow) -> dict[str, object]:
    payload = row.to_dict()
    payload["actionRequests"] = [
        request.to_dict() for request in build_manga_mask_assistant_action_requests(row)
    ]
    return payload


def _category_counts(
    rows: tuple[Sam3MangaMaskAssistantRow, ...],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.category] = counts.get(row.category, 0) + 1
    return counts


def _preset_counts(category_counts: dict[str, int]) -> dict[MangaMaskPreset, int]:
    counts: dict[MangaMaskPreset, int] = {}
    for preset in SAM3_MANGA_MASK_PRESET_VALUES:
        counts[preset] = sum(
            category_counts.get(category, 0)
            for category in categories_for_manga_mask_preset(preset)
        )
    return counts


def _warning_codes(
    package_warnings: tuple[str, ...],
    rows: tuple[Sam3MangaMaskAssistantRow, ...],
) -> tuple[str, ...]:
    seen: set[str] = set()
    codes: list[str] = []
    for code in (*package_warnings, *(warning for row in rows for warning in row.warnings)):
        if code in seen:
            continue
        seen.add(code)
        codes.append(code)
    return tuple(codes)


def _require_preset(preset: object) -> MangaMaskPreset:
    if not isinstance(preset, str):
        raise Sam3MangaMaskAssistantViewModelError(
            "manga_mask_assistant_preset_invalid",
            "SAM3 Manga Mask Assistant preset must be a string.",
        )
    preset_key = "_".join(str(preset).strip().lower().replace("-", " ").replace("_", " ").split())
    if preset_key not in SAM3_MANGA_MASK_PRESET_VALUES:
        raise Sam3MangaMaskAssistantViewModelError(
            "manga_mask_assistant_preset_invalid",
            "Unknown SAM3 Manga Mask Assistant preset.",
        )
    return cast(MangaMaskPreset, preset_key)


__all__ = [
    "SAM3_MANGA_MASK_ASSISTANT_FILTER_RESULT_SCHEMA_VERSION",
    "SAM3_MANGA_MASK_ASSISTANT_VIEW_MODEL_SCHEMA_VERSION",
    "Sam3MangaMaskAssistantFilterResult",
    "Sam3MangaMaskAssistantViewModel",
    "Sam3MangaMaskAssistantViewModelError",
    "build_manga_mask_assistant_view_model",
    "filter_manga_mask_assistant_view_model",
]
