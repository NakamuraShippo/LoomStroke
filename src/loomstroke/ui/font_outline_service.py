"""Text object -> outline polygons (CT-25 F4 ``FontOutlineService``).

Drives the *real* text render primitives with a :class:`PathRecordingPainter`
so the outline follows the exact layout (wrap, kinsoku, vertical glyph
rotation, tracking).  Each glyph run is converted with
``QPainterPath.toFillPolygon`` — a single rewound polygon per run — so
counters (the hole in "o") survive the canvas's hole-less subpath fill.
The original object is kept as serialized source metadata for
``Restore Editable Text``.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from PySide6.QtGui import QPainterPath

from ..core.fonts.outlined_text_payload import OutlinedTextPayload
from ..core.vector_text_layout import TextLayoutCalculator
from ..core.vector_text_render_service import VectorTextRenderService
from ..io.layer_io import serialize_vector_objects
from .path_recording_painter import PathRecordingPainter

logger = logging.getLogger(__name__)

__all__ = ["FontOutlineService"]


class FontOutlineService:
    SOURCE_KEY: str = "text_outline_source"
    MAX_POLYGONS: int = 20_000

    def __init__(self, canvas: Any | None = None) -> None:
        self._service = VectorTextRenderService(canvas)

    def outline(self, vec: Any) -> OutlinedTextPayload | None:
        payload = TextLayoutCalculator.extract_text_payload(vec)
        if payload is None:
            return None
        bounds = TextLayoutCalculator.text_bbox_from_payload(payload)
        context = VectorTextRenderService.text_object_render_context(
            payload=payload,
            bounds=bounds,
            selection=None,
            object_id=getattr(vec, "id", ""),
            layer_id="",
            is_preview_object=False,
        )
        if context is None:
            return None
        recorder = PathRecordingPainter(context.font)
        try:
            self._service.draw_text_object_content(recorder, context)
        except Exception:
            logger.warning("text outline failed", exc_info=True)
            return None
        polygons = self._polygons(recorder.path)
        if len(polygons) > self.MAX_POLYGONS:
            logger.warning("text outline exceeds the polygon budget; refusing")
            return None
        color = context.color
        rect = recorder.path.boundingRect()
        return OutlinedTextPayload(
            object_id=str(getattr(vec, "id", "") or ""),
            polygons=polygons,
            bounds=(rect.x(), rect.y(), rect.x() + rect.width(), rect.y() + rect.height()),
            color_rgba=(color.red(), color.green(), color.blue(), color.alpha()),
            source_meta=self.source_meta(vec),
            glyph_count=recorder.glyph_runs,
        )

    @staticmethod
    def _polygons(path: QPainterPath) -> tuple[tuple[tuple[float, float], ...], ...]:
        polygons: list[tuple[tuple[float, float], ...]] = []
        for sub in path.toSubpathPolygons():
            points = tuple((float(p.x()), float(p.y())) for p in sub)
            if len(points) >= 3:
                polygons.append(points)
        # Rewind overlapping subpaths (counters) into fillable polygons.
        try:
            filled = path.toFillPolygons()
        except Exception:
            filled = []
        if filled:
            polygons = [
                tuple((float(p.x()), float(p.y())) for p in poly)
                for poly in filled
                if len(poly) >= 3
            ]
        return tuple(polygons)

    @classmethod
    def source_meta(cls, vec: Any) -> Mapping[str, Any]:
        try:
            serialized = serialize_vector_objects([vec])
        except Exception:
            logger.debug("text outline: source serialization failed", exc_info=True)
            return {}
        return {
            "schema": "loomstroke.text_outline_source.v1",
            "object": serialized[0] if serialized else {},
        }
