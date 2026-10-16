"""Dry-run result of a document mode conversion (CT-24): blocked reasons, estimates, warnings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .document_color_mode import DocumentColorMode

__all__ = ["ModeConversionPlan"]


@dataclass(frozen=True, slots=True)
class ModeConversionPlan:
    source: DocumentColorMode
    target: DocumentColorMode
    width: int
    height: int
    layer_count: int
    estimated_bytes: int
    blocked_reason_codes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    lossy: bool = False

    @property
    def is_ready(self) -> bool:
        return not self.blocked_reason_codes

    @property
    def is_noop(self) -> bool:
        return self.source == self.target

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source.to_dict(),
            "target": self.target.to_dict(),
            "width": int(self.width),
            "height": int(self.height),
            "layerCount": int(self.layer_count),
            "estimatedBytes": int(self.estimated_bytes),
            "blockedReasonCodes": list(self.blocked_reason_codes),
            "warnings": list(self.warnings),
            "lossy": bool(self.lossy),
            "ready": self.is_ready,
        }
