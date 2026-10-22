from __future__ import annotations

import hashlib
import html
import logging
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from loomstroke.core.japanese_text.typography import has_typography

from .common import Anchor, VectorObject, VectorPath, VectorStyle, make_vector_object

logger = logging.getLogger(__name__)

PARAMETRIC_META_KEY = "shape_manager_parametric"
PARAMETRIC_META_VERSION = 1
_BALLOON_TAIL_RANGE = 5.0
_SAFE_SVG_ID_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.:-]{0,127}\Z")
_SENSITIVE_SVG_ID_MARKERS = (
    "\\",
    "/",
    "api_key",
    "apikey",
    "cache",
    "credential",
    "secret",
    "token",
    "user_data",
)
_SVG_LINE_CAP_VALUES = {"butt", "round", "square"}
_SVG_LINE_JOIN_VALUES = {"arcs", "bevel", "miter", "miter-clip", "round"}
_TEXT_META_KEY = "text_tool"
_SVG_FONT_FAMILY_FALLBACK = "sans-serif"

# Cloud body template derived from cloud.svg (7 cubic-bezier anchors).
# Coordinates are normalized to [-1, 1] relative to bounding-box center.
# Each tuple: (pos_x, pos_y, h_in_x, h_in_y, h_out_x, h_out_y)
_CLOUD_SVG_ANCHORS: tuple[tuple[float, float, float, float, float, float], ...] = (
    (-0.7546, -0.7596, -0.4566, +0.0247, +0.0397, -0.6631),  # top-left
    (+0.0960, -1.0000, -0.1255, -0.4280, +0.2288, -0.3490),  # top
    (+0.7848, -0.5789, +0.0533, -0.5333, +0.3646, +0.0510),  # top-right
    (+1.0000, +0.3316, +0.1725, -0.2280, +0.1228, +0.4562),  # right
    (+0.4934, +0.9211, +0.2984, +0.1878, -0.2215, +0.6668),  # bottom-right
    (-0.3477, +1.0000, +0.0500, +0.2720, -0.0758, +0.2194),  # bottom
    (-1.0000, +0.4579, -0.1553, +0.9036, -0.2580, -0.2490),  # left
)


@dataclass(frozen=True)
class BasicShapeDefinition:
    shape_id: str
    display_name: str
    category: str
    default_size_px: int = 320


@dataclass(frozen=True)
class ParametricHandle:
    handle_id: str
    pos: tuple[float, float]
    kind: str = "circle"
    color: tuple[int, int, int] | None = None  # RGB override; None = default


BASIC_SHAPE_DEFINITIONS: tuple[BasicShapeDefinition, ...] = (
    BasicShapeDefinition("ellipse", "Ellipse", "basic"),
    BasicShapeDefinition("rounded_rect", "Rounded Rectangle", "basic"),
    BasicShapeDefinition("parallelogram", "Parallelogram", "basic"),
    BasicShapeDefinition("trapezoid", "Trapezoid", "basic"),
    BasicShapeDefinition("focus_lines_circle", "Focus Lines (Circle)", "effect"),
    BasicShapeDefinition("focus_lines_rect", "Focus Lines (Rect)", "effect"),
    BasicShapeDefinition("speech_round", "Speech Balloon (Round)", "balloon"),
    BasicShapeDefinition("speech_rect", "Speech Balloon (Rect)", "balloon"),
    BasicShapeDefinition("speech_cloud", "Speech Balloon (Cloud)", "balloon"),
    BasicShapeDefinition("spiky_balloon", "Spiky Balloon", "balloon"),
    BasicShapeDefinition("polygon_3", "Triangle", "polygon"),
    BasicShapeDefinition("polygon_4", "Quadrilateral", "polygon"),
    BasicShapeDefinition("polygon_5", "Pentagon", "polygon"),
    BasicShapeDefinition("polygon_6", "Hexagon", "polygon"),
    BasicShapeDefinition("polygon_7", "Heptagon", "polygon"),
    BasicShapeDefinition("polygon_8", "Octagon", "polygon"),
    BasicShapeDefinition("polygon_9", "Nonagon", "polygon"),
    BasicShapeDefinition("polygon_10", "Decagon", "polygon"),
)

_DEFINITIONS_BY_ID = {entry.shape_id: entry for entry in BASIC_SHAPE_DEFINITIONS}
# Backward compat: old "focus_lines" maps to the circle variant
_DEFINITIONS_BY_ID["focus_lines"] = _DEFINITIONS_BY_ID["focus_lines_circle"]
_FOCUS_LINES_IDS = {"focus_lines", "focus_lines_circle", "focus_lines_rect"}


def list_basic_shape_definitions() -> tuple[BasicShapeDefinition, ...]:
    return BASIC_SHAPE_DEFINITIONS


def get_basic_shape_definition(shape_id: str) -> BasicShapeDefinition | None:
    return _DEFINITIONS_BY_ID.get(shape_id)


def create_basic_shape_object(
    shape_id: str,
    center: tuple[float, float],
    *,
    default_size_px: int = 320,
    style: VectorStyle | None = None,
) -> VectorObject | None:
    if shape_id not in _DEFINITIONS_BY_ID:
        return None
    params = _default_params(shape_id)
    width, height = _default_size_for_shape(shape_id, float(default_size_px))
    cx, cy = center
    bbox = (
        float(cx - width * 0.5),
        float(cy - height * 0.5),
        float(cx + width * 0.5),
        float(cy + height * 0.5),
    )
    paths = _build_paths(shape_id, bbox, params)
    if not paths:
        return None
    if style is not None:
        vector_style = style
    elif shape_id in {"speech_round", "speech_rect", "speech_cloud", "spiky_balloon"}:
        vector_style = VectorStyle(
            stroke_color=(0, 0, 0),
            stroke_width=2.0,
            fill_enabled=True,
            fill_color=(255, 255, 255),
        )
    elif shape_id in _FOCUS_LINES_IDS:
        vector_style = VectorStyle(
            stroke_color=(0, 0, 0),
            stroke_width=0.0,
            fill_enabled=True,
            fill_color=(0, 0, 0),
        )
    else:
        vector_style = VectorStyle(
            stroke_color=(0, 0, 0),
            stroke_width=2.0,
            fill_enabled=False,
        )
    meta = {
        PARAMETRIC_META_KEY: {
            "version": PARAMETRIC_META_VERSION,
            "shape_id": shape_id,
            "params": params,
            "editable": True,
        }
    }
    if (
        shape_id
        in {"speech_round", "speech_rect", "speech_cloud", "parallelogram"} | _FOCUS_LINES_IDS
    ):
        meta[PARAMETRIC_META_KEY]["reference_bbox"] = [bbox[0], bbox[1], bbox[2], bbox[3]]
    return make_vector_object(paths, vector_style, meta=meta)


def build_parametric_shape_object(
    shape_id: str,
    bbox: tuple[float, float, float, float],
    params: dict[str, float | int] | None = None,
    *,
    style: VectorStyle,
    object_id: str | None = None,
    extra_meta: dict[str, Any] | None = None,
) -> VectorObject | None:
    """Build a parametric shape from an explicit bbox and params.

    Comic Tools balloons need deterministic ids, explicit body bboxes and
    tail params — ``create_basic_shape_object`` only offers center+defaults.
    """

    if shape_id not in _DEFINITIONS_BY_ID:
        return None
    normalized = _sanitize_params(shape_id, dict(params or {}))
    paths = _build_paths(shape_id, bbox, normalized)
    if not paths:
        return None
    meta: dict[str, Any] = {
        PARAMETRIC_META_KEY: {
            "version": PARAMETRIC_META_VERSION,
            "shape_id": shape_id,
            "params": normalized,
            "editable": True,
            "reference_bbox": [bbox[0], bbox[1], bbox[2], bbox[3]],
        }
    }
    if extra_meta:
        meta.update(extra_meta)
    obj = make_vector_object(paths, style, meta=meta)
    if object_id:
        obj = VectorObject(
            id=object_id, paths=obj.paths, style=obj.style, meta=obj.meta, preview=obj.preview
        )
    return obj


def tail_tip_position(
    bbox: tuple[float, float, float, float],
    params: Mapping[str, float | int],
) -> tuple[float, float]:
    """Public accessor for a balloon's tail tip in page coordinates."""

    return _tail_tip_from_params(bbox, dict(params))


def tail_params_for_tip(
    bbox: tuple[float, float, float, float],
    tip: tuple[float, float],
) -> dict[str, float]:
    """Inverse of :func:`tail_tip_position` (bbox-relative tail_x/tail_y)."""

    min_x, min_y, max_x, max_y = bbox
    cx = (min_x + max_x) * 0.5
    cy = (min_y + max_y) * 0.5
    width = max(1.0, max_x - min_x)
    height = max(1.0, max_y - min_y)
    tail_x = (float(tip[0]) - cx) / (width * 0.5)
    tail_y = (float(tip[1]) - cy) / (height * 0.5)
    clamp = _BALLOON_TAIL_RANGE
    return {
        "tail_x": max(-clamp, min(clamp, tail_x)),
        "tail_y": max(-clamp, min(clamp, tail_y)),
    }


def is_editable_parametric_shape(obj: object) -> bool:
    payload = _parametric_payload(obj)
    if payload is None:
        return False
    return bool(payload.get("editable", True))


