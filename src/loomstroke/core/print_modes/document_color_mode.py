"""Document colour mode descriptor (CT-24 D1): explicit mode id, profile ref, alpha, compositing."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar, Literal

__all__ = ["DocumentColorMode", "ModeId"]

ModeId = Literal["rgba8", "gray8", "mono1", "rgba16", "cmyk8", "cmyk16"]
MODE_IDS: tuple[str, ...] = ("rgba8", "gray8", "mono1", "rgba16", "cmyk8", "cmyk16")


@dataclass(frozen=True, slots=True)
class DocumentColorMode:
    mode_id: ModeId = "rgba8"
    profile_ref: str | None = None
    alpha_enabled: bool = True
    compositing_policy: str = "premultiplied_srgb"

    MODE_IDS: ClassVar[tuple[str, ...]] = MODE_IDS

    @classmethod
    def default(cls) -> DocumentColorMode:
        """The current editing truth: 8-bit premultiplied RGBA (ARGB32_Premultiplied)."""
        return cls()

    @property
    def is_default(self) -> bool:
        return self.mode_id == "rgba8"

    @property
    def requires_profile(self) -> bool:
        return self.mode_id in ("cmyk8", "cmyk16")

    def to_dict(self) -> dict[str, Any]:
        return {
            "modeId": self.mode_id,
            "profileRef": self.profile_ref,
            "alphaEnabled": bool(self.alpha_enabled),
            "compositingPolicy": self.compositing_policy,
        }

    @classmethod
    def from_dict(cls, data: Any) -> DocumentColorMode | None:
        if not isinstance(data, Mapping):
            return None
        mode_id = data.get("modeId", "rgba8")
        if mode_id not in MODE_IDS:
            return None
        profile = data.get("profileRef")
        return cls(
            mode_id,
            str(profile) if isinstance(profile, str) and profile else None,
            bool(data.get("alphaEnabled", True)),
            str(data.get("compositingPolicy") or "premultiplied_srgb"),
        )
