"""Identity + content facts of one resource (CT-18 §7.1).

Consumers (layers) are *not* stored here; ``ResourceConsumerIndex`` derives them.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Literal

from .resource_locator import ResourceLocator

__all__ = ["ResourceKind", "ResourceMode", "ResourceRecord"]

ResourceKind = Literal["raster_image", "vector_source", "document", "font", "other"]
ResourceMode = Literal["linked", "embedded"]
_KINDS = ("raster_image", "vector_source", "document", "font", "other")


@dataclass(frozen=True, slots=True)
class ResourceRecord:
    resource_id: str
    kind: ResourceKind
    mode: ResourceMode
    display_name: str
    locator: ResourceLocator | None = None
    content_hash: str = ""
    byte_size: int = 0
    media_type: str = "application/octet-stream"
    width: int = 0
    height: int = 0
    dpi: tuple[float, float] | None = None
    license_note: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_linked(self) -> bool:
        return self.mode == "linked" and self.locator is not None

    def with_content(
        self,
        *,
        locator: ResourceLocator | None,
        content_hash: str,
        byte_size: int,
        width: int | None = None,
        height: int | None = None,
    ) -> ResourceRecord:
        return replace(
            self,
            locator=locator,
            content_hash=content_hash,
            byte_size=byte_size,
            width=self.width if width is None else width,
            height=self.height if height is None else height,
        )

    def embedded(self) -> ResourceRecord:
        return replace(self, mode="embedded", locator=None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_id": self.resource_id,
            "kind": self.kind,
            "mode": self.mode,
            "display_name": self.display_name,
            "locator": self.locator.to_dict() if self.locator is not None else None,
            "content_hash": self.content_hash,
            "byte_size": int(self.byte_size),
            "media_type": self.media_type,
            "width": int(self.width),
            "height": int(self.height),
            "dpi": list(self.dpi) if self.dpi is not None else None,
            "license_note": self.license_note,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: Any) -> ResourceRecord | None:
        if not isinstance(data, dict):
            return None
        resource_id = data.get("resource_id")
        kind = data.get("kind")
        mode = data.get("mode")
        if not isinstance(resource_id, str) or not resource_id:
            return None
        if kind not in _KINDS or mode not in ("linked", "embedded"):
            return None
        locator = ResourceLocator.from_dict(data.get("locator"))
        if mode == "linked" and locator is None:
            return None
        display_name = data.get("display_name")
        if not isinstance(display_name, str) or not display_name:
            display_name = locator.basename if locator is not None else resource_id
        extra = data.get("extra")
        return cls(
            resource_id=resource_id,
            kind=kind,
            mode=mode,
            display_name=display_name,
            locator=locator if mode == "linked" else None,
            content_hash=str(data.get("content_hash") or ""),
            byte_size=_int(data.get("byte_size")),
            media_type=str(data.get("media_type") or "application/octet-stream"),
            width=_int(data.get("width")),
            height=_int(data.get("height")),
            dpi=_dpi(data.get("dpi")),
            license_note=str(data.get("license_note") or ""),
            extra=dict(extra) if isinstance(extra, dict) else {},
        )


def _int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _dpi(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        return (float(value[0]), float(value[1]))
    except (TypeError, ValueError):
        return None