def is_parametric_shape(obj: object) -> bool:
    return _parametric_payload(obj) is not None


def convert_parametric_to_vector(obj: object) -> VectorObject | None:
    payload = _parametric_payload(obj)
    if payload is None:
        return None
    meta_src = getattr(obj, "meta", {})
    meta: dict[str, object] = dict(meta_src) if isinstance(meta_src, dict) else {}
    meta.pop(PARAMETRIC_META_KEY, None)
    return _rebuild_object(
        obj=obj,
        paths=list(getattr(obj, "paths", []) or []),
        meta=meta,
    )


def parametric_handles(obj: object) -> tuple[ParametricHandle, ...]:
    payload = _parametric_payload(obj)
    if payload is None or not bool(payload.get("editable", True)):
        return ()
    shape_id = str(payload.get("shape_id", ""))
    params = _sanitize_params(shape_id, payload.get("params"))
    bbox = _reference_bbox_for_shape(obj, shape_id, payload=payload)
    if bbox is None:
        return ()
    min_x, min_y, max_x, max_y = bbox
    width = max(1.0, max_x - min_x)
    height = max(1.0, max_y - min_y)
    cx = (min_x + max_x) * 0.5
    cy = (min_y + max_y) * 0.5

    handles: list[ParametricHandle] = []
    if shape_id == "rounded_rect":
        radius = float(params.get("radius", 0.2))
        handles.append(
            ParametricHandle(
                handle_id="radius",
                pos=(min_x + width * radius, min_y),
                kind="diamond",
            )
        )
    elif shape_id in {"speech_round", "speech_rect", "speech_cloud"}:
        tail = _tail_tip_from_params(bbox, params)
        handles.append(ParametricHandle(handle_id="tail_tip", pos=tail, kind="circle"))
    elif shape_id == "spiky_balloon":
        depth = float(params.get("spike_depth", 0.25))
        handles.append(
            ParametricHandle(
                handle_id="spike_depth",
                pos=(cx, min_y + depth * height),
                kind="diamond",
            )
        )
    elif shape_id == "parallelogram":
        skew = float(params.get("skew", 0.2))
        handles.append(
            ParametricHandle(
                handle_id="skew",
                pos=(cx + skew * width * 0.5, min_y),
                kind="diamond",
            )
        )
    elif shape_id == "trapezoid":
        inset = float(params.get("top_inset", 0.2))
        handles.append(
            ParametricHandle(
                handle_id="top_inset",
                pos=(min_x + inset * width, min_y),
                kind="diamond",
            )
        )
    elif shape_id in _FOCUS_LINES_IDS:
        inner_ratio = float(params.get("inner_ratio", 0.35))
        line_count = int(params.get("line_count", 90))
        outer_width = float(params.get("outer_width", 0.2))
        length_jitter = float(params.get("length_jitter", 0.6))
        inner_width = float(params.get("inner_width", 0.0))
        vp_x = float(params.get("vp_x", 0.0))
        vp_y = float(params.get("vp_y", 0.0))
        # Vanishing point in absolute coordinates
        abs_vp_x = cx + vp_x * width * 0.5
        abs_vp_y = cy + vp_y * height * 0.5
        handles.append(
            ParametricHandle(
                handle_id="inner_ratio",
                pos=(abs_vp_x + width * 0.5 * inner_ratio, abs_vp_y),
                kind="circle",
            )
        )
        # Red: line count (top edge, x maps to count)
        count_ratio = (line_count - 8) / max(1, 200 - 8)
        handles.append(
            ParametricHandle(
                handle_id="line_count",
                pos=(min_x + width * count_ratio, min_y),
                kind="diamond",
                color=(220, 60, 60),
            )
        )
        # Blue: outer stroke width upper bound (near right edge, y maps to width)
        inset = width * 0.08
        handles.append(
            ParametricHandle(
                handle_id="outer_width",
                pos=(max_x - inset, cy - height * 0.5 * outer_width),
                kind="circle",
                color=(60, 100, 220),
            )
        )
        # Green: inner stroke width (near left edge, y maps to width)
        handles.append(
            ParametricHandle(
                handle_id="inner_width",
                pos=(min_x + inset, cy - height * 0.5 * inner_width),
                kind="circle",
                color=(60, 180, 80),
            )
        )
        # Orange: inner length jitter (bottom edge, x maps to jitter)
        handles.append(
            ParametricHandle(
                handle_id="length_jitter",
                pos=(min_x + width * length_jitter, max_y),
                kind="diamond",
                color=(240, 150, 40),
            )
        )
        # Purple: vanishing point position
        handles.append(
            ParametricHandle(
                handle_id="vanishing_point",
                pos=(abs_vp_x, abs_vp_y),
                kind="diamond",
                color=(160, 60, 220),
            )
        )
    return tuple(handles)


def apply_parametric_handle_drag(
    obj: object,
    handle_id: str,
    point: tuple[float, float],
) -> VectorObject | None:
    payload = _parametric_payload(obj)
    if payload is None or not bool(payload.get("editable", True)):
        return None
    shape_id = str(payload.get("shape_id", ""))
    if not shape_id:
        return None
    bbox = _reference_bbox_for_shape(obj, shape_id, payload=payload)
    if bbox is None:
        return None
    params = _sanitize_params(shape_id, payload.get("params"))
    updated_params = _update_params_from_handle(shape_id, params, handle_id, point, bbox)
    if updated_params is None:
        return None
    paths = _build_paths(shape_id, bbox, updated_params)
    if not paths:
        return None

    meta_src = getattr(obj, "meta", {})
    meta: dict[str, object] = dict(meta_src) if isinstance(meta_src, dict) else {}
    meta[PARAMETRIC_META_KEY] = {
        "version": PARAMETRIC_META_VERSION,
        "shape_id": shape_id,
        "params": updated_params,
        "editable": True,
    }
    if (
        shape_id
        in {"speech_round", "speech_rect", "speech_cloud", "parallelogram"} | _FOCUS_LINES_IDS
    ):
        meta[PARAMETRIC_META_KEY]["reference_bbox"] = [bbox[0], bbox[1], bbox[2], bbox[3]]
    return _rebuild_object(obj=obj, paths=paths, meta=meta)


def _reference_bbox_for_shape(  # noqa: PLR0911
    obj: object,
    shape_id: str,
    *,
    payload: dict[str, Any] | None = None,
) -> tuple[float, float, float, float] | None:
    # Shapes whose vertices don't span the full bbox: use stored reference_bbox.
    # Move/scale tools update reference_bbox in meta, so we trust it directly.
    if shape_id in {"parallelogram"} | _FOCUS_LINES_IDS:
        if payload is not None:
            raw = payload.get("reference_bbox")
            if isinstance(raw, (list, tuple)) and len(raw) == 4:
                try:
                    return (float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3]))
                except (TypeError, ValueError):
                    logger.debug("reference_bbox parse failed", exc_info=True)
        return _compute_bbox(obj)
    if shape_id in {"speech_round", "speech_rect", "speech_cloud"}:
        if payload is not None:
            raw = payload.get("reference_bbox")
            if isinstance(raw, (list, tuple)) and len(raw) == 4:
                try:
                    stored_bbox = (
                        float(raw[0]),
                        float(raw[1]),
                        float(raw[2]),
                        float(raw[3]),
                    )
                    tail_now = _current_tail_tip(obj)
                    params = _sanitize_params(shape_id, payload.get("params"))
                    tail_pred = _tail_tip_from_params(stored_bbox, params)
                    if tail_now is None:
                        # Cloud: track movement via body center
                        body_cx, body_cy = _body_center(obj)
                        bbox_cx = (stored_bbox[0] + stored_bbox[2]) * 0.5
                        bbox_cy = (stored_bbox[1] + stored_bbox[3]) * 0.5
                        dx = body_cx - bbox_cx
                        dy = body_cy - bbox_cy
                        if abs(dx) < 0.5 and abs(dy) < 0.5:
                            return stored_bbox
                        return (
                            stored_bbox[0] + dx,
                            stored_bbox[1] + dy,
                            stored_bbox[2] + dx,
                            stored_bbox[3] + dy,
                        )
                    dx = tail_now[0] - tail_pred[0]
                    dy = tail_now[1] - tail_pred[1]
                    return (
                        stored_bbox[0] + dx,
                        stored_bbox[1] + dy,
                        stored_bbox[2] + dx,
                        stored_bbox[3] + dy,
                    )
                except (TypeError, ValueError):
                    logger.debug("speech bbox derivation failed", exc_info=True)
        current_bbox = _speech_body_bbox(obj)
        if current_bbox is not None:
            return current_bbox
    return _compute_bbox(obj)


def _current_tail_tip(obj: object) -> tuple[float, float] | None:
    paths = getattr(obj, "paths", None) or []
    if not paths:
        return None
    anchors = list(getattr(paths[0], "anchors", None) or [])
    if not anchors:
        return None
    # Multi-path balloon (cloud): no tail anchor in paths[0].
    if len(paths) > 1:
        return None
    tail = anchors[-1]
    return (float(tail.pos[0]), float(tail.pos[1]))


