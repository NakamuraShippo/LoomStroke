"""Suffix -> media type / resource kind table shared by place, status and package."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from .resource_record import ResourceKind

__all__ = ["MediaTypeTable"]


class MediaTypeTable:
    OCTET_STREAM: ClassVar[str] = "application/octet-stream"
    _BY_SUFFIX: ClassVar[dict[str, str]] = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
        ".gif": "image/gif",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
        ".psd": "image/vnd.adobe.photoshop",
        ".svg": "image/svg+xml",
        ".lstroke": "application/x-loomstroke",
        ".ttf": "font/ttf",
        ".otf": "font/otf",
        ".ttc": "font/collection",
    }
    DECODABLE_RASTER: ClassVar[frozenset[str]] = frozenset(
        {
            "image/png",
            "image/jpeg",
            "image/webp",
            "image/bmp",
            "image/gif",
            "image/tiff",
            "image/vnd.adobe.photoshop",
        }
    )

    @classmethod
    def for_path(cls, path: Path | str) -> str:
        return cls._BY_SUFFIX.get(Path(path).suffix.lower(), cls.OCTET_STREAM)

    @classmethod
    def kind_for(cls, media_type: str) -> ResourceKind:
        if media_type in cls.DECODABLE_RASTER:
            return "raster_image"
        if media_type == "image/svg+xml":
            return "vector_source"
        if media_type == "application/x-loomstroke":
            return "document"
        if media_type.startswith("font/"):
            return "font"
        return "other"

    @classmethod
    def is_decodable(cls, media_type: str) -> bool:
        return media_type in cls.DECODABLE_RASTER
