"""
Standard event definitions for LoomStroke.

Per Master.md PR-B specifications, defines standard events for
communication between core systems, UI, and plugins.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

_MASKED = "***masked***"
_SENSITIVE_KEY_RE = re.compile(r"(?i)\b(api[_-]?key|token|password|secret|authorization|bearer)\b")
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|password|secret|authorization|bearer)\s*[:=]\s*([^,\s;]+)"
)


def _mask_sensitive_text(value: str) -> str:
    return _SENSITIVE_ASSIGNMENT_RE.sub(lambda m: f"{m.group(1)}={_MASKED}", value)


def _sanitize_debug_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): (
                _MASKED
                if _SENSITIVE_KEY_RE.search(str(key)) and isinstance(item, (str, bytes))
                else _sanitize_debug_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_debug_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_debug_value(item) for item in value)
    if isinstance(value, str):
        return _mask_sensitive_text(value)
    if isinstance(value, bytes):
        return "<bytes>"
    return value


def build_error_payload(
    *,
    reason_code: str,
    user_visible_message: str,
    error: str | Exception | None = None,
    debug_details_without_secret: dict[str, Any] | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Build a backend error event payload with stable taxonomy fields."""

    payload = dict(fields)
    payload["reason_code"] = reason_code
    payload["user_visible_message"] = user_visible_message
    payload["debug_details_without_secret"] = _sanitize_debug_value(
        debug_details_without_secret or {}
    )
    if error is not None:
        payload["error"] = _mask_sensitive_text(str(error))
    return payload


def classify_error_reason(default_reason_code: str, error: Exception) -> str:
    """Return a stable reason code for common backend failures."""

    message = str(error).lower()
    if any(token in message for token in ("archive", "zip", "compression", "path traversal")):
        return "archive_unsafe"
    if any(token in message for token in ("manifest", "format_version", "schema")):
        return "manifest_invalid"
    if isinstance(error, PermissionError):
        return "permission_denied"
    return default_reason_code


class EventType(str, Enum):
    """Standard event types used across the application."""

    # Document events
    DOC_CREATED = "doc.created"
    DOC_CHANGED = "doc.changed"
    DOC_SAVED = "doc.saved"
    DOC_LOADED = "doc.loaded"
    DOC_OPENED = "doc.opened"
    DOC_CLOSED = "doc.closed"
    DOC_SAVE_FAILED = "doc.save_failed"
    DOC_LOAD_FAILED = "doc.load_failed"
    # SAVE-ASYNC-REV-01: authoritative save-outcome stage emitted after the
    # DOC_SAVED broadcast (manual saves only).  Handlers must treat it as
    # read-only: mutating or switching the document from a DOC_SAVE_OUTCOME
    # handler is undefined behaviour.
    DOC_SAVE_OUTCOME = "doc.save_outcome"
    DOC_IO_PROGRESS = "doc.io.progress"
    DOC_HISTORY_RESET = "doc.history.reset"

    # Page events
    PAGES_CHANGED = "pages.changed"
    # After incoming hydration succeeds, before outgoing history/snapshot capture.
    # Handlers may finish transient edits, but must not switch pages recursively.
    ACTIVE_PAGE_CHANGING = "pages.active.changing"
    ACTIVE_PAGE_CHANGED = "pages.active.changed"
    PAGE_DIRTY_CHANGED = "pages.dirty.changed"
    PAGE_THUMBNAIL_UPDATED = "pages.thumbnail.updated"
    PAGE_OP_FAILED = "pages.op.failed"
    # Single vs Spread page view (CT-06).  Runtime-only UI state: it is not
    # persisted, and the canvas follows it to decide whether to draw the
    # facing page.
    PAGE_VIEW_MODE_CHANGED = "pages.view_mode.changed"

    # Layer events
    LAYER_CHANGED = "layer.changed"
    LAYER_ADDED = "layer.added"
    LAYER_REMOVED = "layer.removed"
    LAYER_SELECTED = "layer.selected"

    # Render events
    RENDER_STARTED = "render.started"
    RENDER_COMPLETED = "render.completed"
    RENDER_FAILED = "render.failed"

    # AI job events
    AI_JOB_SUBMITTED = "ai.job.submitted"
    AI_JOB_PROGRESS = "ai.job.progress"
    AI_JOB_RESULT = "ai.job.result"
    AI_JOB_FAILED = "ai.job.failed"
    AI_JOB_CANCELLED = "ai.job.cancelled"
    AI_PREVIEW_READY = "ai.preview.ready"
    AI_GENERATION_APPLIED = "ai.generation.applied"
    AI_GENERATION_REJECTED = "ai.generation.rejected"
    AI_PREVIEW_ADJUSTED = "ai.preview.adjusted"

    # Input events
    INPUT_POINTER_DOWN = "input.pointer.down"
    INPUT_POINTER_MOVE = "input.pointer.move"
    INPUT_POINTER_UP = "input.pointer.up"
    INPUT_KEY_DOWN = "input.key.down"
    INPUT_KEY_UP = "input.key.up"

    # Selection events
    SELECTION_CHANGED = "selection.changed"

    # Tool events
    TOOL_CHANGED = "tool.changed"
    TOOL_SETTINGS_CHANGED = "tool.settings.changed"

    # Settings events
    SETTINGS_CHANGED = "settings.changed"

    # Connection events
    CONNECTION_STATUS_CHANGED = "connection.status.changed"
    CONNECTION_ERROR = "connection.error"

    # Color management events
    COLOR_CHANGED = "color.changed"
    COLOR_PALETTE_MODIFIED = "color.palette.modified"
    HISTORY_STATE = "history.state"
    HISTORY_CHANGED = "history.changed"
    LANGUAGE_CHANGED = "ui.language.changed"
    MENU_VISIBILITY_CHANGED = "ui.menu.visibility"