def _body_center(obj: object) -> tuple[float, float]:
    """Return the bbox center of the body anchors.

    For single-path shapes, uses paths[0].  For multi-path shapes whose
    first path is the main body (cloud balloon), also uses paths[0].
    For shapes where all paths contribute to the body (focus_lines),
    uses all anchors across all paths.
    """
    paths = getattr(obj, "paths", None) or []
    if not paths:
        return (0.0, 0.0)
    # Determine if all paths should be included (e.g. focus_lines)
    # Heuristic: if paths[0] has <= 2 anchors, include all paths
    first_anchors = list(getattr(paths[0], "anchors", None) or [])
    if len(first_anchors) <= 2 and len(paths) > 1:
        all_anchors = [a for p in paths for a in (getattr(p, "anchors", None) or [])]
    else:
        all_anchors = first_anchors
    if not all_anchors:
        return (0.0, 0.0)
    xs = [float(a.pos[0]) for a in all_anchors]
    ys = [float(a.pos[1]) for a in all_anchors]
    return ((min(xs) + max(xs)) * 0.5, (min(ys) + max(ys)) * 0.5)


def _rebuild_object(
    *,
    obj: object,
    paths: list[VectorPath],
    meta: dict[str, object],
) -> VectorObject:
    style = getattr(obj, "style", VectorStyle())
    vec_id = getattr(obj, "id", "")
    preview = bool(getattr(obj, "preview", False))
    return VectorObject(id=vec_id, paths=paths, style=style, meta=meta, preview=preview)


def _parametric_payload(obj: object) -> dict[str, Any] | None:
    meta = getattr(obj, "meta", None)
    if not isinstance(meta, dict):
        return None
    raw = meta.get(PARAMETRIC_META_KEY)
    if not isinstance(raw, dict):
        return None
    shape_id = raw.get("shape_id")
    if not isinstance(shape_id, str) or not shape_id:
        return None
    return raw


def _default_size_for_shape(shape_id: str, base_size: float) -> tuple[float, float]:
    size = max(32.0, base_size)
    if shape_id in {"speech_round", "speech_rect", "speech_cloud", "spiky_balloon"}:
        return (size * 1.2, size * 0.9)
    if shape_id in _FOCUS_LINES_IDS:
        return (size * 1.1, size * 1.1)
    return (size, size)


def _default_params(shape_id: str) -> dict[str, float | int]:  # noqa: PLR0911
    if shape_id == "rounded_rect":
        return {"radius": 0.2}
    if shape_id == "parallelogram":
        return {"skew": 0.2}
    if shape_id == "trapezoid":
        return {"top_inset": 0.2}
    if shape_id in _FOCUS_LINES_IDS:
        return {
            "line_count": 90,
            "inner_ratio": 0.35,
            "outer_width": 0.2,
            "inner_width": 0.0,
            "vp_x": 0.0,
            "vp_y": 0.0,
            "length_jitter": 0.6,
        }
    if shape_id == "speech_round":
        return {"tail_x": 0.5, "tail_y": 1.15, "tail_width": 0.16}
    if shape_id == "speech_rect":
        return {"tail_x": 0.55, "tail_y": 1.12, "tail_width": 0.16, "radius": 0.12}
    if shape_id == "speech_cloud":
        return {"tail_x": -0.55, "tail_y": 1.1, "tail_width": 0.2, "lobes": 7}
    if shape_id == "spiky_balloon":
        return {"spike_count": 20, "spike_depth": 0.28}
    if shape_id.startswith("polygon_"):
        try:
            sides = int(shape_id.split("_", maxsplit=1)[1])
        except (TypeError, ValueError):
            sides = 5
        return {"sides": max(3, min(10, sides))}
    return {}


def _sanitize_params(shape_id: str, raw: object) -> dict[str, float | int]:
    params = _default_params(shape_id)
    if isinstance(raw, dict):
        for key, value in raw.items():
            params[str(key)] = value  # type: ignore[assignment]  # raw dict value narrowed downstream
    return _normalize_params(shape_id, params)


def _normalize_params(shape_id: str, params: dict[str, object]) -> dict[str, float | int]:
    data: dict[str, float | int] = {}
    if shape_id == "rounded_rect":
        data["radius"] = _clamp(_float(params.get("radius"), 0.2), 0.02, 0.49)
    elif shape_id == "parallelogram":
        data["skew"] = _clamp(_float(params.get("skew"), 0.2), -0.8, 0.8)
    elif shape_id == "trapezoid":
        data["top_inset"] = _clamp(_float(params.get("top_inset"), 0.2), 0.0, 0.45)
    elif shape_id in _FOCUS_LINES_IDS:
        data["line_count"] = _int(params.get("line_count"), 90, min_value=8, max_value=200)
        data["inner_ratio"] = _clamp(_float(params.get("inner_ratio"), 0.35), 0.05, 0.8)
        data["outer_width"] = _clamp(_float(params.get("outer_width"), 0.2), 0.0, 1.0)
        data["inner_width"] = _clamp(_float(params.get("inner_width"), 0.0), 0.0, 1.0)
        data["vp_x"] = _clamp(_float(params.get("vp_x"), 0.0), -0.8, 0.8)
        data["vp_y"] = _clamp(_float(params.get("vp_y"), 0.0), -0.8, 0.8)
        data["length_jitter"] = _clamp(_float(params.get("length_jitter"), 0.6), 0.0, 1.0)
    elif shape_id in {"speech_round", "speech_rect", "speech_cloud"}:
        data["tail_x"] = _clamp(
            _float(params.get("tail_x"), 0.55),
            -_BALLOON_TAIL_RANGE,
            _BALLOON_TAIL_RANGE,
        )
        data["tail_y"] = _clamp(
            _float(params.get("tail_y"), 0.65),
            -_BALLOON_TAIL_RANGE,
            _BALLOON_TAIL_RANGE,
        )
        data["tail_width"] = _clamp(_float(params.get("tail_width"), 0.16), 0.06, 0.35)
        if shape_id == "speech_rect":
            data["radius"] = _clamp(_float(params.get("radius"), 0.12), 0.02, 0.45)
        if shape_id == "speech_cloud":
            data["lobes"] = _int(params.get("lobes"), 7, min_value=5, max_value=20)
    elif shape_id == "spiky_balloon":
        data["spike_count"] = _int(params.get("spike_count"), 20, min_value=6, max_value=60)
        data["spike_depth"] = _clamp(_float(params.get("spike_depth"), 0.28), 0.1, 0.8)
    elif shape_id.startswith("polygon_"):
        data["sides"] = _int(params.get("sides"), 5, min_value=3, max_value=10)
    return data


def _update_params_from_handle(  # noqa: PLR0911
    shape_id: str,
    params: dict[str, float | int],
    handle_id: str,
    point: tuple[float, float],
    bbox: tuple[float, float, float, float],
) -> dict[str, float | int] | None:
    min_x, min_y, max_x, max_y = bbox
    width = max(1.0, max_x - min_x)
    height = max(1.0, max_y - min_y)
    cx = (min_x + max_x) * 0.5
    cy = (min_y + max_y) * 0.5
    px, py = point

    updated = dict(params)
    if shape_id == "rounded_rect" and handle_id == "radius":
        updated["radius"] = _clamp((px - min_x) / width, 0.02, 0.49)
        return _normalize_params(shape_id, updated)
    if shape_id in {"speech_round", "speech_rect", "speech_cloud"} and handle_id == "tail_tip":
        updated["tail_x"] = _clamp(
            (px - cx) / (width * 0.5),
            -_BALLOON_TAIL_RANGE,
            _BALLOON_TAIL_RANGE,
        )
        updated["tail_y"] = _clamp(
            (py - cy) / (height * 0.5),
            -_BALLOON_TAIL_RANGE,
            _BALLOON_TAIL_RANGE,
        )
        return _normalize_params(shape_id, updated)
    if shape_id == "spiky_balloon" and handle_id == "spike_depth":
        updated["spike_depth"] = _clamp((py - min_y) / height, 0.1, 0.8)
        return _normalize_params(shape_id, updated)
    if shape_id == "parallelogram" and handle_id == "skew":
        updated["skew"] = _clamp((px - cx) / (width * 0.5), -0.8, 0.8)
        return _normalize_params(shape_id, updated)
    if shape_id == "trapezoid" and handle_id == "top_inset":
        updated["top_inset"] = _clamp((px - min_x) / width, 0.0, 0.45)
        return _normalize_params(shape_id, updated)
    if shape_id in _FOCUS_LINES_IDS:
        if handle_id == "vanishing_point":
            updated["vp_x"] = _clamp((px - cx) / (width * 0.5), -0.8, 0.8)
            updated["vp_y"] = _clamp((py - cy) / (height * 0.5), -0.8, 0.8)
            return _normalize_params(shape_id, updated)
        # inner_ratio is relative to vanishing point
        vp_x = _float(updated.get("vp_x"), 0.0)
        vp_y = _float(updated.get("vp_y"), 0.0)
        abs_vp_x = cx + vp_x * width * 0.5
        abs_vp_y = cy + vp_y * height * 0.5
        if handle_id == "inner_ratio":
            dist = math.hypot(px - abs_vp_x, py - abs_vp_y)
            updated["inner_ratio"] = _clamp(dist / (width * 0.5), 0.05, 0.8)
            return _normalize_params(shape_id, updated)
        if handle_id == "line_count":
            ratio = _clamp((px - min_x) / width, 0.0, 1.0)
            updated["line_count"] = int(8 + ratio * (200 - 8))
            return _normalize_params(shape_id, updated)
        if handle_id == "outer_width":
            updated["outer_width"] = _clamp((cy - py) / (height * 0.5), 0.0, 1.0)
            return _normalize_params(shape_id, updated)
        if handle_id == "inner_width":
            updated["inner_width"] = _clamp((cy - py) / (height * 0.5), 0.0, 1.0)
            return _normalize_params(shape_id, updated)
        if handle_id == "length_jitter":
            updated["length_jitter"] = _clamp((px - min_x) / width, 0.0, 1.0)
            return _normalize_params(shape_id, updated)
    return None


