from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ...vector_tools.common import Anchor, VectorPath, VectorStyle


@dataclass(frozen=True)
class ShapeRecord:
    shape_id: str
    name: str
    category: str
    tags: tuple[str, ...]
    created_at_iso: str
    updated_at_iso: str
    svg_path: str
    template_path: str
    thumb_path: str
    default_insert_policy: str
    default_size_px: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "shape_id": self.shape_id,
            "name": self.name,
            "category": self.category,
            "tags": list(self.tags),
            "created_at_iso": self.created_at_iso,
            "updated_at_iso": self.updated_at_iso,
            "svg_path": self.svg_path,
            "template_path": self.template_path,
            "thumb_path": self.thumb_path,
            "default_insert_policy": self.default_insert_policy,
            "default_size_px": self.default_size_px,
        }

    @staticmethod
    def from_dict(raw: Mapping[str, Any]) -> ShapeRecord:
        return ShapeRecord(
            shape_id=str(raw.get("shape_id", "")),
            name=str(raw.get("name", "")),
            category=str(raw.get("category", "custom")),
            tags=tuple(str(tag) for tag in raw.get("tags", []) if tag is not None),
            created_at_iso=str(raw.get("created_at_iso", "")),
            updated_at_iso=str(raw.get("updated_at_iso", "")),
            svg_path=str(raw.get("svg_path", "")),
            template_path=str(raw.get("template_path", "")),
            thumb_path=str(raw.get("thumb_path", "")),
            default_insert_policy=str(raw.get("default_insert_policy", "default_size")),
            default_size_px=int(raw.get("default_size_px", 320)),
        )


@dataclass(frozen=True)
class ShapeTemplate:
    paths: tuple[VectorPath, ...]
    style: VectorStyle
    bbox: tuple[float, float, float, float]
    meta: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "paths": [_path_to_dict(path) for path in self.paths],
            "style": _style_to_dict(self.style),
            "bbox": list(self.bbox),
            "meta": dict(self.meta),
        }

    @staticmethod
    def from_dict(raw: Mapping[str, Any]) -> ShapeTemplate:
        paths = tuple(_path_from_dict(entry) for entry in raw.get("paths", []) if entry)
        style = _style_from_dict(raw.get("style", {}))
        bbox_raw = raw.get("bbox", (0.0, 0.0, 0.0, 0.0))
        bbox = (
            float(bbox_raw[0]),
            float(bbox_raw[1]),
            float(bbox_raw[2]),
            float(bbox_raw[3]),
        )
        meta = raw.get("meta", {})
        if not isinstance(meta, Mapping):
            meta = {}
        return ShapeTemplate(paths=paths, style=style, bbox=bbox, meta=meta)


def _anchor_to_dict(anchor: Anchor) -> dict[str, Any]:
    return {
        "pos": [float(anchor.pos[0]), float(anchor.pos[1])],
        "kind": anchor.kind,
        "h_in": list(anchor.h_in) if anchor.h_in is not None else None,
        "h_out": list(anchor.h_out) if anchor.h_out is not None else None,
    }


def _anchor_from_dict(raw: Mapping[str, Any]) -> Anchor:
    pos = raw.get("pos", (0.0, 0.0))
    h_in = raw.get("h_in")
    h_out = raw.get("h_out")
    return Anchor(
        pos=(float(pos[0]), float(pos[1])),
        kind=str(raw.get("kind", "corner")),
        h_in=(float(h_in[0]), float(h_in[1])) if h_in else None,
        h_out=(float(h_out[0]), float(h_out[1])) if h_out else None,
    )


def _path_to_dict(path: VectorPath) -> dict[str, Any]:
    return {
        "anchors": [_anchor_to_dict(anchor) for anchor in path.anchors],
        "closed": bool(path.closed),
    }


def _path_from_dict(raw: Mapping[str, Any]) -> VectorPath:
    anchors = [_anchor_from_dict(entry) for entry in raw.get("anchors", []) if entry]
    return VectorPath(anchors=anchors, closed=bool(raw.get("closed", False)))


def _style_to_dict(style: VectorStyle) -> dict[str, Any]:
    return {
        "stroke_color": list(style.stroke_color),
        "stroke_width": float(style.stroke_width),
        "fill_enabled": bool(style.fill_enabled),
        "fill_color": list(style.fill_color) if style.fill_color is not None else None,
        "join": style.join,
        "cap": style.cap,
        "miter_limit": float(style.miter_limit),
        "dash": list(style.dash),
    }


def _style_from_dict(raw: Mapping[str, Any]) -> VectorStyle:
    stroke = raw.get("stroke_color", (0, 0, 0))
    fill = raw.get("fill_color")
    return VectorStyle(
        stroke_color=(int(stroke[0]), int(stroke[1]), int(stroke[2])),
        stroke_width=float(raw.get("stroke_width", 2.0)),
        fill_enabled=bool(raw.get("fill_enabled", False) and fill is not None),
        fill_color=(int(fill[0]), int(fill[1]), int(fill[2])) if fill is not None else None,
        join=str(raw.get("join", "miter")),
        cap=str(raw.get("cap", "butt")),
        miter_limit=float(raw.get("miter_limit", 4.0)),
        dash=tuple(float(v) for v in raw.get("dash", []) if v is not None),
    )