@dataclass(frozen=True)
class EventPayload:
    """Base class for event payloads."""

    source: str | None = None
    timestamp: float | None = None


@dataclass(frozen=True)
class DocumentChangeEvent(EventPayload):
    """Payload for document change events."""

    change_type: str = ""
    affected_area: tuple[int, int, int, int] | None = None  # x, y, width, height
    layer_id: str | None = None
    command_id: str | None = None


@dataclass(frozen=True)
class DocumentIoProgressEvent(EventPayload):
    """Payload for document I/O progress notifications."""

    progress: float = 0.0
    stage: str | None = None
    path: str | None = None


@dataclass(frozen=True)
class HistoryResetEvent(EventPayload):
    """Payload for document history reset notifications."""

    reason: str = ""


@dataclass(frozen=True)
class LayerChangeEvent(EventPayload):
    """Payload for layer change events."""

    layer_id: str = ""
    change_type: str = ""
    old_value: Any = None
    new_value: Any = None


@dataclass(frozen=True)
class RenderEvent(EventPayload):
    """Payload for render events."""

    area: tuple[int, int, int, int] | None = None
    layer_ids: list[str] | None = None
    success: bool = True
    error: str | None = None


@dataclass(frozen=True)
class JobProgressEvent(EventPayload):
    """Payload for AI job progress events."""

    job_id: str = ""
    step: int = 0
    total: int = 0
    message: str | None = None
    preview_data: bytes | None = None


@dataclass(frozen=True)
class JobResultEvent(EventPayload):
    """Payload for AI job result events."""

    job_id: str = ""
    success: bool = False
    result: Any | None = None
    result_data: Any | None = None
    error: str | None = None


@dataclass(frozen=True)
class PointerEvent(EventPayload):
    """Payload for pointer input events."""

    x: float = 0.0
    y: float = 0.0
    pressure: float = 1.0
    tilt_x: float = 0.0
    tilt_y: float = 0.0
    button: int = 0
    modifiers: int = 0


@dataclass(frozen=True)
class KeyEvent(EventPayload):
    """Payload for keyboard input events."""

    key: str = ""
    code: int = 0
    modifiers: int = 0
    is_repeat: bool = False


@dataclass(frozen=True)
class ToolChangeEvent(EventPayload):
    """Payload for tool change events."""

    new_tool_id: str = ""
    old_tool_id: str | None = None
    settings: dict[str, Any] | None = None


@dataclass(frozen=True)
class SettingsChangeEvent(EventPayload):
    """Payload for settings change events."""

    key: str = ""
    old_value: Any | None = None
    new_value: Any | None = None
    category: str | None = None


@dataclass(frozen=True)
class LanguageChangeEvent(EventPayload):
    """Payload emitted when the UI language changes."""

    language: str = "system"


@dataclass(frozen=True)
class ConnectionStatusEvent(EventPayload):
    """Payload for connection status events."""

    service: str = ""
    status: str = ""
    url: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class ColorChangeEvent(EventPayload):
    """Payload for active color changes."""

    rgba: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    blend_mode: str = "normal"
    previous_rgba: tuple[float, float, float, float] | None = None
    history: tuple[tuple[float, float, float, float], ...] = ()


@dataclass(frozen=True)
class ColorPaletteEvent(EventPayload):
    """Payload for palette modifications."""

    palette_id: str = ""
    action: str = ""
    payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class HistoryStateEvent(EventPayload):
    """Payload emitted whenever history availability changes."""

    can_undo: bool = False
    can_redo: bool = False


@dataclass(frozen=True)
class HistoryChangedEvent(EventPayload):
    """Payload emitted whenever history stacks change."""

    can_undo: bool = False
    can_redo: bool = False
    length: int = 0
    cursor: int = 0


@dataclass(frozen=True)
class MenuVisibilityEvent(EventPayload):
    """Payload emitted when an optional menu is toggled."""

    menu_id: str = ""
    enabled: bool = False
    source: str | None = None