def _build_paths(  # noqa: PLR0911
    shape_id: str,
    bbox: tuple[float, float, float, float],
    params: dict[str, float | int],
) -> list[VectorPath]:
    if shape_id == "ellipse":
        return [_ellipse_path(bbox)]
    if shape_id.startswith("polygon_"):
        sides = _int(params.get("sides"), 5, min_value=3, max_value=10)
        return [_polygon_path(_regular_polygon_points(bbox, sides))]
    if shape_id == "rounded_rect":
        radius_ratio = _float(params.get("radius"), 0.2)
        return [_rounded_rect_path(bbox, radius_ratio)]
    if shape_id == "parallelogram":
        skew = _float(params.get("skew"), 0.2)
        return [_polygon_path(_parallelogram_points(bbox, skew))]
    if shape_id == "trapezoid":
        inset = _float(params.get("top_inset"), 0.2)
        return [_polygon_path(_trapezoid_points(bbox, inset))]
    if shape_id in _FOCUS_LINES_IDS:
        count = _int(params.get("line_count"), 90, min_value=8, max_value=200)
        inner_ratio = _float(params.get("inner_ratio"), 0.35)
        outer_width = _float(params.get("outer_width"), 0.2)
        inner_width = _float(params.get("inner_width"), 0.0)
        vp_x = _float(params.get("vp_x"), 0.0)
        vp_y = _float(params.get("vp_y"), 0.0)
        rect_outer = shape_id == "focus_lines_rect"
        return _focus_line_paths(
            bbox,
            count,
            inner_ratio,
            outer_width,
            inner_width,
            vp_x=vp_x,
            vp_y=vp_y,
            rect_outer=rect_outer,
            length_jitter=_float(params.get("length_jitter"), 0.6),
        )
    if shape_id == "speech_round":
        points = _ellipse_points(bbox, count=36)
        tip = _tail_tip_from_params(bbox, params)
        width_ratio = _float(params.get("tail_width"), 0.16)
        return [_polygon_path(_compose_balloon_with_tail(points, tip, width_ratio))]
    if shape_id == "speech_rect":
        radius = _float(params.get("radius"), 0.12)
        points = _rounded_rect_points(bbox, radius_ratio=radius, corner_steps=5)
        tip = _tail_tip_from_params(bbox, params)
        width_ratio = _float(params.get("tail_width"), 0.16)
        return [_polygon_path(_compose_balloon_with_tail(points, tip, width_ratio))]
    if shape_id == "speech_cloud":
        lobes = _int(params.get("lobes"), 7, min_value=5, max_value=20)
        body = _cloud_body_path(bbox, lobes=lobes)
        tip = _tail_tip_from_params(bbox, params)
        anchors_xy = [(float(a.pos[0]), float(a.pos[1])) for a in body.anchors]
        bubbles = _bubble_tail_paths(bbox, tip, body_anchors=anchors_xy)
        return [body, *bubbles]
    if shape_id == "spiky_balloon":
        spike_count = _int(params.get("spike_count"), 20, min_value=6, max_value=60)
        spike_depth = _float(params.get("spike_depth"), 0.28)
        return [_polygon_path(_spiky_points(bbox, spike_count=spike_count, depth=spike_depth))]
    return []


def _ellipse_path(bbox: tuple[float, float, float, float]) -> VectorPath:
    min_x, min_y, max_x, max_y = bbox
    cx = (min_x + max_x) * 0.5
    cy = (min_y + max_y) * 0.5
    rx = max(1.0, (max_x - min_x) * 0.5)
    ry = max(1.0, (max_y - min_y) * 0.5)
    k = 0.5522847498
    anchors = [
        Anchor(pos=(cx + rx, cy), kind="smooth", h_in=(0.0, -k * ry), h_out=(0.0, k * ry)),
        Anchor(pos=(cx, cy + ry), kind="smooth", h_in=(k * rx, 0.0), h_out=(-k * rx, 0.0)),
        Anchor(pos=(cx - rx, cy), kind="smooth", h_in=(0.0, k * ry), h_out=(0.0, -k * ry)),
        Anchor(pos=(cx, cy - ry), kind="smooth", h_in=(-k * rx, 0.0), h_out=(k * rx, 0.0)),
    ]
    return VectorPath(anchors=anchors, closed=True)


def _rounded_rect_path(bbox: tuple[float, float, float, float], radius_ratio: float) -> VectorPath:
    min_x, min_y, max_x, max_y = bbox
    width = max(1.0, max_x - min_x)
    height = max(1.0, max_y - min_y)
    radius = max(1.0, min(width, height) * _clamp(radius_ratio, 0.02, 0.49))
    k = 0.5522847498
    d = k * radius
    anchors = [
        Anchor(pos=(min_x + radius, min_y), kind="corner", h_in=(-d, 0.0), h_out=None),
        Anchor(pos=(max_x - radius, min_y), kind="corner", h_in=None, h_out=(d, 0.0)),
        Anchor(pos=(max_x, min_y + radius), kind="corner", h_in=(0.0, -d), h_out=None),
        Anchor(pos=(max_x, max_y - radius), kind="corner", h_in=None, h_out=(0.0, d)),
        Anchor(pos=(max_x - radius, max_y), kind="corner", h_in=(d, 0.0), h_out=None),
        Anchor(pos=(min_x + radius, max_y), kind="corner", h_in=None, h_out=(-d, 0.0)),
        Anchor(pos=(min_x, max_y - radius), kind="corner", h_in=(0.0, d), h_out=None),
        Anchor(pos=(min_x, min_y + radius), kind="corner", h_in=None, h_out=(0.0, -d)),
    ]
    return VectorPath(anchors=anchors, closed=True)


def _polygon_path(points: list[tuple[float, float]]) -> VectorPath:
    anchors = [Anchor(pos=(float(x), float(y)), kind="corner") for x, y in points]
    return VectorPath(anchors=anchors, closed=True)


def _regular_polygon_points(
    bbox: tuple[float, float, float, float],
    sides: int,
) -> list[tuple[float, float]]:
    min_x, min_y, max_x, max_y = bbox
    cx = (min_x + max_x) * 0.5
    cy = (min_y + max_y) * 0.5
    rx = max(1.0, (max_x - min_x) * 0.5)
    ry = max(1.0, (max_y - min_y) * 0.5)
    points: list[tuple[float, float]] = []
    for idx in range(sides):
        angle = -math.pi * 0.5 + (2.0 * math.pi * idx / sides)
        points.append((cx + math.cos(angle) * rx, cy + math.sin(angle) * ry))
    return points


def _parallelogram_points(
    bbox: tuple[float, float, float, float],
    skew_ratio: float,
) -> list[tuple[float, float]]:
    min_x, min_y, max_x, max_y = bbox
    width = max(1.0, max_x - min_x)
    skew = _clamp(skew_ratio, -0.8, 0.8) * width * 0.35
    return [
        (min_x + skew, min_y),
        (max_x + skew, min_y),
        (max_x - skew, max_y),
        (min_x - skew, max_y),
    ]


def _trapezoid_points(
    bbox: tuple[float, float, float, float],
    top_inset: float,
) -> list[tuple[float, float]]:
    min_x, min_y, max_x, max_y = bbox
    width = max(1.0, max_x - min_x)
    inset = _clamp(top_inset, 0.0, 0.45) * width
    return [
        (min_x + inset, min_y),
        (max_x - inset, min_y),
        (max_x, max_y),
        (min_x, max_y),
    ]


def _focus_line_outer_point_circle(
    cx: float,
    cy: float,
    rx: float,
    ry: float,
    origin: tuple[float, float],
    angle: float,
) -> tuple[float, float] | None:
    """Outer endpoint where a ray from *origin* leaves the ellipse."""
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    ox = (origin[0] - cx) / rx
    oy = (origin[1] - cy) / ry
    dx = cos_a / rx
    dy = sin_a / ry
    a = dx * dx + dy * dy
    b = 2.0 * (ox * dx + oy * dy)
    c = ox * ox + oy * oy - 1.0
    disc = b * b - 4.0 * a * c
    if a <= 1e-12 or disc < 0.0:
        return None
    t = (-b + math.sqrt(disc)) / (2.0 * a)
    if t <= 1e-6:
        return None
    return (origin[0] + cos_a * t, origin[1] + sin_a * t)


