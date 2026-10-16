"""Shared data structures for the LLM gateway plugin."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

AttachmentSource = Literal["file", "layer"]


@dataclass(frozen=True)
class LayerSelectionRequest:
    """Describes a request to capture a layer snapshot."""

    layer_id: str | None
    fmt: str = "png"
    snapshot_name: str | None = None
    include_metadata: bool = True
    request_id: str = field(default_factory=lambda: uuid4().hex)


@dataclass(frozen=True)
class LayerMetadata:
    """Metadata associated with a layer attachment."""

    id: str
    name: str
    visible: bool = True
    opacity: float = 1.0
    blend_mode: str = "normal"


@dataclass(frozen=True)
class LayerSnapshotResult:
    """Result payload returned by `layer_tasks` when a snapshot is captured."""

    request_id: str
    data: bytes
    mime: str
    width: int
    height: int
    layer: LayerMetadata | None
    source: AttachmentSource
    created_at: float


@dataclass(frozen=True)
class ImageAttachment:
    """Normalized attachment ready to be embedded in a provider payload."""

    id: str
    source: AttachmentSource
    mime: str
    data: bytes
    width: int
    height: int
    layer: LayerMetadata | None = None
    original_path: Path | None = None


@dataclass
class AttachmentRecord:
    """Tracks attachment status for UI state updates."""

    attachment: ImageAttachment
    status: Literal["pending", "ready", "failed"] = "pending"
    error: str | None = None


@dataclass
class AttachmentQueueState:
    """Represents the current queue of attachment preparation tasks."""

    active_request: LayerSelectionRequest | None = None
    pending: dict[str, AttachmentRecord] = field(default_factory=dict)


@dataclass
class ProviderPayload:
    """Provider-specific request payload wrapper."""

    body: dict[str, Any]
    headers: dict[str, str]


@dataclass(frozen=True)
class PendingAttachment:
    """Return value for asynchronous attachment preparation."""

    id: str
    future_id: str
