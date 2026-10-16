"""Provider-neutral contract for generated 3D assets."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

MASKED_VALUE = "***masked***"
CONTRACT_VERSION = "3d-generated-output-v1"
SENSITIVE_KEY_PATTERNS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "token",
    "password",
    "secret",
    "authorization",
    "bearer",
)


def _is_sensitive_key(key: object) -> bool:
    if not isinstance(key, str):
        return False
    lowered = key.lower()
    return any(pattern in lowered for pattern in SENSITIVE_KEY_PATTERNS)


def mask_sensitive_metadata(value: Any, *, parent_key: object | None = None) -> Any:
    """Return ``value`` with credential-like keys masked.

    The function is intentionally reusable by adapters and store code so
    provider-specific payloads never leak credentials into manifests/tests.
    """

    if _is_sensitive_key(parent_key) and isinstance(value, (bytes, str)):
        return MASKED_VALUE
    if isinstance(value, Mapping):
        return {
            str(key): mask_sensitive_metadata(child, parent_key=key) for key, child in value.items()
        }
    if isinstance(value, list):
        return [mask_sensitive_metadata(child, parent_key=parent_key) for child in value]
    if isinstance(value, tuple):
        return tuple(mask_sensitive_metadata(child, parent_key=parent_key) for child in value)
    return value


@dataclass(frozen=True, slots=True)
class ProviderArtifact:
    """One artifact returned by a 3D generation provider adapter."""

    kind: str
    uri: str | None = None
    local_path: Path | None = None
    role: str | None = None
    mime_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.uri is None and self.local_path is None:
            raise ValueError("ProviderArtifact requires either uri or local_path")
        object.__setattr__(self, "metadata", mask_sensitive_metadata(self.metadata))


@dataclass(frozen=True, slots=True)
class Generated3DProviderOutput:
    """Normalized output contract shared by all 3D providers."""

    provider_id: str
    job_id: str
    artifacts: tuple[ProviderArtifact, ...]
    contract_version: str = CONTRACT_VERSION
    prompt: str | None = None
    seed: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifacts", tuple(self.artifacts))
        object.__setattr__(self, "metadata", mask_sensitive_metadata(self.metadata))


@runtime_checkable
class ProviderAdapter(Protocol):
    """Adapter that converts provider-specific responses to the output contract."""

    provider_id: str
    requires_external_services: bool

    def normalize_response(self, response: Mapping[str, Any]) -> Generated3DProviderOutput:
        """Normalize a provider response into ``Generated3DProviderOutput``."""
        ...