def _focus_line_outer_point_rect(
    min_x: float,
    min_y: float,
    max_x: float,
    max_y: float,
    cx: float,
    cy: float,
    angle: float,
) -> tuple[float, float]:
    """Outer endpoint on the bbox rectangle edge (ray-rect intersection)."""
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    # Find t for intersection with each side
    t_min = float("inf")
    if abs(cos_a) > 1e-9:
        t_right = (max_x - cx) / cos_a
        if t_right > 1e-6:
            t_min = min(t_min, t_right)
        t_left = (min_x - cx) / cos_a
        if t_left > 1e-6:
            t_min = min(t_min, t_left)
    if abs(sin_a) > 1e-9:
        t_bottom = (max_y - cy) / sin_a
        if t_bottom > 1e-6:
            t_min = min(t_min, t_bottom)
        t_top = (min_y - cy) / sin_a
        if t_top > 1e-6:
            t_min = min(t_min, t_top)
    if t_min == float("inf"):
        t_min = 1.0
    return (cx + cos_a * t_min, cy + sin_a * t_min)


def _focus_line_random(idx: int, salt: int) -> float:
    """Deterministic per-line pseudo-random value in [0, 1)."""
    x = (idx * 374761393 + salt * 668265263) & 0xFFFFFFFF
    x = ((x ^ (x >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((x ^ (x >> 16)) & 0xFFFFFF) / float(0x1000000)


def _focus_line_paths(
    bbox: tuple[float, float, float, float],
    line_count: int,
    inner_ratio: float,
    outer_width: float = 0.0,
    inner_width: float = 0.0,
    *,
    vp_x: float = 0.0,
    vp_y: float = 0.0,
    rect_outer: bool = False,
    length_jitter: float = 0.0,
) -> list[VectorPath]:
    """Manga-style focus lines: wedges radiating from the vanishing point.

    Each line is thick at the outer edge and tapers toward the vanishing
    point.  Per-line angle, width and start distance are jittered with a
    deterministic hash so rebuilding the shape is stable.
    """
    min_x, min_y, max_x, max_y = bbox
    bbox_cx = (min_x + max_x) * 0.5
    bbox_cy = (min_y + max_y) * 0.5
    w = max(2.0, max_x - min_x)
    h = max(2.0, max_y - min_y)
    # Vanishing point (center of convergence)
    vpx = bbox_cx + vp_x * w * 0.5
    vpy = bbox_cy + vp_y * h * 0.5
    rx = w * 0.5
    ry = h * 0.5
    inner = _clamp(inner_ratio, 0.05, 0.8)
    jitter_amount = _clamp(length_jitter, 0.0, 1.0)
    n = max(8, line_count)
    avg_r = (rx + ry) * 0.5
    max_outer_w = avg_r * 0.15 * outer_width
    max_inner_w = avg_r * 0.15 * inner_width
    paths: list[VectorPath] = []
    for idx in range(n):
        angle_jitter = (_focus_line_random(idx, 1) - 0.5) * (2.0 * math.pi / n) * 0.8
        angle = 2.0 * math.pi * idx / n + angle_jitter
        cos_a = math.cos(angle)
        sin_a = math.sin(angle)
        # Outer point: where the ray from the vanishing point leaves the frame
        if rect_outer:
            outer = _focus_line_outer_point_rect(
                min_x,
                min_y,
                max_x,
                max_y,
                vpx,
                vpy,
                angle,
            )
        else:
            outer = _focus_line_outer_point_circle(bbox_cx, bbox_cy, rx, ry, (vpx, vpy), angle)
        if outer is None:
            continue
        x1, y1 = outer
        outer_dist = math.hypot(x1 - vpx, y1 - vpy)
        # Inner start: on the inner ellipse around the vanishing point,
        # pushed outward per line so the lines have uneven lengths.
        base_dist = math.hypot(cos_a * rx * inner, sin_a * ry * inner)
        reach = jitter_amount * 0.6 * _focus_line_random(idx, 2) ** 1.5
        inner_dist = base_dist + max(0.0, outer_dist - base_dist) * reach
        if outer_dist - inner_dist < 1.0:
            continue
        x0 = vpx + cos_a * inner_dist
        y0 = vpy + sin_a * inner_dist
        width_factor = 0.3 + 0.7 * _focus_line_random(idx, 3)
        ow = max_outer_w * width_factor
        iw = max_inner_w * width_factor
        if ow < 0.05 and iw < 0.05:
            continue
        # Normal perpendicular to the line direction
        nx = -sin_a
        ny = cos_a
        anchors = [
            Anchor(pos=(x1 - nx * ow, y1 - ny * ow), kind="corner"),
            Anchor(pos=(x1 + nx * ow, y1 + ny * ow), kind="corner"),
        ]
        if iw < 0.05:
            anchors.append(Anchor(pos=(x0, y0), kind="corner"))
        else:
            anchors.append(Anchor(pos=(x0 + nx * iw, y0 + ny * iw), kind="corner"))
            anchors.append(Anchor(pos=(x0 - nx * iw, y0 - ny * iw), kind="corner"))
        paths.append(VectorPath(anchors=anchors, closed=True))
    return paths


def _tail_tip_from_params(
    bbox: tuple[float, float, float, float],
    params: dict[str, float | int],
) -> tuple[float, float]:
    min_x, min_y, max_x, max_y = bbox
    cx = (min_x + max_x) * 0.5
    cy = (min_y + max_y) * 0.5
    width = max(1.0, max_x - min_x)
    height = max(1.0, max_y - min_y)
    tail_x = _float(params.get("tail_x"), 0.55)
    tail_y = _float(params.get("tail_y"), 0.65)
    return (cx + tail_x * width * 0.5, cy + tail_y * height * 0.5)


def _ellipse_points(
    bbox: tuple[float, float, float, float],
    *,
    count: int,
) -> list[tuple[float, float]]:
    min_x, min_y, max_x, max_y = bbox
    cx = (min_x + max_x) * 0.5
    cy = (min_y + max_y) * 0.5
    rx = max(1.0, (max_x - min_x) * 0.5)
    ry = max(1.0, (max_y - min_y) * 0.5)
    points: list[tuple[float, float]] = []
    for idx in range(max(8, count)):
        angle = -math.pi * 0.5 + 2.0 * math.pi * idx / max(8, count)
        points.append((cx + math.cos(angle) * rx, cy + math.sin(angle) * ry))
    return points


def _rounded_rect_points(
    bbox: tuple[float, float, float, float],
    *,
    radius_ratio: float,
    corner_steps: int,
) -> list[tuple[float, float]]:
    min_x, min_y, max_x, max_y = bbox
    width = max(1.0, max_x - min_x)
    height = max(1.0, max_y - min_y)
    radius = max(1.0, min(width, height) * _clamp(radius_ratio, 0.02, 0.49))
    steps = max(2, corner_steps)
    points: list[tuple[float, float]] = []
    centers = [
        (max_x - radius, min_y + radius, -math.pi * 0.5, 0.0),
        (max_x - radius, max_y - radius, 0.0, math.pi * 0.5),
        (min_x + radius, max_y - radius, math.pi * 0.5, math.pi),
        (min_x + radius, min_y + radius, math.pi, math.pi * 1.5),
    ]
    for cx, cy, start, end in centers:
        for step in range(steps):
            t = step / max(1, steps - 1)
            angle = start + (end - start) * t
            points.append((cx + math.cos(angle) * radius, cy + math.sin(angle) * radius))
    return points


def _cloud_body_path(
    bbox: tuple[float, float, float, float],
    *,
    lobes: int,
) -> VectorPath:
    """Generate a cloud outline using SVG-derived template or algorithmic fallback."""
    min_x, min_y, max_x, max_y = bbox
    cx = (min_x + max_x) * 0.5
    cy = (min_y + max_y) * 0.5
    rx = max(1.0, (max_x - min_x) * 0.5)
    ry = max(1.0, (max_y - min_y) * 0.5)

    if lobes == 7:
        return _cloud_body_from_template(cx, cy, rx, ry)
    return _cloud_body_algorithmic(cx, cy, rx, ry, lobes)


def _cloud_body_from_template(
    cx: float,
    cy: float,
    rx: float,
    ry: float,
) -> VectorPath:
    """Build cloud body from the SVG-derived 7-anchor cubic-bezier template."""
    anchors: list[Anchor] = []
    for px_n, py_n, hi_x, hi_y, ho_x, ho_y in _CLOUD_SVG_ANCHORS:
        anchors.append(
            Anchor(
                pos=(cx + px_n * rx, cy + py_n * ry),
                kind="smooth",
                h_in=(hi_x * rx, hi_y * ry),
                h_out=(ho_x * rx, ho_y * ry),
            )
        )
    return VectorPath(anchors=anchors, closed=True)


def _cloud_body_algorithmic(
    cx: float,
    cy: float,
    rx: float,
    ry: float,
    lobes: int,
) -> VectorPath:
    """Algorithmic cloud body for non-default lobe counts."""
    inner = 0.65
    bump_r = 0.42

    bumps: list[tuple[float, float, float]] = []
    for i in range(lobes):
        theta = -math.pi * 0.5 + 2.0 * math.pi * i / lobes
        var = 1.0 + 0.08 * math.sin(i * 2.39 + 0.5) + 0.05 * math.cos(i * 3.7)
        bumps.append((inner * math.cos(theta), inner * math.sin(theta), bump_r * var))

    avg_r = (rx + ry) * 0.5
    anchors: list[Anchor] = []
    for i in range(lobes):
        nbx, nby, nbr = bumps[i]
        nnx, nny, nnr = bumps[(i + 1) % lobes]

        vn = _cloud_valley(nbx, nby, nbr, nnx, nny, nnr, 0.0, 0.0)
        out_a = math.atan2(nby, nbx)
        pn = (nbx + nbr * math.cos(out_a), nby + nbr * math.sin(out_a))

        valley = (cx + vn[0] * rx, cy + vn[1] * ry)
        peak = (cx + pn[0] * rx, cy + pn[1] * ry)

        v_a = math.atan2(vn[1], vn[0])
        tvx, tvy = -math.sin(v_a) * rx, math.cos(v_a) * ry
        tv_len = math.hypot(tvx, tvy)
        if tv_len > 0.001:
            tvx /= tv_len
            tvy /= tv_len
        tpx, tpy = -math.sin(out_a) * rx, math.cos(out_a) * ry
        tp_len = math.hypot(tpx, tpy)
        if tp_len > 0.001:
            tpx /= tp_len
            tpy /= tp_len

        vh = nbr * avg_r * 0.40
        ph = nbr * avg_r * 0.70

        anchors.append(
            Anchor(
                pos=valley,
                kind="smooth",
                h_in=(-tvx * vh, -tvy * vh),
                h_out=(tvx * vh, tvy * vh),
            )
        )
        anchors.append(
            Anchor(
                pos=peak,
                kind="smooth",
                h_in=(-tpx * ph, -tpy * ph),
                h_out=(tpx * ph, tpy * ph),
            )
        )
    return VectorPath(anchors=anchors, closed=True)


def _cloud_valley(
    x1: float,
    y1: float,
    r1: float,
    x2: float,
    y2: float,
    r2: float,
    cx: float,
    cy: float,
) -> tuple[float, float]:
    """Outer intersection of two bump circles (valley on the visible boundary)."""
    dx = x2 - x1
    dy = y2 - y1
    d = math.hypot(dx, dy)

    if d < 0.001:
        return ((x1 + x2) * 0.5, (y1 + y2) * 0.5)

    base = math.atan2(dy, dx)

    if d >= r1 + r2:
        # No overlap — proportional midpoint pushed outward
        t = r1 / max(0.001, r1 + r2)
        mx = x1 + dx * t
        my = y1 + dy * t
        out_a = math.atan2(my - cy, mx - cx)
        push = min(r1, r2) * 0.5
        return (mx + math.cos(out_a) * push, my + math.sin(out_a) * push)

    # Circle-circle intersection
    cos_a = (d * d + r1 * r1 - r2 * r2) / (2.0 * d * r1)
    cos_a = max(-1.0, min(1.0, cos_a))
    alpha = math.acos(cos_a)

    p1 = (x1 + r1 * math.cos(base + alpha), y1 + r1 * math.sin(base + alpha))
    p2 = (x1 + r1 * math.cos(base - alpha), y1 + r1 * math.sin(base - alpha))

    # Return the one FARTHER from center (outer valley on visible boundary)
    d1_sq = (p1[0] - cx) ** 2 + (p1[1] - cy) ** 2
    d2_sq = (p2[0] - cx) ** 2 + (p2[1] - cy) ** 2
    return p1 if d1_sq >= d2_sq else p2


def _bubble_tail_paths(
    bbox: tuple[float, float, float, float],
    tip: tuple[float, float],
    *,
    body_anchors: list[tuple[float, float]] | None = None,
) -> list[VectorPath]:
    """Generate 3 decreasing circles from body toward tail tip."""
    min_x, min_y, max_x, max_y = bbox
    cx = (min_x + max_x) * 0.5
    cy = (min_y + max_y) * 0.5
    body_rx = max(1.0, (max_x - min_x) * 0.5)
    body_ry = max(1.0, (max_y - min_y) * 0.5)
    body_r = min(body_rx, body_ry)
    dx = tip[0] - cx
    dy = tip[1] - cy
    dist = math.hypot(dx, dy)
    if dist < 1.0:
        return []
    nx = dx / dist
    ny = dy / dist
    # Estimate body edge distance along the tail direction
    if body_anchors:
        max_proj = 0.0
        for ax, ay in body_anchors:
            proj = (ax - cx) * nx + (ay - cy) * ny
            max_proj = max(max_proj, proj)
        edge_dist = max_proj
    else:
        edge_dist = body_r * 0.88
    # Available span from body edge to tip
    span = max(1.0, dist - edge_dist)
    # Bubble radii scale with span; clamp max to body_r * 0.22
    r1 = min(span * 0.12, body_r * 0.22)
    r2 = r1 * 0.65
    r3 = r2 * 0.60
    # Place bubbles at 25%, 55%, 80% of span (measured from body edge)
    d1 = edge_dist + span * 0.25
    d2 = edge_dist + span * 0.55
    d3 = edge_dist + span * 0.80
    r1 = max(r1, 1.0)
    r2 = max(r2, 1.0)
    r3 = max(r3, 1.0)
    return [
        _circle_path(cx + nx * d1, cy + ny * d1, r1),
        _circle_path(cx + nx * d2, cy + ny * d2, r2),
        _circle_path(cx + nx * d3, cy + ny * d3, r3),
    ]


def _circle_path(cx: float, cy: float, r: float) -> VectorPath:
    """Generate a circle as a closed bezier path with 4 anchors."""
    k = 0.5522847498
    return VectorPath(
        anchors=[
            Anchor(pos=(cx + r, cy), kind="smooth", h_in=(0.0, -k * r), h_out=(0.0, k * r)),
            Anchor(pos=(cx, cy + r), kind="smooth", h_in=(k * r, 0.0), h_out=(-k * r, 0.0)),
            Anchor(pos=(cx - r, cy), kind="smooth", h_in=(0.0, k * r), h_out=(0.0, -k * r)),
            Anchor(pos=(cx, cy - r), kind="smooth", h_in=(-k * r, 0.0), h_out=(k * r, 0.0)),
        ],
        closed=True,
    )


def _spiky_points(
    bbox: tuple[float, float, float, float],
    *,
    spike_count: int,
    depth: float,
) -> list[tuple[float, float]]:
    min_x, min_y, max_x, max_y = bbox
    cx = (min_x + max_x) * 0.5
    cy = (min_y + max_y) * 0.5
    rx = max(1.0, (max_x - min_x) * 0.5)
    ry = max(1.0, (max_y - min_y) * 0.5)
    inner_scale = max(0.15, 1.0 - _clamp(depth, 0.1, 0.8))
    points: list[tuple[float, float]] = []
    count = max(6, spike_count)
    for idx in range(count * 2):
        angle = -math.pi * 0.5 + (math.pi * idx / count)
        scale = 1.0 if idx % 2 == 0 else inner_scale
        points.append((cx + math.cos(angle) * rx * scale, cy + math.sin(angle) * ry * scale))
    return points


def _compose_balloon_with_tail(
    points: list[tuple[float, float]],
    tail_tip: tuple[float, float],
    width_ratio: float,
) -> list[tuple[float, float]]:
    if len(points) < 8:
        return [*points, tail_tip]
    cx = sum(point[0] for point in points) / len(points)
    cy = sum(point[1] for point in points) / len(points)
    angle = math.atan2(tail_tip[1] - cy, tail_tip[0] - cx)
    spread = _clamp(width_ratio * 1.1, 0.08, 0.45)
    left_hit = _ray_hit_on_polygon(points, (cx, cy), angle - spread)
    right_hit = _ray_hit_on_polygon(points, (cx, cy), angle + spread)
    if left_hit is None or right_hit is None:
        return [*points, tail_tip]

    expanded, left_idx, right_idx = _expand_polygon_with_hits(points, left_hit, right_hit)
    if left_idx is None or right_idx is None or left_idx == right_idx:
        return [*points, tail_tip]
    size = len(expanded)
    if size < 3:
        return [*points, tail_tip]
    arc_lr = _cyclic_slice(expanded, left_idx, right_idx)
    arc_rl = _cyclic_slice(expanded, right_idx, left_idx)
    body_ring = arc_lr if _extent_score(arc_lr) >= _extent_score(arc_rl) else arc_rl
    if len(body_ring) < 3:
        return [*points, tail_tip]
    return [*body_ring, tail_tip]


def _ray_hit_on_polygon(
    points: list[tuple[float, float]],
    origin: tuple[float, float],
    angle: float,
) -> tuple[int, float, tuple[float, float]] | None:
    direction = (math.cos(angle), math.sin(angle))
    best_t = float("inf")
    best_hit: tuple[int, float, tuple[float, float]] | None = None
    count = len(points)
    for idx in range(count):
        a = points[idx]
        b = points[(idx + 1) % count]
        hit = _ray_segment_intersection(origin, direction, a, b)
        if hit is None:
            continue
        t, u = hit
        if t < best_t:
            best_t = t
            px = origin[0] + direction[0] * t
            py = origin[1] + direction[1] * t
            best_hit = (idx, u, (px, py))
    return best_hit


def _ray_segment_intersection(
    origin: tuple[float, float],
    direction: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
) -> tuple[float, float] | None:
    rx, ry = direction
    sx = b[0] - a[0]
    sy = b[1] - a[1]
    denom = rx * sy - ry * sx
    if abs(denom) <= 1e-9:
        return None
    qpx = a[0] - origin[0]
    qpy = a[1] - origin[1]
    t = (qpx * sy - qpy * sx) / denom
    u = (qpx * ry - qpy * rx) / denom
    if t <= 1e-6:
        return None
    if u < -1e-6 or u > 1.0 + 1e-6:
        return None
    return (t, _clamp(u, 0.0, 1.0))


def _expand_polygon_with_hits(
    points: list[tuple[float, float]],
    left_hit: tuple[int, float, tuple[float, float]],
    right_hit: tuple[int, float, tuple[float, float]],
) -> tuple[list[tuple[float, float]], int | None, int | None]:
    inserts: dict[int, list[tuple[str, float, tuple[float, float]]]] = {}
    for name, hit in (("left", left_hit), ("right", right_hit)):
        seg_idx, t, point = hit
        inserts.setdefault(seg_idx, []).append((name, t, point))
    for entries in inserts.values():
        entries.sort(key=lambda item: item[1])

    expanded: list[tuple[float, float]] = []
    left_idx: int | None = None
    right_idx: int | None = None
    for seg_idx, point in enumerate(points):
        _append_unique(expanded, point)
        for name, _t, inserted in inserts.get(seg_idx, []):
            idx = _append_unique(expanded, inserted)
            if name == "left":
                left_idx = idx
            else:
                right_idx = idx
    return (expanded, left_idx, right_idx)


def _append_unique(points: list[tuple[float, float]], point: tuple[float, float]) -> int:
    if points and _points_close(points[-1], point):
        return len(points) - 1
    points.append(point)
    return len(points) - 1


def _points_close(a: tuple[float, float], b: tuple[float, float], eps: float = 1e-6) -> bool:
    return abs(a[0] - b[0]) <= eps and abs(a[1] - b[1]) <= eps


def _cyclic_distance(size: int, start_idx: int, end_idx: int) -> int:
    return (end_idx - start_idx) % size


def _cyclic_slice(
    points: list[tuple[float, float]],
    start_idx: int,
    end_idx: int,
) -> list[tuple[float, float]]:
    result = [points[start_idx]]
    idx = start_idx
    guard = 0
    while idx != end_idx and guard <= len(points):
        idx = (idx + 1) % len(points)
        result.append(points[idx])
        guard += 1
    return result


def _extent_score(points: list[tuple[float, float]]) -> float:
    if len(points) < 3:
        return 0.0
    min_x = min(point[0] for point in points)
    min_y = min(point[1] for point in points)
    max_x = max(point[0] for point in points)
    max_y = max(point[1] for point in points)
    return (max_x - min_x) * (max_y - min_y)


def _compute_bbox(obj: object) -> tuple[float, float, float, float] | None:
    from ..shape_manager.ops.bbox import compute_bbox  # noqa: PLC0415

    paths = getattr(obj, "paths", None) or []
    if not paths:
        return None
    return compute_bbox(paths)


def _speech_body_bbox(obj: object) -> tuple[float, float, float, float] | None:
    paths = getattr(obj, "paths", None) or []
    if not paths:
        return None
    anchors = list(getattr(paths[0], "anchors", None) or [])
    if len(anchors) < 5:
        return _compute_bbox(obj)
    # Multi-path balloon (cloud): paths[0] is body only, no tail to strip
    if len(paths) > 1 and all(getattr(a, "kind", "") == "smooth" for a in anchors):
        points = [(float(a.pos[0]), float(a.pos[1])) for a in anchors]
    else:
        points = [(float(a.pos[0]), float(a.pos[1])) for a in anchors[:-1]]
    if len(points) < 4:
        return _compute_bbox(obj)
    min_x = min(point[0] for point in points)
    min_y = min(point[1] for point in points)
    max_x = max(point[0] for point in points)
    max_y = max(point[1] for point in points)
    return (min_x, min_y, max_x, max_y)


def _float(value: object, default: float) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _int(value: object, default: int, *, min_value: int, max_value: int) -> int:
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(max_value, parsed))


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _angle_delta(a: float, b: float) -> float:
    diff = (a - b + math.pi) % (2.0 * math.pi) - math.pi
    return diff


# ---------------------------------------------------------------------------
# SVG export utilities
# ---------------------------------------------------------------------------


def vector_object_to_svg(obj: VectorObject) -> str:
    """Export a VectorObject to a standalone SVG string."""
    bbox = _compute_bbox(obj)
    if bbox is None:
        return "<svg/>"
    min_x, min_y, max_x, max_y = bbox
    payload = _svg_text_payload(obj)
    if payload is not None and has_typography(payload):
        from loomstroke.core.japanese_text.typography_painter import (  # noqa: PLC0415
            japanese_ink_bounds,
        )

        ink = japanese_ink_bounds(payload)
        min_x, min_y = min(min_x, ink.left()), min(min_y, ink.top())
        max_x, max_y = max(max_x, ink.right()), max(max_y, ink.bottom())
    pad = max(_safe_svg_stroke_width(obj.style.stroke_width) * 2, 4.0)
    min_x -= pad
    min_y -= pad
    max_x += pad
    max_y += pad
    w = max(1.0, max_x - min_x)
    h = max(1.0, max_y - min_y)
    vb = f"{min_x:.2f} {min_y:.2f} {w:.2f} {h:.2f}"

    body = "\n".join(_svg_elements_for_object(obj))
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="{vb}" width="{w:.0f}" height="{h:.0f}">\n'
        f"{body}\n</svg>\n"
    )


def _svg_elements_for_object(obj: VectorObject) -> list[str]:
    """Return SVG elements for *obj* (no ``<svg>`` wrapper)."""
    text_elements = _svg_text_elements_for_object(obj)
    if text_elements is not None:
        object_id = _safe_svg_object_id(obj.id)
        if object_id and text_elements:
            return [f'  <g id="{object_id}">', *[f"    {line}" for line in text_elements], "  </g>"]
        return [f"  {line}" for line in text_elements]

    stroke = _rgb_hex(obj.style.stroke_color)
    fill = "none"
    if obj.style.fill_enabled and obj.style.fill_color is not None:
        fill = _rgb_hex(obj.style.fill_color)
    sw = f"{_safe_svg_stroke_width(obj.style.stroke_width):.2f}"
    cap = _safe_svg_line_cap(obj.style.cap)
    join = _safe_svg_line_join(obj.style.join)
    attrs = (
        f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}" '
        f'stroke-linejoin="{join}" stroke-linecap="{cap}"'
    )
    if obj.style.dash:
        safe_dash = tuple(v for v in obj.style.dash if math.isfinite(v) and v > 0.0)
        if safe_dash:
            dash_str = ",".join(f"{v:.2f}" for v in safe_dash)
            attrs += f' stroke-dasharray="{dash_str}"'
    path_elements: list[str] = []
    for path in obj.paths:
        d = _svg_path_d(path)
        if d:
            path_elements.append(f'<path d="{d}" {attrs} />')
    object_id = _safe_svg_object_id(obj.id)
    if object_id and path_elements:
        return [f'  <g id="{object_id}">', *[f"    {line}" for line in path_elements], "  </g>"]
    return [f"  {line}" for line in path_elements]


