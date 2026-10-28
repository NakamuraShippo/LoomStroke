"""Vector adapter for independently implemented effect geometry."""

from __future__ import annotations

from ...core.effect_line_geometry import streamline_params, streamline_polygons
from ...core.flash_geometry import flash_params, flash_polygons
from .common import Anchor, VectorPath, VectorStyle

EFFECT_SHAPE_IDS = frozenset({"stream_lines", "solid_flash", "urchin_flash"})


def effect_params(shape_id: str, raw: dict) -> dict[str, float | int]:
    if shape_id in ("solid_flash", "urchin_flash"):
        return flash_params(raw)
    if shape_id != "stream_lines":
        raise ValueError("Unknown effect")
    return streamline_params(raw)


def effect_paths(shape_id: str, bbox: tuple, params: dict) -> list[VectorPath]:
    if shape_id in ("solid_flash", "urchin_flash"):
        return [
            VectorPath([Anchor(point) for point in polygon], closed=True)
            for polygon in flash_polygons(bbox, params, inward=shape_id == "solid_flash")
        ]
    if shape_id != "stream_lines":
        raise ValueError("Unknown effect")
    return [
        VectorPath([Anchor(point) for point in polygon], closed=True)
        for polygon in streamline_polygons(bbox, params)
    ]


def effect_style(params: dict | None = None) -> VectorStyle:
    color = (255, 255, 255) if params and params.get("white") else (0, 0, 0)
    return VectorStyle(stroke_width=0.0, fill_enabled=True, fill_color=color)
