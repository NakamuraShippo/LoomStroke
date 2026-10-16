"""Pre-flight answer for one SAM3 source scope: ready or stable blocked codes (CT-23 §7.1)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["Sam3SourceReadiness"]


@dataclass(frozen=True, slots=True)
class Sam3SourceReadiness:
    scope: str
    ready: bool
    blocked_reason_codes: tuple[str, ...] = ()
    width: int = 0
    height: int = 0
    page_id: str | None = None
    layer_id: str | None = None
    crop_bounds: tuple[int, int, int, int] | None = None

    @property
    def pixel_count(self) -> int:
        return max(0, int(self.width)) * max(0, int(self.height))

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "ready": bool(self.ready),
            "blockedReasonCodes": list(self.blocked_reason_codes),
            "width": int(self.width),
            "height": int(self.height),
            "pixelCount": self.pixel_count,
            "pageId": self.page_id,
            "layerId": self.layer_id,
            "cropBounds": list(self.crop_bounds) if self.crop_bounds is not None else None,
        }