def _svg_text_elements_for_object(obj: VectorObject) -> list[str] | None:
    payload = _svg_text_payload(obj)
    if payload is None:
        return None
    if has_typography(payload):
        from loomstroke.core.japanese_text.typography_svg import (  # noqa: PLC0415
            japanese_svg_elements,
        )

        return japanese_svg_elements(payload)
    content = _svg_text_content(payload)
    if not content:
        return []
    x, y, width, height = _svg_text_frame(payload, obj)
    font_size = _safe_svg_font_size(_svg_payload_float(payload, "font_size_pt", 32.0))
    baseline_y = y + font_size
    color = _svg_text_color(payload, obj)
    font_family = _safe_svg_font_family(_svg_text_font_family(payload))
    attrs = (
        f'x="{x:.2f}" y="{baseline_y:.2f}" '
        f'font-family="{_svg_attr(font_family)}" font-size="{font_size:.2f}" '
        f'fill="{_rgb_hex(color[:3])}"'
    )
    if color[3] < 255:
        attrs += f' fill-opacity="{color[3] / 255.0:.3f}"'
    if _svg_text_is_vertical(payload):
        attrs += ' writing-mode="vertical-rl"'
    rotation = _svg_text_rotation(payload)
    if abs(rotation) > 1e-6:
        cx = x + (width * 0.5)
        cy = y + (height * 0.5)
        attrs += f' transform="rotate({rotation:.2f} {cx:.2f} {cy:.2f})"'
    lines = content.splitlines() or [content]
    if len(lines) == 1:
        return [f"<text {attrs}>{_svg_text(lines[0])}</text>"]
    tspans = [f"<text {attrs}>"]
    for index, line in enumerate(lines):
        dy = "0" if index == 0 else "1.2em"
        tspans.append(f'  <tspan x="{x:.2f}" dy="{dy}">{_svg_text(line)}</tspan>')
    tspans.append("</text>")
    return tspans


