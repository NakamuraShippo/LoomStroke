"""Optional colour settings for new-document requests; old requests stay RGBA8."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class NewDocumentColorSpec:
    mode_id: str = "rgba8"
    profile: bytes | None = None

    def __post_init__(self) -> None:
        if self.mode_id not in ("rgba8", "cmyk8", "cmyk16"):
            raise ValueError("Unsupported new-document mode")
        if self.mode_id != "rgba8" and (
            not isinstance(self.profile, bytes) or not 128 <= len(self.profile) <= 4 * 1024 * 1024
        ):
            raise ValueError("A CMYK ICC profile is required")
        if self.mode_id == "rgba8" and self.profile is not None:
            raise ValueError("RGB creation does not take a CMYK profile")

    def to_payload(self) -> dict[str, Any]:
        return {
            "mode_id": self.mode_id,
            "icc_base64": base64.b64encode(self.profile).decode("ascii") if self.profile else None,
        }

    @classmethod
    def from_payload(cls, payload: Any) -> NewDocumentColorSpec:
        if payload is None:
            return cls()
        if not isinstance(payload, Mapping):
            raise ValueError("Invalid new-document colour settings")
        encoded = payload.get("icc_base64")
        profile = None
        if encoded is not None:
            if not isinstance(encoded, str) or len(encoded) > 5_592_408:
                raise ValueError("Invalid new-document ICC payload")
            try:
                profile = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValueError("Invalid new-document ICC payload") from exc
        return cls(str(payload.get("mode_id", "rgba8")), profile)
