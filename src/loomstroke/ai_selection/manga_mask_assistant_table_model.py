"""Table-safe projections for the SAM3 Manga Mask Assistant."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .manga_mask_assistant_view_model import Sam3MangaMaskAssistantFilterResult

SAM3_MANGA_MASK_ASSISTANT_TABLE_MODEL_SCHEMA_VERSION = "sam3.manga_mask_assistant_table_model.v1"

SAM3_MANGA_MASK_ASSISTANT_TABLE_COLUMNS = (
    "scope",
    "panel",
    "category",
    "score",
    "area",
    "accepted",
    "warnings",
    "actions",
)


class Sam3MangaMaskAssistantTableModelError(ValueError):
    """Raised when a Manga Mask Assistant table model cannot be built."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Sam3MangaMaskAssistantTableModel:
    filter_result: Sam3MangaMaskAssistantFilterResult
    schema_version: str = SAM3_MANGA_MASK_ASSISTANT_TABLE_MODEL_SCHEMA_VERSION
    columns: tuple[str, ...] = SAM3_MANGA_MASK_ASSISTANT_TABLE_COLUMNS

    def to_dict(self) -> dict[str, object]:
        filter_payload = self.filter_result.to_dict()
        return {
            "schemaVersion": self.schema_version,
            "filterResultSchemaVersion": filter_payload["schemaVersion"],
            "activePreset": filter_payload["activePreset"],
            "totalRowCount": filter_payload["totalRowCount"],
            "visibleRowCount": filter_payload["visibleRowCount"],
            "hiddenRowCount": filter_payload["hiddenRowCount"],
            "columns": list(self.columns),
            "rows": [_table_row_payload(row) for row in _rows(filter_payload)],
        }


def build_manga_mask_assistant_table_model(
    filter_result: Sam3MangaMaskAssistantFilterResult,
) -> Sam3MangaMaskAssistantTableModel:
    """Build a table-safe projection from a Manga Mask Assistant filter result."""

    if not isinstance(filter_result, Sam3MangaMaskAssistantFilterResult):
        raise Sam3MangaMaskAssistantTableModelError(
            "manga_mask_assistant_filter_result_invalid",
            "SAM3 Manga Mask Assistant table models require a filter result.",
        )
    return Sam3MangaMaskAssistantTableModel(filter_result=filter_result)


def _rows(filter_payload: dict[str, object]) -> tuple[dict[str, object], ...]:
    rows = filter_payload.get("rows")
    if not isinstance(rows, list):
        return ()
    return tuple(row for row in rows if isinstance(row, dict))


def _table_row_payload(row: dict[str, object]) -> dict[str, object]:
    return {
        "rowId": row.get("rowId"),
        "scope": row.get("scope"),
        "panelId": row.get("panelId"),
        "candidateId": row.get("candidateId"),
        "category": row.get("category"),
        "score": row.get("score"),
        "areaPx": row.get("areaPx"),
        "accepted": bool(row.get("accepted")),
        "warningCodes": _list(row.get("warnings")),
        "bbox": _list(row.get("bbox")),
        "panelLocalBbox": _optional_list(row.get("panelLocalBbox")),
        "actionButtons": [_action_button_payload(action) for action in _action_requests(row)],
    }


def _action_requests(row: dict[str, object]) -> tuple[dict[str, object], ...]:
    actions = row.get("actionRequests")
    if not isinstance(actions, list):
        return ()
    return tuple(action for action in actions if isinstance(action, dict))


def _action_button_payload(action: dict[str, object]) -> dict[str, object]:
    return {
        "actionId": action.get("actionId"),
        "action": action.get("action"),
        "enabled": bool(action.get("enabled")),
        "blockedReasonCodes": _list(action.get("blockedReasonCodes")),
    }


def _list(value: object) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    return []


def _optional_list(value: object) -> list[Any] | None:
    if value is None:
        return None
    return _list(value)


__all__ = [
    "SAM3_MANGA_MASK_ASSISTANT_TABLE_COLUMNS",
    "SAM3_MANGA_MASK_ASSISTANT_TABLE_MODEL_SCHEMA_VERSION",
    "Sam3MangaMaskAssistantTableModel",
    "Sam3MangaMaskAssistantTableModelError",
    "build_manga_mask_assistant_table_model",
]
