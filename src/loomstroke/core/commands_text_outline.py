"""Replace a text object with its glyph outlines (CT-25 F4 ``ConvertTextToOutlinesCommand``).

One undo step: the before object (editable text) and the after object
(filled outline polygons carrying ``text_outline_source`` metadata) are
held on the command; redo re-applies the stored object, never re-parsing
fonts (spec 25 §Command / Undo).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..plugins.vector_tools.common import (
    Anchor,
    Command,
    VectorObject,
    VectorPath,
    VectorReplaceCommand,
    VectorStyle,
)
from .fonts.outlined_text_payload import OutlinedTextPayload

__all__ = ["ConvertTextToOutlinesCommand"]


class ConvertTextToOutlinesCommand(Command):
    SOURCE_KEY: str = "text_outline_source"

    def __init__(
        self,
        target_id: str,
        outline: OutlinedTextPayload,
        *,
        layer_id: str | None = None,
        source_meta: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(merge_key=None)
        self.target_id = target_id
        self.outline = outline
        self.layer_id = layer_id
        self.new_object = self.build_outline_object(target_id, outline, source_meta)
        self._inner = VectorReplaceCommand(target_id, self.new_object, layer_id=layer_id)

    @classmethod
    def build_outline_object(
        cls,
        object_id: str,
        outline: OutlinedTextPayload,
        source_meta: Mapping[str, Any] | None = None,
    ) -> VectorObject:
        paths = [
            VectorPath(anchors=[Anchor(pos=(float(x), float(y))) for x, y in polygon], closed=True)
            for polygon in outline.polygons
        ]
        r, g, b, _a = outline.color_rgba
        style = VectorStyle(
            stroke_color=(int(r), int(g), int(b)),
            stroke_width=1.0,
            fill_enabled=True,
            fill_color=(int(r), int(g), int(b)),
        )
        meta: dict[str, Any] = {
            cls.SOURCE_KEY: dict(source_meta or outline.source_meta),
            "text_outline": {"glyph_runs": outline.glyph_count, "polygons": len(outline.polygons)},
        }
        return VectorObject(id=object_id, paths=paths, style=style, meta=meta)

    def apply(self, canvas: Any) -> bool:
        if not self.outline.polygons:
            return False
        return bool(self._inner.apply(canvas))

    def revert(self, canvas: Any) -> bool:
        return bool(self._inner.revert(canvas))