def _svg_text_payload(obj: VectorObject) -> Mapping[str, object] | None:
    meta = getattr(obj, "meta", None)
    if not isinstance(meta, Mapping):
        return None
    payload = meta.get(_TEXT_META_KEY)
    return payload if isinstance(payload, Mapping) else None


def _svg_text_content(payload: Mapping[str, object]) -> str:
    document = payload.get("document")
    if isinstance(document, Mapping):
        text = document.get("text")
        if text is not None:
            return str(text)
    return str(payload.get("content", "") or "")


def _svg_text_frame(
    payload: Mapping[str, object],
    obj: VectorObject,
) -> tuple[float, float, float, float]:
    frame = payload.get("frame")
    if isinstance(frame, Mapping):
        x = _svg_payload_float(frame, "x", _svg_payload_float(payload, "x", 0.0))
        y = _svg_payload_float(frame, "y", _svg_payload_float(payload, "y", 0.0))
        w = _svg_payload_float(frame, "width", _svg_payload_float(payload, "width", 1.0))
        h = _svg_payload_float(frame, "height", _svg_payload_float(payload, "height", 1.0))
        return (x, y, max(1.0, w), max(1.0, h))
    bbox = _compute_bbox(obj)
    if bbox is not None:
        min_x, min_y, max_x, max_y = bbox
        return (min_x, min_y, max(1.0, max_x - min_x), max(1.0, max_y - min_y))
    return (
        _svg_payload_float(payload, "x", 0.0),
        _svg_payload_float(payload, "y", 0.0),
        max(1.0, _svg_payload_float(payload, "width", 1.0)),
        max(1.0, _svg_payload_float(payload, "height", 1.0)),
    )


def _svg_text_font_family(payload: Mapping[str, object]) -> object:
    style_map = payload.get("style_map")
    if isinstance(style_map, Mapping):
        default_style = style_map.get("default")
        if isinstance(default_style, Mapping):
            family = default_style.get("font_family")
            if family is not None:
                return family
    return payload.get("font_family", _SVG_FONT_FAMILY_FALLBACK)


def _svg_text_color(
    payload: Mapping[str, object],
    obj: VectorObject,
) -> tuple[int, int, int, int]:
    style_map = payload.get("style_map")
    raw: object = None
    if isinstance(style_map, Mapping):
        default_style = style_map.get("default")
        if isinstance(default_style, Mapping):
            raw = default_style.get("color_rgba")
    if raw is None:
        raw = payload.get("color_rgba")
    if isinstance(raw, (list, tuple)) and len(raw) >= 3:
        channels: list[int] = []
        for value in raw[:4]:
            try:
                channels.append(max(0, min(255, round(float(value)))))
            except (TypeError, ValueError):
                channels.append(255)
        while len(channels) < 4:
            channels.append(255)
        return (channels[0], channels[1], channels[2], channels[3])
    r, g, b = obj.style.stroke_color
    return (r, g, b, 255)


def _svg_text_is_vertical(payload: Mapping[str, object]) -> bool:
    style_map = payload.get("style_map")
    if isinstance(style_map, Mapping):
        default_style = style_map.get("default")
        if isinstance(default_style, Mapping) and "vertical" in default_style:
            return bool(default_style.get("vertical"))
    return bool(payload.get("vertical"))


def _svg_text_rotation(payload: Mapping[str, object]) -> float:
    transform = payload.get("transform")
    if isinstance(transform, Mapping):
        return _svg_payload_float(transform, "rotation_deg", 0.0)
    return _svg_payload_float(payload, "rotation_deg", 0.0)


def _svg_payload_float(payload: Mapping[str, object], key: str, default: float) -> float:
    try:
        value = float(payload.get(key, default))
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _svg_attr(value: object) -> str:
    return html.escape(str(value), quote=True)


def _svg_text(value: object) -> str:
    return html.escape(str(value), quote=False)


def vector_objects_to_svg(
    objects: Sequence[VectorObject],
    canvas_width: int,
    canvas_height: int,
) -> str:
    """Export multiple *VectorObject*s as a single SVG document.

    The SVG viewBox matches the canvas dimensions so coordinates are preserved.
    """
    parts: list[str] = []
    for obj in objects:
        parts.extend(_svg_elements_for_object(obj))
    return _svg_document(parts, canvas_width, canvas_height)


def vector_object_groups_to_svg(
    groups: Sequence[tuple[Sequence[VectorObject], object]],
    canvas_width: int,
    canvas_height: int,
) -> str:
    """Export grouped *VectorObject*s as a single SVG document.

    Each group represents one source layer. Non-default layer opacity is emitted
    as safe SVG group opacity without serializing private layer metadata.
    """
    parts: list[str] = []
    for objects, opacity in groups:
        group_parts: list[str] = []
        for obj in objects:
            group_parts.extend(_svg_elements_for_object(obj))
        if not group_parts:
            continue
        safe_opacity = _safe_svg_opacity(opacity)
        if safe_opacity < 1.0:
            parts.append(f'  <g opacity="{safe_opacity:.3f}">')
            parts.extend(f"  {line}" for line in group_parts)
            parts.append("  </g>")
        else:
            parts.extend(group_parts)
    return _svg_document(parts, canvas_width, canvas_height)


def _svg_document(parts: Sequence[str], canvas_width: int, canvas_height: int) -> str:
    body = "\n".join(parts)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {canvas_width} {canvas_height}" '
        f'width="{canvas_width}" height="{canvas_height}">\n'
        f"{body}\n</svg>\n"
    )


def _safe_svg_opacity(value: object) -> float:
    try:
        opacity = float(value)
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(opacity):
        return 1.0
    return max(0.0, min(1.0, opacity))


def _svg_path_d(path: VectorPath) -> str:
    anchors = list(path.anchors)
    if not anchors:
        return ""
    s = anchors[0]
    segs = [f"M {s.pos[0]:.2f} {s.pos[1]:.2f}"]
    for i in range(1, len(anchors)):
        segs.append(_svg_seg(anchors[i - 1], anchors[i]))
    if path.closed:
        segs.append(_svg_seg(anchors[-1], anchors[0]))
        segs.append("Z")
    return " ".join(segs)


def _svg_seg(prev: Anchor, curr: Anchor) -> str:
    if prev.h_out or curr.h_in:
        c1 = _h_pt(prev.pos, prev.h_out)
        c2 = _h_pt(curr.pos, curr.h_in)
        return (
            f"C {c1[0]:.2f} {c1[1]:.2f} "
            f"{c2[0]:.2f} {c2[1]:.2f} "
            f"{curr.pos[0]:.2f} {curr.pos[1]:.2f}"
        )
    return f"L {curr.pos[0]:.2f} {curr.pos[1]:.2f}"


def _h_pt(pos: tuple[float, float], h: tuple[float, float] | None) -> tuple[float, float]:
    if h is None:
        return pos
    return (pos[0] + h[0], pos[1] + h[1])


def _rgb_hex(c: tuple[int, int, int]) -> str:
    return f"#{max(0,min(255,c[0])):02x}{max(0,min(255,c[1])):02x}{max(0,min(255,c[2])):02x}"


def _safe_svg_object_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    lower = candidate.lower()
    if _SAFE_SVG_ID_RE.fullmatch(candidate) and not any(
        marker in lower for marker in _SENSITIVE_SVG_ID_MARKERS
    ):
        return candidate
    digest = hashlib.sha1(candidate.encode("utf-8", "surrogatepass")).hexdigest()
    return f"vec-export-{digest[:12]}"


def _safe_svg_line_cap(value: object) -> str:
    candidate = str(value)
    return candidate if candidate in _SVG_LINE_CAP_VALUES else "butt"


def _safe_svg_line_join(value: object) -> str:
    candidate = str(value)
    return candidate if candidate in _SVG_LINE_JOIN_VALUES else "miter"


def _safe_svg_stroke_width(value: object) -> float:
    try:
        width = float(value)
    except (TypeError, ValueError):
        return 1.0
    return width if math.isfinite(width) and width >= 0.0 else 1.0


def _safe_svg_font_size(value: object) -> float:
    try:
        size = float(value)
    except (TypeError, ValueError):
        return 32.0
    if not math.isfinite(size):
        return 32.0
    return max(1.0, min(512.0, size))


def _safe_svg_font_family(value: object) -> str:
    candidate = str(value or "").strip()
    lower = candidate.lower()
    if (
        not candidate
        or len(candidate) > 128
        or any(marker in lower for marker in _SENSITIVE_SVG_ID_MARKERS)
        or any(ord(ch) < 32 for ch in candidate)
    ):
        return _SVG_FONT_FAMILY_FALLBACK
    return candidate


# ---------------------------------------------------------------------------
# CLI: python -m loomstroke.plugins.vector_tools.parametric_shapes [shape_id] [out.svg]
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    shape_id = sys.argv[1] if len(sys.argv) > 1 else "speech_cloud"
    out_path = sys.argv[2] if len(sys.argv) > 2 else None

    obj = create_basic_shape_object(shape_id, (200.0, 200.0), default_size_px=300)
    if obj is None:
        print(f"Unknown shape: {shape_id}", file=sys.stderr)
        sys.exit(1)

    svg = vector_object_to_svg(obj)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(svg)
        print(f"Wrote {out_path}")
    else:
        print(svg)
