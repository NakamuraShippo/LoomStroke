"""External MCP schema types and structured errors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "loomstroke-mcp"
SERVER_VERSION = "0.1.0"

JSON_RPC_VERSION = "2.0"


class ReasonCode(str, Enum):
    """Stable reason codes surfaced in JSON-RPC error data."""

    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    PRIVACY_DENIED = "privacy_denied"
    CREDENTIAL_UNAVAILABLE = "credential_unavailable"
    PERMISSION_DENIED = "permission_denied"
    INVALID_ARGUMENT = "invalid_argument"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    BACKEND_FAILED = "backend_failed"
    CANCELLED = "cancelled"


class AssetKind(str, Enum):
    """Reserved asset namespace for 2D and future 3D payloads."""

    RASTER = "raster"
    VECTOR = "vector"
    MATERIAL = "material"
    TEXTURE = "texture"
    MESH = "mesh"
    SCENE3D = "scene3d"
    GENERATED_OUTPUT = "generated_output"


RESERVED_ASSET_KINDS = tuple(kind.value for kind in AssetKind)


class McpStructuredError(Exception):
    """Exception that maps to JSON-RPC error data without leaking secrets."""

    def __init__(
        self,
        reason_code: ReasonCode | str,
        message: str,
        *,
        code: int = -32000,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason_code = (
            reason_code.value if isinstance(reason_code, ReasonCode) else str(reason_code)
        )
        self.message = message
        self.code = int(code)
        self.details = dict(details or {})

    def to_error(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "data": {
                "reason_code": self.reason_code,
                "details": self.details,
            },
        }


@dataclass(frozen=True, slots=True)
class McpResourceSpec:
    uri: str
    name: str
    description: str
    mime_type: str = "application/json"

    def to_protocol(self) -> dict[str, Any]:
        return {
            "uri": self.uri,
            "name": self.name,
            "description": self.description,
            "mimeType": self.mime_type,
        }


@dataclass(frozen=True, slots=True)
class McpToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool = False
    destructive: bool = False
    external: bool = False

    def to_protocol(self) -> dict[str, Any]:
        annotations: dict[str, Any] = {
            "readOnlyHint": self.read_only,
            "destructiveHint": self.destructive,
        }
        if self.external:
            annotations["openWorldHint"] = True
        return {
            "name": self.name,
            "description": self.description,
            # MCP requires an explicit root object type, including oneOf schemas.
            "inputSchema": {"type": "object", **self.input_schema},
            "annotations": annotations,
        }


@dataclass(frozen=True, slots=True)
class McpPromptArgument:
    name: str
    description: str
    required: bool = False

    def to_protocol(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "required": self.required,
        }


@dataclass(frozen=True, slots=True)
class McpPromptSpec:
    name: str
    description: str
    arguments: tuple[McpPromptArgument, ...] = ()

    def to_protocol(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "arguments": [arg.to_protocol() for arg in self.arguments],
        }


@dataclass(frozen=True, slots=True)
class DocumentRecord:
    document_id: str
    name: str
    source: Literal["live", "headless"]
    path: str | None = None
    active_page_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PageRecord:
    page_id: str
    document_id: str
    index: int
    name: str
    dimensions: dict[str, int]
    layer_ids: tuple[str, ...]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LayerRecord:
    layer_id: str
    document_id: str
    page_id: str | None
    name: str
    layer_type: str
    visible: bool
    opacity: float
    texture_version: int = 0
    parent_id: str | None = None
    bounds: tuple[int, int, int, int] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AssetRecord:
    asset_id: str
    kind: str
    source: str
    mime_type: str
    dimensions: dict[str, int] | None = None
    color_space: str = "unknown"
    related_layer_id: str | None = None
    texture_version: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_schema(self) -> dict[str, Any]:
        return {
            "assetId": self.asset_id,
            "kind": self.kind,
            "source": self.source,
            "mimeType": self.mime_type,
            "dimensions": self.dimensions,
            "colorSpace": self.color_space,
            "relatedLayerId": self.related_layer_id,
            "textureVersion": self.texture_version,
            "metadata": dict(self.metadata),
        }


CancelCallback = Callable[[], bool]


@dataclass(slots=True)
class JobRecord:
    job_id: str
    job_class: str
    status: str
    progress: float | None = None
    related_layer_id: str | None = None
    result_asset_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    cancel: CancelCallback | None = field(default=None, repr=False, compare=False)

    def touch(self) -> None:
        self.updated_at = datetime.now(UTC).isoformat()

    def to_schema(self) -> dict[str, Any]:
        return {
            "jobId": self.job_id,
            "jobClass": self.job_class,
            "status": self.status,
            "progress": self.progress,
            "relatedLayerId": self.related_layer_id,
            "resultAssetIds": list(self.result_asset_ids),
            "metadata": dict(self.metadata),
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }
