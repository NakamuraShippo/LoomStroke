"""Bring an outlined text object back to editable text (CT-25 ``RestoreEditableTextCommand``).

Reads the serialized source object kept under ``text_outline_source`` and
swaps it back in through ``VectorReplaceCommand``; path edits made after
the outline are lost (the UI confirms first).  Fails cleanly when the
metadata is missing or malformed.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..io.layer_io import deserialize_vector_objects
from ..plugins.vector_tools.common import Command, VectorReplaceCommand

__all__ = ["RestoreEditableTextCommand"]


class RestoreEditableTextCommand(Command):
    SOURCE_KEY: str = "text_outline_source"

    def __init__(self, outlined_object: Any, *, layer_id: str | None = None) -> None:
        super().__init__(merge_key=None)
        self.target_id = str(getattr(outlined_object, "id", "") or "")
        self.layer_id = layer_id
        self.restored = self.source_object(outlined_object)
        self._inner: VectorReplaceCommand | None = (
            VectorReplaceCommand(self.target_id, self.restored, layer_id=layer_id)
            if self.restored is not None
            else None
        )

    @classmethod
    def source_object(cls, outlined_object: Any) -> Any | None:
        meta = getattr(outlined_object, "meta", None)
        source = meta.get(cls.SOURCE_KEY) if isinstance(meta, Mapping) else None
        if not isinstance(source, Mapping):
            return None
        raw = source.get("object")
        if not isinstance(raw, Mapping):
            return None
        try:
            objects = deserialize_vector_objects([dict(raw)])
        except Exception:
            return None
        if not objects:
            return None
        restored = objects[0]
        text_meta = getattr(restored, "meta", None)
        if not isinstance(text_meta, Mapping) or "text_tool" not in text_meta:
            return None
        return restored

    @property
    def can_apply(self) -> bool:
        return self._inner is not None

    def apply(self, canvas: Any) -> bool:
        return bool(self._inner is not None and self._inner.apply(canvas))

    def revert(self, canvas: Any) -> bool:
        return bool(self._inner is None or self._inner.revert(canvas))
