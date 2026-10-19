"""Prepare geometric coverage for native vector/text paints on the Qt thread."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from copy import deepcopy
from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import Any

import numpy as np
from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QGuiApplication, QImage

from loomstroke.core.japanese_text.character_styles import color_runs, valid_color
from loomstroke.core.japanese_text.story_preview import preview_objects
from loomstroke.core.native_cmyk.vector_paints import object_paints, paint_inks
from loomstroke.core.snapshot_vector_rasterizer import SnapshotVectorRasterizer
from loomstroke.core.vector_text_layout import TextLayoutCalculator

_preview_objects: Callable[..., list[Any]] = preview_objects
_color_runs: Callable[..., list[tuple[int, int, tuple[int, ...]]]] = color_runs
_valid_color: Callable[..., tuple[int, int, int, int] | None] = valid_color


@dataclass(frozen=True)
class InkRaster:
    image: QImage
    bounds: QRect
    color: tuple[int, ...] | None


class _MaskRasterizer(SnapshotVectorRasterizer):
    _vector_preview: Any = None
    _vector_preview_layer_id: str | None = None


class NativeCmykVectorMasks:
    def __init__(self) -> None:
        self._cache: dict[str, tuple[Any, list[InkRaster]]] = {}

    def prepare(
        self, node: Any, preview: Any, size: tuple[int, int], profile: bytes, dtype: np.dtype[Any]
    ) -> list[InkRaster] | None:
        objects = _preview_objects(node.vector_objects, preview)
        if preview is not None:
            objects = [obj for obj in objects if obj.id != preview.id] + [preview]
        if not any(object_paints(obj) for obj in objects):
            self._cache.pop(node.id, None)
            return None
        key = (objects, size, profile, dtype)
        scratch = _MaskRasterizer(width=size[0], height=size[1])
        if len(objects) == 1 and isinstance(getattr(node, "image", None), QImage):
            obj = objects[0]
            payload = scratch._extract_text_payload(obj)
            color = _single_paint(obj, payload, object_paints(obj), profile, dtype)
            if color is not None:
                if isinstance(QGuiApplication.instance(), QGuiApplication):
                    region = scratch._vector_text_render_service._layer_raster_region(
                        SimpleNamespace(id=node.id, vector_objects=objects)
                    )
                    bounds = QRect(*region) if region else QRect(0, 0, *size)
                else:
                    bounds = QRect(
                        node.vector_raster_origin[0],
                        node.vector_raster_origin[1],
                        node.image.width(),
                        node.image.height(),
                    )
                local = bounds.translated(
                    -node.vector_raster_origin[0], -node.vector_raster_origin[1]
                )
                # RGB channels are ignored: the prepared raster contributes
                # geometry/coverage only, never an RGB-to-ink round trip.
                return self._prepared_mask(node, bounds, local, color)
        cached = self._cache.get(node.id)
        if cached is not None and cached[0] == key:
            return cached[1]
        if not isinstance(QGuiApplication.instance(), QGuiApplication) and any(
            scratch._extract_text_payload(obj) is not None for obj in objects
        ):
            raise ValueError("CMYK text ink coverage requires Qt font services")
        if scratch._vector_text_render_service._layer_has_explicit_transform(node):
            raise ValueError("CMYK transforms must be rasterized")
        result = []
        for obj in objects:
            layer = SimpleNamespace(id=node.id, vector_objects=[obj])
            region = scratch._vector_text_render_service._layer_raster_region(layer)
            bounds = QRect(*region) if region else QRect(0, 0, *size)
            paints = object_paints(obj)
            payload = scratch._extract_text_payload(obj)
            if not paints:
                result.append(InkRaster(self._raster(scratch, layer, bounds), bounds, None))
                continue
            parts = (
                _text_masks(obj, payload, paints, live=preview is not None and obj.id == preview.id)
                if payload is not None
                else _vector_masks(obj, paints)
            )
            for masked, rgba, raw in parts:
                image = self._raster(
                    scratch,
                    SimpleNamespace(id=node.id, vector_objects=[masked]),
                    bounds,
                    preview=bool(
                        (scratch._extract_text_payload(masked) or {}).get("inline_preview")
                    ),
                )
                result.append(InkRaster(image, bounds, paint_inks(raw, rgba, profile, dtype)))
        self._cache[node.id] = (deepcopy(key), result)
        return result

    def retain(self, identifiers: Iterable[str]) -> None:
        self._cache = {key: value for key, value in self._cache.items() if key in identifiers}

    def _prepared_mask(
        self, node: Any, bounds: QRect, local: QRect, color: tuple[int, ...]
    ) -> list[InkRaster]:
        key = ("prepared", node.image.cacheKey(), local.getRect(), bounds.getRect(), color)
        cached = self._cache.get(node.id)
        if cached is not None and cached[0] == key:
            return cached[1]
        image = QImage(node.image) if local == node.image.rect() else node.image.copy(local)
        result = [InkRaster(image, bounds, color)]
        self._cache[node.id] = key, result
        return result

    @staticmethod
    def _raster(
        scratch: _MaskRasterizer, layer: Any, bounds: QRect, *, preview: bool = False
    ) -> QImage:
        scratch._vector_preview = layer.vector_objects[0] if preview else None
        scratch._vector_preview_layer_id = layer.id
        try:
            return scratch._vector_text_render_service.rasterize_vector_layer(
                layer, region=(bounds.x(), bounds.y(), bounds.width(), bounds.height())
            )
        finally:
            scratch._vector_preview = None


def _vector_masks(obj: Any, paints: Any) -> Iterator[tuple[Any, tuple[int, ...], Any]]:
    style = obj.style
    roles = (
        ("fill", "stroke") if style.fill_enabled and style.fill_color is not None else ("stroke",)
    )
    for path in obj.paths:
        for role in roles:
            rgb = tuple(getattr(style, f"{role}_color")[:3])
            raw = paints.get(role)
            rgba = (*rgb, 255)
            if (
                isinstance(raw, dict)
                and isinstance(raw.get("rgba"), (list, tuple))
                and len(raw["rgba"]) == 4
                and tuple(raw["rgba"][:3]) == rgb
                and isinstance(raw["rgba"][3], int)
                and 0 <= raw["rgba"][3] <= 255
            ):
                rgba = (*rgb, raw["rgba"][3])
            masked_style = SimpleNamespace(**vars(style))
            masked_style.stroke_color = QColor(0, 0, 0, rgba[3] if role == "stroke" else 0)
            masked_style.fill_enabled = role == "fill"
            masked_style.fill_color = QColor(0, 0, 0, rgba[3])
            masked = replace(obj, paths=[path], style=masked_style)
            yield masked, rgba, raw


def _single_paint(
    obj: Any, payload: Any, paints: Any, profile: bytes, dtype: np.dtype[Any]
) -> tuple[int, ...] | None:
    if payload is not None:
        if _color_runs(payload):
            return None
        defaults = TextLayoutCalculator.text_style_defaults(payload)
        rgba = _valid_color(defaults.get("color_rgba", payload.get("color_rgba"))) or (0, 0, 0, 255)
        return paint_inks(paints.get("text"), rgba, profile, dtype)
    style = obj.style
    colors = []
    roles = (
        ("stroke", "fill") if style.fill_enabled and style.fill_color is not None else ("stroke",)
    )
    for role in roles:
        raw = paints.get(role)
        if (
            isinstance(raw, dict)
            and _valid_color(raw.get("rgba")) is not None
            and raw["rgba"][3] != 255
        ):
            return None
        rgba = (*tuple(getattr(style, f"{role}_color")[:3]), 255)
        colors.append(paint_inks(raw, rgba, profile, dtype))
    return colors[0] if colors and all(color == colors[0] for color in colors) else None


def _text_masks(
    obj: Any, payload: Any, paints: Any, *, live: bool = False
) -> Iterator[tuple[Any, tuple[int, ...], Any]]:
    defaults = TextLayoutCalculator.text_style_defaults(payload)
    rgba = _valid_color(defaults.get("color_rgba", payload.get("color_rgba"))) or (0, 0, 0, 255)
    chrome = live and bool(payload.get("inline_preview"))
    if chrome:
        yield _text_chrome(obj, payload, selection=True), rgba, paints.get("text")
    variants: list[tuple[tuple[int, ...], Any]] = [(rgba, paints.get("text"))]
    runs = _color_runs(payload)
    for _start, _end, color in runs:
        if (color, None) not in variants:
            variants.append((color, None))
    for selected, raw in variants:
        mask = deepcopy(payload)
        mask.pop("inline_preview", None)
        default_color = (0, 0, 0, rgba[3] if raw is paints.get("text") and selected == rgba else 0)
        mask["color_rgba"] = list(default_color)
        mask.setdefault("style_map", {}).setdefault("default", {})["color_rgba"] = list(
            default_color
        )
        for run in mask.get("document", {}).get("runs", []):
            if "color_rgba" in run:
                color = tuple(run["color_rgba"])
                run["color_rgba"] = [0, 0, 0, color[3] if raw is None and color == selected else 0]
        yield replace(obj, meta={**obj.meta, "text_tool": mask}), selected, raw
    if chrome:
        yield _text_chrome(obj, payload, selection=False), rgba, paints.get("text")


def _text_chrome(obj: Any, payload: Any, *, selection: bool) -> Any:
    mask = deepcopy(payload)
    mask["color_rgba"] = [0, 0, 0, 0]
    mask.setdefault("style_map", {}).setdefault("default", {})["color_rgba"] = [0, 0, 0, 0]
    for run in mask.get("document", {}).get("runs", []):
        if "color_rgba" in run:
            run["color_rgba"] = [0, 0, 0, 0]
    inline = mask["inline_preview"]
    if selection:
        inline["cursor_pos"] = None
    else:
        inline.pop("selection_start", None)
        inline.pop("selection_end", None)
    return replace(obj, meta={**obj.meta, "text_tool": mask})