class Events:
    """Convenience aliases for event names."""

    # Document
    DOC_CREATED = EventType.DOC_CREATED.value
    DOC_CHANGED = EventType.DOC_CHANGED.value
    DOC_SAVED = EventType.DOC_SAVED.value
    DOC_LOADED = EventType.DOC_LOADED.value
    DOC_OPENED = EventType.DOC_OPENED.value
    DOC_CLOSED = EventType.DOC_CLOSED.value
    DOC_SAVE_FAILED = EventType.DOC_SAVE_FAILED.value
    DOC_LOAD_FAILED = EventType.DOC_LOAD_FAILED.value
    DOC_SAVE_OUTCOME = EventType.DOC_SAVE_OUTCOME.value
    DOC_IO_PROGRESS = EventType.DOC_IO_PROGRESS.value
    DOC_HISTORY_RESET = EventType.DOC_HISTORY_RESET.value

    # Pages
    PAGES_CHANGED = EventType.PAGES_CHANGED.value
    ACTIVE_PAGE_CHANGING = EventType.ACTIVE_PAGE_CHANGING.value
    ACTIVE_PAGE_CHANGED = EventType.ACTIVE_PAGE_CHANGED.value
    PAGE_DIRTY_CHANGED = EventType.PAGE_DIRTY_CHANGED.value
    PAGE_THUMBNAIL_UPDATED = EventType.PAGE_THUMBNAIL_UPDATED.value
    PAGE_OP_FAILED = EventType.PAGE_OP_FAILED.value
    PAGE_VIEW_MODE_CHANGED = EventType.PAGE_VIEW_MODE_CHANGED.value

    # Layer
    LAYER_CHANGED = EventType.LAYER_CHANGED.value
    LAYER_ADDED = EventType.LAYER_ADDED.value
    LAYER_REMOVED = EventType.LAYER_REMOVED.value
    LAYER_SELECTED = EventType.LAYER_SELECTED.value

    # Selection
    SELECTION_CHANGED = EventType.SELECTION_CHANGED.value

    # Render
    RENDER_STARTED = EventType.RENDER_STARTED.value
    RENDER_COMPLETED = EventType.RENDER_COMPLETED.value
    RENDER_FAILED = EventType.RENDER_FAILED.value

    # AI Jobs
    AI_JOB_SUBMITTED = EventType.AI_JOB_SUBMITTED.value
    AI_JOB_PROGRESS = EventType.AI_JOB_PROGRESS.value
    AI_JOB_RESULT = EventType.AI_JOB_RESULT.value
    AI_JOB_FAILED = EventType.AI_JOB_FAILED.value
    AI_JOB_CANCELLED = EventType.AI_JOB_CANCELLED.value
    AI_PREVIEW_READY = EventType.AI_PREVIEW_READY.value
    AI_GENERATION_APPLIED = EventType.AI_GENERATION_APPLIED.value
    AI_GENERATION_REJECTED = EventType.AI_GENERATION_REJECTED.value
    AI_PREVIEW_ADJUSTED = EventType.AI_PREVIEW_ADJUSTED.value

    # Input
    INPUT_POINTER_DOWN = EventType.INPUT_POINTER_DOWN.value
    INPUT_POINTER_MOVE = EventType.INPUT_POINTER_MOVE.value
    INPUT_POINTER_UP = EventType.INPUT_POINTER_UP.value
    INPUT_KEY_DOWN = EventType.INPUT_KEY_DOWN.value
    INPUT_KEY_UP = EventType.INPUT_KEY_UP.value

    # Tool
    TOOL_CHANGED = EventType.TOOL_CHANGED.value
    TOOL_SETTINGS_CHANGED = EventType.TOOL_SETTINGS_CHANGED.value

    # Settings
    SETTINGS_CHANGED = EventType.SETTINGS_CHANGED.value

    # Connection
    CONNECTION_STATUS_CHANGED = EventType.CONNECTION_STATUS_CHANGED.value
    CONNECTION_ERROR = EventType.CONNECTION_ERROR.value

    # Color management
    COLOR_CHANGED = EventType.COLOR_CHANGED.value
    COLOR_PALETTE_MODIFIED = EventType.COLOR_PALETTE_MODIFIED.value
    HISTORY_STATE = EventType.HISTORY_STATE.value
    HISTORY_CHANGED = EventType.HISTORY_CHANGED.value
    LANGUAGE_CHANGED = EventType.LANGUAGE_CHANGED.value
    MENU_VISIBILITY_CHANGED = EventType.MENU_VISIBILITY_CHANGED.value


__all__ = [
    "ColorChangeEvent",
    "ColorPaletteEvent",
    "ConnectionStatusEvent",
    "DocumentChangeEvent",
    "DocumentIoProgressEvent",
    "EventPayload",
    "EventType",
    "Events",
    "HistoryChangedEvent",
    "HistoryResetEvent",
    "HistoryStateEvent",
    "JobProgressEvent",
    "JobResultEvent",
    "KeyEvent",
    "LanguageChangeEvent",
    "LayerChangeEvent",
    "MenuVisibilityEvent",
    "PointerEvent",
    "RenderEvent",
    "SettingsChangeEvent",
    "ToolChangeEvent",
]
