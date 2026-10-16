"""One published page (or Webtoon segment) in logical order (CT-26)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

__all__ = ["PublicationItem", "PublicationRole", "SpreadSide"]

PublicationRole = Literal["cover", "front_matter", "body", "back_matter"]
SpreadSide = Literal["left", "right", "center", "unspecified"]

_MEDIA_TYPES: dict[str, str] = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
}


@dataclass(frozen=True, slots=True)
class PublicationItem:
    """``logical_index`` is story order from 0 and never flips with binding;
    ``package_path`` is the normalized relative member path."""

    source_id: str
    logical_index: int
    role: PublicationRole
    spread_side: SpreadSide
    media_type: str
    package_path: str
    content_hash: str = ""
    pixel_size: tuple[int, int] = (0, 0)
    byte_size: int = 0
    alt_text: str | None = None

    @staticmethod
    def media_type_for(extension: str) -> str:
        key = extension.lower().lstrip(".")
        if key not in _MEDIA_TYPES:
            raise ValueError(f"unsupported publication image type: {extension}")
        return _MEDIA_TYPES[key]

    @property
    def extension(self) -> str:
        return self.package_path.rsplit(".", 1)[-1].lower() if "." in self.package_path else ""

    @property
    def file_name(self) -> str:
        return self.package_path.rsplit("/", 1)[-1]

    def with_content(
        self, *, content_hash: str, pixel_size: tuple[int, int], byte_size: int
    ) -> PublicationItem:
        return replace(self, content_hash=content_hash, pixel_size=pixel_size, byte_size=byte_size)
