"""Action request helpers for the SAM3 Manga Mask Assistant."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast, get_args
from urllib.parse import quote, unquote

from .manga_mask_assistant_model import (
    MangaMaskAssistantAction,
    MangaMaskAssistantScope,
    Sam3MangaMaskAssistantRow,
)

SAM3_MANGA_MASK_ASSISTANT_ACTION_REQUEST_SCHEMA_VERSION = (
    "sam3.manga_mask_assistant_action_request.v1"
)
SAM3_MANGA_MASK_ASSISTANT_ACTION_ID_PREFIX = "sam3_manga_mask_assistant."

_MANGA_MASK_ASSISTANT_ACTION_VALUES = tuple(
    str(value) for value in get_args(MangaMaskAssistantAction)
)
_MANGA_MASK_ASSISTANT_ACTION_VALUE_SET = frozenset(_MANGA_MASK_ASSISTANT_ACTION_VALUES)


class Sam3MangaMaskAssistantActionRequestError(ValueError):
    """Raised when Manga Mask Assistant action requests cannot be built."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Sam3MangaMaskAssistantActionTarget:
    action: MangaMaskAssistantAction
    row_id: str


@dataclass(frozen=True, slots=True)
class Sam3MangaMaskAssistantActionRequest:
    action_id: str
    action: MangaMaskAssistantAction
    row_id: str
    scope: MangaMaskAssistantScope
    package_id: str
    source_page_id: str
    candidate_id: str
    category: str
    enabled: bool
    blocked_reason_codes: tuple[str, ...]
    panel_id: str | None = None
    schema_version: str = SAM3_MANGA_MASK_ASSISTANT_ACTION_REQUEST_SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "actionId": self.action_id,
            "action": self.action,
            "rowId": self.row_id,
            "scope": self.scope,
            "packageId": self.package_id,
            "sourcePageId": self.source_page_id,
            "panelId": self.panel_id,
            "candidateId": self.candidate_id,
            "category": self.category,
            "enabled": self.enabled,
            "blockedReasonCodes": list(self.blocked_reason_codes),
        }


def manga_mask_assistant_action_id(row_id: str, action: MangaMaskAssistantAction) -> str:
    """Build a stable action id from a row id and action key."""

    action_key = _require_action(action)
    row_id_text = str(row_id)
    if not row_id_text:
        raise Sam3MangaMaskAssistantActionRequestError(
            "manga_mask_assistant_row_id_missing",
            "SAM3 Manga Mask Assistant action ids require a row id.",
        )
    return (
        f"{SAM3_MANGA_MASK_ASSISTANT_ACTION_ID_PREFIX}"
        f"{action_key}.{quote(row_id_text, safe='')}"
    )


def parse_manga_mask_assistant_action_id(
    action_id: str,
) -> Sam3MangaMaskAssistantActionTarget | None:
    """Parse a Manga Mask Assistant action id into action and row identity."""

    action_id_text = str(action_id)
    for action_key in _MANGA_MASK_ASSISTANT_ACTION_VALUES:
        prefix = f"{SAM3_MANGA_MASK_ASSISTANT_ACTION_ID_PREFIX}{action_key}."
        if not action_id_text.startswith(prefix):
            continue
        encoded_row_id = action_id_text[len(prefix) :]
        if not encoded_row_id:
            return None
        row_id = unquote(encoded_row_id).strip()
        if not row_id:
            return None
        return Sam3MangaMaskAssistantActionTarget(
            action=cast(MangaMaskAssistantAction, action_key),
            row_id=row_id,
        )
    return None


def build_manga_mask_assistant_action_request(
    row: Sam3MangaMaskAssistantRow,
    action: MangaMaskAssistantAction,
) -> Sam3MangaMaskAssistantActionRequest:
    """Build one JSON-safe action request for a Manga Mask Assistant row."""

    if not isinstance(row, Sam3MangaMaskAssistantRow):
        raise Sam3MangaMaskAssistantActionRequestError(
            "manga_mask_assistant_row_invalid",
            "SAM3 Manga Mask Assistant action requests require an assistant row.",
        )
    action_key = _require_action(action)
    enabled = action_key in row.actions
    return Sam3MangaMaskAssistantActionRequest(
        action_id=manga_mask_assistant_action_id(row.row_id, action_key),
        action=action_key,
        row_id=row.row_id,
        scope=row.scope,
        package_id=row.package_id,
        source_page_id=row.source_page_id,
        panel_id=row.panel_id,
        candidate_id=row.candidate_id,
        category=row.category,
        enabled=enabled,
        blocked_reason_codes=() if enabled else ("manga_mask_assistant_action_unavailable",),
    )


def build_manga_mask_assistant_action_requests(
    row: Sam3MangaMaskAssistantRow,
) -> tuple[Sam3MangaMaskAssistantActionRequest, ...]:
    """Build action requests in the stable row action order."""

    if not isinstance(row, Sam3MangaMaskAssistantRow):
        raise Sam3MangaMaskAssistantActionRequestError(
            "manga_mask_assistant_row_invalid",
            "SAM3 Manga Mask Assistant action requests require an assistant row.",
        )
    return tuple(build_manga_mask_assistant_action_request(row, action) for action in row.actions)


def _require_action(action: str) -> MangaMaskAssistantAction:
    action_key = str(action)
    if action_key not in _MANGA_MASK_ASSISTANT_ACTION_VALUE_SET:
        raise Sam3MangaMaskAssistantActionRequestError(
            "manga_mask_assistant_action_unknown",
            "Unknown SAM3 Manga Mask Assistant action.",
        )
    return cast(MangaMaskAssistantAction, action_key)


__all__ = [
    "SAM3_MANGA_MASK_ASSISTANT_ACTION_ID_PREFIX",
    "SAM3_MANGA_MASK_ASSISTANT_ACTION_REQUEST_SCHEMA_VERSION",
    "Sam3MangaMaskAssistantActionRequest",
    "Sam3MangaMaskAssistantActionRequestError",
    "Sam3MangaMaskAssistantActionTarget",
    "build_manga_mask_assistant_action_request",
    "build_manga_mask_assistant_action_requests",
    "manga_mask_assistant_action_id",
    "parse_manga_mask_assistant_action_id",
]
