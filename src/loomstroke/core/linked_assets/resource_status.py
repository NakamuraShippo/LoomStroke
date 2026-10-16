"""Observed state of one resource at inspection time (CT-18 §7.1 ``state``)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

__all__ = ["ResourceState", "ResourceStatus"]

ResourceState = Literal["ok", "modified", "missing", "unsupported", "unsafe", "offline"]


@dataclass(frozen=True, slots=True)
class ResourceStatus:
    resource_id: str
    state: ResourceState
    resolved_path: Path | None = None
    current_hash: str = ""
    byte_size: int = 0
    modified_at: float | None = None
    reason_code: str = ""

    @property
    def is_available(self) -> bool:
        """The file can be read right now (its content may differ from the record)."""
        return self.state in {"ok", "modified"}

    @property
    def needs_attention(self) -> bool:
        return self.state != "ok"
