"""Resolves panel bboxes from explicit, user-visible sources (CT-23 §6 priority 1-2).

Never promotes guessed rectangles silently: the caller (manga assistant) picks
``comic_panels`` (dedicated panel frame objects) or ``vector_rectangles``
(axis-aligned closed 4-anchor vector paths on the active page).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from .sam3_panel_geometry import Sam3PanelGeometry
from .sam3_panel_geometry_set import Sam3PanelGeometrySet

__all__ = ["Sam3PanelGeometryProvider"]

_COMIC_PANEL_META_KEY = "loomstroke:comic_panel"  # owned by plugins/comic_tools (read only)


class Sam3PanelGeometryProvider:
    SOURCE_KINDS: ClassVar[tuple[str, ...]] = ("none", "comic_panels", "vector_rectangles")
    HANDLE_EPSILON: ClassVar[float] = 1e-3

    def __init__(self, host_api: object) -> None:
        self._host = host_api

    def available_sources(self) -> tuple[str, ...]:
        return self.SOURCE_KINDS

    def resolve(self, source_kind: str, *, page_id: str | None = None) -> Sam3PanelGeometrySet:
        kind = str(source_kind or "none")
        page = page_id or self._active_page_id()
        if kind == "comic_panels":
            return self._comic_panels(page)
        if kind == "vector_rectangles":
            return self._vector_rectangles(page)
        return Sam3PanelGeometrySet("none", (), page)

    # -- sources -----------------------------------------------------------------------

    def _comic_panels(self, page_id: str | None) -> Sam3PanelGeometrySet:
        from loomstroke.core.comic_panels.metadata import read_frame_layer  # noqa: PLC0415
        from loomstroke.core.comic_panels.tool_context import (  # noqa: PLC0415
            frame_content_bounds,
        )
        from loomstroke.core.tool_layer_info import tool_layer_info  # noqa: PLC0415

        panels: list[Sam3PanelGeometry] = []
        for layer_id, vec in self._vector_objects(include_hidden=True):
            meta = getattr(vec, "meta", None)
            record = meta.get(_COMIC_PANEL_META_KEY) if isinstance(meta, Mapping) else None
            if not isinstance(record, Mapping):
                continue
            frame = read_frame_layer(record.get("metadata"))
            visible_id = frame.panel_group_id if frame else layer_id
            info = tool_layer_info(getattr(self._host, "canvas", None), visible_id)
            if isinstance(info, Mapping) and not info.get("visible", True):
                continue
            panel_id = str(record.get("panel_id") or "")
            bbox = (
                frame_content_bounds(getattr(self._host, "canvas", None), frame)
                if frame
                else self._bbox(vec)
            )
            if not panel_id or bbox is None:
                continue
            try:
                order = int(record.get("reading_order", 0))
            except (TypeError, ValueError):
                order = 0
            panels.append(Sam3PanelGeometry(panel_id, bbox, order, f"{layer_id}:{vec.id}"))
        return Sam3PanelGeometrySet.build(
            "comic_panels", panels, page_id=page_id, provenance="comic_tools panel frames"
        )

    def _vector_rectangles(self, page_id: str | None) -> Sam3PanelGeometrySet:
        rects: list[tuple[tuple[int, int, int, int], str]] = []
        for layer_id, vec in self._vector_objects():
            meta = getattr(vec, "meta", None)
            if isinstance(meta, Mapping) and _COMIC_PANEL_META_KEY in meta:
                continue
            bbox = self._rectangle_bbox(vec)
            if bbox is not None:
                rects.append((bbox, f"{layer_id}:{vec.id}"))
        # Manga reading order: top to bottom, then right to left.
        rects.sort(key=lambda item: (item[0][1], -(item[0][0] + item[0][2])))
        panels = tuple(
            Sam3PanelGeometry(f"rect-{index + 1}", bbox, index, ref)
            for index, (bbox, ref) in enumerate(rects)
        )
        return Sam3PanelGeometrySet.build(
            "vector_rectangles", panels, page_id=page_id, provenance="selected vector rectangles"
        )

    # -- helpers -----------------------------------------------------------------------

    def _active_page_id(self) -> str | None:
        pages = getattr(self._host, "pages", None)
        getter = getattr(pages, "get_active_page_id", None)
        try:
            value = getter() if callable(getter) else None
        except Exception:
            value = None
        return str(value) if value else None

    def _vector_objects(self, *, include_hidden: bool = False) -> list[tuple[str, Any]]:
        canvas = getattr(self._host, "canvas", None)
        getter = getattr(canvas, "get_vector_objects", None)
        if not callable(getter):
            return []
        try:
            return [(str(layer_id), vec) for layer_id, vec in getter(include_hidden=include_hidden)]
        except TypeError:
            return [(str(layer_id), vec) for layer_id, vec in getter()]
        except Exception:
            return []

    @classmethod
    def _rectangle_bbox(cls, vec: Any) -> tuple[int, int, int, int] | None:
        paths = list(getattr(vec, "paths", None) or [])
        if len(paths) != 1:
            return None
        anchors = list(getattr(paths[0], "anchors", None) or [])
        if len(anchors) not in (4, 5):
            return None
        if len(anchors) == 5 and tuple(anchors[0].pos) != tuple(anchors[-1].pos):
            return None
        for anchor in anchors:
            for handle in (getattr(anchor, "h_in", None), getattr(anchor, "h_out", None)):
                if handle and (
                    abs(handle[0]) > cls.HANDLE_EPSILON or abs(handle[1]) > cls.HANDLE_EPSILON
                ):
                    return None
        xs = sorted({round(float(a.pos[0]), 3) for a in anchors[:4]})
        ys = sorted({round(float(a.pos[1]), 3) for a in anchors[:4]})
        if len(xs) != 2 or len(ys) != 2:
            return None  # not axis aligned
        return cls._bbox(vec)

    @staticmethod
    def _bbox(vec: Any) -> tuple[int, int, int, int] | None:
        xs: list[float] = []
        ys: list[float] = []
        for path in getattr(vec, "paths", None) or []:
            for anchor in getattr(path, "anchors", None) or []:
                xs.append(float(anchor.pos[0]))
                ys.append(float(anchor.pos[1]))
        if not xs:
            return None
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        width, height = round(x1 - x0), round(y1 - y0)
        if width <= 0 or height <= 0:
            return None
        return (round(x0), round(y0), width, height)
