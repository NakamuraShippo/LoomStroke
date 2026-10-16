"""Builds the resource record for a file the user places (CT-18 §5.1)."""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

from .media_type_table import MediaTypeTable
from .place_resource_plan import PlaceResourcePlan
from .resource_content_hasher import ResourceContentHasher
from .resource_locator_resolver import ResourceLocatorResolver
from .resource_record import ResourceMode, ResourceRecord

__all__ = ["ResourcePlacePlanner"]


class ResourcePlacePlanner:
    _UNSAFE_CHARS: ClassVar[re.Pattern[str]] = re.compile(r"[\x00-\x1f\x7f/\\:*?\"<>|]+")
    MAX_NAME_CHARS: ClassVar[int] = 120

    def __init__(
        self,
        resolver: ResourceLocatorResolver,
        hasher: ResourceContentHasher | None = None,
        *,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._resolver = resolver
        self._hasher = hasher or ResourceContentHasher()
        self._id_factory = id_factory or self.new_resource_id

    @staticmethod
    def new_resource_id() -> str:
        return "res_" + uuid.uuid4().hex[:12]

    def plan(
        self,
        path: Path | str,
        mode: ResourceMode,
        *,
        width: int = 0,
        height: int = 0,
        dpi: tuple[float, float] | None = None,
    ) -> PlaceResourcePlan:
        source = Path(path)
        warnings: list[str] = []
        media_type = MediaTypeTable.for_path(source)
        kind = MediaTypeTable.kind_for(media_type)
        if not MediaTypeTable.is_decodable(media_type):
            warnings.append("media_type_unsupported")
        if self._resolver.is_symlink(source):
            warnings.append("resource_symlink")
        byte_size = 0
        content_hash = ""
        try:
            byte_size = source.stat().st_size
            content_hash = self._hasher.hash_file(source)
        except OSError:
            warnings.append("read_failed")
        locator = None
        if mode == "linked":
            locator = self._resolver.locator_for(source)
            if not locator.is_portable:
                warnings.append("locator_not_portable")
        record = ResourceRecord(
            resource_id=self._id_factory(),
            kind=kind,
            mode=mode,
            display_name=self.sanitize_name(source.name),
            locator=locator,
            content_hash=content_hash,
            byte_size=byte_size,
            media_type=media_type,
            width=max(0, int(width)),
            height=max(0, int(height)),
            dpi=dpi,
        )
        return PlaceResourcePlan(source, record, tuple(warnings))

    @classmethod
    def sanitize_name(cls, name: str) -> str:
        cleaned = cls._UNSAFE_CHARS.sub("_", name).strip(" .")
        return (cleaned or "resource")[: cls.MAX_NAME_CHARS]
