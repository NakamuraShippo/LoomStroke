from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from math import isfinite

from ...core.commands import Command

logger = logging.getLogger(__name__)

VECTOR_IDS_META_KEY = "loomstroke:ids"
VECTOR_STROKE_PROFILE_META_KEY = "loomstroke:stroke_profile"
_ALLOWED_ANCHOR_KINDS = frozenset({"corner", "smooth"})
_ALLOWED_LINE_CAPS = frozenset({"butt", "round", "square"})
_ALLOWED_LINE_JOINS = frozenset({"miter", "round", "bevel"})
_MAX_VECTOR_COORD_ABS = 1_000_000.0
_VECTOR_IDS_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Anchor:
    pos: tuple[float, float]
    kind: str = "corner"  # "corner" | "smooth"
    h_in: tuple[float, float] | None = None
    h_out: tuple[float, float] | None = None


@dataclass(frozen=True)
class VectorStyle:
    stroke_color: tuple[int, int, int] = (0, 0, 0)
    stroke_width: float = 1.0
    fill_enabled: bool = False
    fill_color: tuple[int, int, int] | None = None
    join: str = "miter"
    cap: str = "butt"
    miter_limit: float = 4.0
    dash: tuple[float, ...] = ()


@dataclass(frozen=True)
class VectorPath:
    anchors: Sequence[Anchor]
    closed: bool = False


@dataclass(frozen=True)
class VectorObject:
    id: str
    paths: list[VectorPath]
    style: VectorStyle
    meta: dict[str, object] = field(default_factory=dict)
    preview: bool = False


def make_vector_object(
    paths: Sequence[VectorPath],
    style: VectorStyle,
    meta: dict[str, object] | None = None,
) -> VectorObject:
    return VectorObject(
        id=f"vec-{uuid.uuid4().hex[:8]}",
        paths=list(paths),
        style=style,
        meta=meta or {},
    )


def ensure_vector_object_id(value: object, *, fallback_index: int = 0) -> str:
    """Return an existing vector object id or a deterministic fallback id.

    Older/partial payloads can miss ``id``. Loading those with a random UUID
    breaks selection and redo stability across repeated loads, so the fallback
    is derived from the vector payload plus its list position.
    """

    existing = _existing_vector_object_id(value)
    if existing:
        return existing
    payload = _canonical_vector_payload(value)
    seed = {
        "fallback_index": max(0, int(fallback_index)),
        "payload": payload,
        "version": 1,
    }
    digest = hashlib.sha1(
        json.dumps(seed, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    return f"vec-{digest[:12]}"


def ensure_vector_element_ids(obj: VectorObject, *, object_index: int = 0) -> VectorObject:
    """Return *obj* with stable subpath, anchor, and text-run ids in metadata.

    The ids live in optional ``meta["loomstroke:ids"]`` data so existing
    ``.lstroke`` payloads and the public vector object schema stay unchanged.
    Existing ids are preserved by index; missing entries are deterministic from
    the object id and canonical element payload.
    """

    object_id = ensure_vector_object_id(obj, fallback_index=object_index)
    if object_id != obj.id:
        obj = clone_vector_object_with_patch(obj, id=object_id)

    raw_ids = obj.meta.get(VECTOR_IDS_META_KEY)
    ids_meta = dict(raw_ids) if isinstance(raw_ids, Mapping) else {}
    paths_meta = _ensure_path_element_ids(obj, ids_meta, object_index=object_index)
    text_runs_meta = _ensure_text_run_ids(obj, ids_meta, object_index=object_index)

    ids_meta["schema_version"] = _VECTOR_IDS_SCHEMA_VERSION
    ids_meta["paths"] = paths_meta
    if text_runs_meta:
        ids_meta["text_runs"] = text_runs_meta
    else:
        ids_meta.pop("text_runs", None)

    meta = dict(obj.meta)
    meta[VECTOR_IDS_META_KEY] = ids_meta
    if meta == obj.meta:
        return obj
    return clone_vector_object_with_patch(obj, meta=meta)


def clone_vector_object_with_patch(obj: VectorObject, **patch: object) -> VectorObject:
    """Deep-copy a vector object and replace selected top-level fields."""

    allowed = {"id", "paths", "style", "meta", "preview"}
    unknown = set(patch) - allowed
    if unknown:
        raise TypeError(f"Unknown vector object field(s): {', '.join(sorted(unknown))}")

    base = copy_vector_object(obj)
    object_id = str(patch.get("id", base.id))
    raw_paths = patch.get("paths", base.paths)
    paths = list(raw_paths) if isinstance(raw_paths, Sequence) else base.paths
    style = patch.get("style", base.style)
    if not isinstance(style, VectorStyle):
        style = base.style
    raw_meta = patch.get("meta", base.meta)
    meta = dict(raw_meta) if isinstance(raw_meta, Mapping) else dict(base.meta)
    preview = bool(patch.get("preview", base.preview))
    return VectorObject(id=object_id, paths=paths, style=style, meta=meta, preview=preview)


def normalize_vector_object(value: object, *, fallback_index: int = 0) -> VectorObject | None:
    """Return *value* as the existing VectorObject dataclass contract.

    The helper accepts the persisted dict schema and the in-memory dataclass
    shape. It deliberately keeps unknown metadata intact and does not introduce
    a second vector model.
    """

    if isinstance(value, VectorObject):
        obj = copy_vector_object(value)
        object_id = ensure_vector_object_id(obj, fallback_index=fallback_index)
        if object_id == obj.id:
            return obj
        return clone_vector_object_with_patch(obj, id=object_id)

    if not isinstance(value, Mapping):
        return None
    raw_paths = value.get("paths", [])
    paths: list[VectorPath] = []
    if isinstance(raw_paths, Sequence) and not isinstance(raw_paths, (str, bytes, bytearray)):
        for raw_path in raw_paths:
            path = _normalize_vector_path(raw_path)
            if path is not None:
                paths.append(path)
    raw_meta = value.get("meta")
    meta = dict(raw_meta) if isinstance(raw_meta, Mapping) else {}
    return VectorObject(
        id=ensure_vector_object_id(value, fallback_index=fallback_index),
        paths=paths,
        style=_normalize_vector_style(value.get("style")),
        meta=meta,
        preview=bool(value.get("preview", False)),
    )


def denormalize_vector_object(obj: VectorObject) -> dict[str, object]:
    """Convert a VectorObject back to the existing JSON-safe persisted schema."""

    payload: dict[str, object] = {
        "id": str(obj.id),
        "paths": [
            {
                "anchors": [
                    {
                        "pos": [float(anchor.pos[0]), float(anchor.pos[1])],
                        "kind": str(anchor.kind or "corner"),
                        "h_in": _point_to_list(anchor.h_in),
                        "h_out": _point_to_list(anchor.h_out),
                    }
                    for anchor in path.anchors
                ],
                "closed": bool(path.closed),
            }
            for path in obj.paths
        ],
        "style": {
            "stroke_color": [int(v) for v in obj.style.stroke_color],
            "stroke_width": float(obj.style.stroke_width),
            "fill_enabled": bool(obj.style.fill_enabled),
            "fill_color": _color_to_list(obj.style.fill_color),
            "join": str(obj.style.join or "miter"),
            "cap": str(obj.style.cap or "butt"),
            "miter_limit": float(obj.style.miter_limit),
            "dash": [float(v) for v in obj.style.dash],
        },
    }
    if obj.meta:
        meta = _canonical_json(obj.meta)
        payload["meta"] = meta if isinstance(meta, dict) else {}
    if obj.preview:
        payload["preview"] = True
    return payload


def compute_vector_bounds(
    obj: VectorObject, *, include_handles: bool = True, include_stroke: bool = True
) -> tuple[float, float, float, float] | None:
    """Return a lightweight canvas-space bounds box for paths and text frames."""

    xs: list[float] = []
    ys: list[float] = []

    def add_point(point: tuple[float, float] | None) -> None:
        if point is None:
            return
        x, y = point
        if isfinite(x) and isfinite(y):
            xs.append(float(x))
            ys.append(float(y))

    for path in obj.paths:
        for anchor in path.anchors:
            add_point(anchor.pos)
            if include_handles:
                add_point(_absolute_handle(anchor.pos, anchor.h_in))
                add_point(_absolute_handle(anchor.pos, anchor.h_out))

    for point in _text_frame_points(obj.meta):
        add_point(point)

    if not xs or not ys:
        return None

    min_x, min_y, max_x, max_y = min(xs), min(ys), max(xs), max(ys)
    if include_stroke and isfinite(obj.style.stroke_width):
        pad = max(0.0, float(obj.style.stroke_width)) * 0.5
        min_x -= pad
        min_y -= pad
        max_x += pad
        max_y += pad
    return (min_x, min_y, max_x, max_y)


def validate_vector_object(obj: VectorObject) -> tuple[str, ...]:
    """Return lightweight validation issue codes without mutating *obj*."""

    issues: list[str] = []
    if not obj.id:
        issues.append("missing_id")
    if not obj.paths:
        issues.append("missing_paths")
    for path_index, path in enumerate(obj.paths):
        issues.extend(_validate_vector_path(path_index, path))
    issues.extend(_validate_vector_style(obj.style))
    return tuple(issues)


def _validate_vector_path(path_index: int, path: VectorPath) -> list[str]:
    issues: list[str] = []
    if not path.anchors:
        return [f"path_{path_index}_missing_anchors"]
    if path.closed and len(path.anchors) < 3:
        issues.append(f"path_{path_index}_closed_too_few_anchors")
    for anchor_index, anchor in enumerate(path.anchors):
        issues.extend(_validate_anchor(path_index, anchor_index, anchor))
    return issues


def _validate_anchor(path_index: int, anchor_index: int, anchor: Anchor) -> list[str]:
    prefix = f"path_{path_index}_anchor_{anchor_index}"
    issues: list[str] = []
    if _normalized_token(anchor.kind) not in _ALLOWED_ANCHOR_KINDS:
        issues.append(f"{prefix}_invalid_kind")
    if not _valid_point(anchor.pos):
        issues.append(f"{prefix}_invalid_pos")
    elif not _point_within_bounds(anchor.pos):
        issues.append(f"{prefix}_pos_out_of_bounds")
    issues.extend(_validate_optional_handle(prefix, "h_in", anchor.pos, anchor.h_in))
    issues.extend(_validate_optional_handle(prefix, "h_out", anchor.pos, anchor.h_out))
    return issues


def _validate_optional_handle(
    prefix: str,
    name: str,
    pos: tuple[float, float],
    handle: tuple[float, float] | None,
) -> list[str]:
    if handle is None:
        return []
    if not _valid_point(handle):
        return [f"{prefix}_invalid_{name}"]
    if not _handle_within_bounds(pos, handle):
        return [f"{prefix}_{name}_out_of_bounds"]
    return []


def _validate_vector_style(style: VectorStyle) -> list[str]:
    issues: list[str] = []
    if not _valid_color(style.stroke_color):
        issues.append("invalid_stroke_color")
    if style.fill_color is not None and not _valid_color(style.fill_color):
        issues.append("invalid_fill_color")
    if not _valid_nonnegative_float(style.stroke_width):
        issues.append("invalid_stroke_width")
    if _normalized_token(style.join) not in _ALLOWED_LINE_JOINS:
        issues.append("invalid_join")
    if _normalized_token(style.cap) not in _ALLOWED_LINE_CAPS:
        issues.append("invalid_cap")
    if not _valid_positive_float(style.miter_limit):
        issues.append("invalid_miter_limit")
    if not _valid_dash(style.dash):
        issues.append("invalid_dash")
    return issues


def copy_vector_object(obj: VectorObject) -> VectorObject:
    paths = []
    for path in obj.paths:
        anchors = [Anchor(pos=a.pos, kind=a.kind, h_in=a.h_in, h_out=a.h_out) for a in path.anchors]
        paths.append(VectorPath(anchors=anchors, closed=path.closed))
    return VectorObject(
        id=obj.id,
        paths=paths,
        style=VectorStyle(
            stroke_color=obj.style.stroke_color,
            stroke_width=obj.style.stroke_width,
            fill_enabled=obj.style.fill_enabled,
            fill_color=obj.style.fill_color,
            join=obj.style.join,
            cap=obj.style.cap,
            miter_limit=obj.style.miter_limit,
            dash=obj.style.dash,
        ),
        meta=dict(obj.meta),
        preview=obj.preview,
    )


def _existing_vector_object_id(value: object) -> str | None:
    raw = value.get("id") if isinstance(value, Mapping) else getattr(value, "id", None)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _canonical_vector_payload(value: object) -> object:
    if isinstance(value, VectorObject):
        return {
            "meta": _canonical_json(value.meta),
            "paths": _canonical_json(value.paths),
            "preview": bool(value.preview),
            "style": _canonical_json(value.style),
        }
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_json(raw)
            for key, raw in sorted(value.items(), key=lambda item: str(item[0]))
            if str(key) != "id"
        }
    return _canonical_json(value)


def _canonical_json(value: object) -> object:  # noqa: PLR0911
    if isinstance(value, Anchor):
        return {
            "h_in": _canonical_json(value.h_in),
            "h_out": _canonical_json(value.h_out),
            "kind": value.kind,
            "pos": _canonical_json(value.pos),
        }
    if isinstance(value, VectorPath):
        return {"anchors": _canonical_json(list(value.anchors)), "closed": bool(value.closed)}
    if isinstance(value, VectorStyle):
        return {
            "cap": value.cap,
            "dash": _canonical_json(value.dash),
            "fill_color": _canonical_json(value.fill_color),
            "fill_enabled": bool(value.fill_enabled),
            "join": value.join,
            "miter_limit": _canonical_json(value.miter_limit),
            "stroke_color": _canonical_json(value.stroke_color),
            "stroke_width": _canonical_json(value.stroke_width),
        }
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_json(raw)
            for key, raw in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonical_json(item) for item in value]
    if isinstance(value, float):
        return value if isfinite(value) else str(value)
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return str(value)


def _ensure_path_element_ids(
    obj: VectorObject,
    existing_ids: Mapping[str, object],
    *,
    object_index: int,
) -> list[dict[str, object]]:
    existing_paths = existing_ids.get("paths")
    paths_meta: list[dict[str, object]] = []
    for path_index, path in enumerate(obj.paths):
        existing_path = _indexed_mapping(existing_paths, path_index)
        path_id = _indexed_entry_id(existing_paths, path_index)
        if path_id is None:
            path_id = _stable_vector_element_id(
                "path",
                {
                    "object_id": obj.id,
                    "object_index": object_index,
                    "path": _canonical_json(path),
                    "path_index": path_index,
                    "schema_version": _VECTOR_IDS_SCHEMA_VERSION,
                },
            )

        anchors_meta: list[dict[str, object]] = []
        existing_anchors = existing_path.get("anchors") if existing_path else None
        for anchor_index, anchor in enumerate(path.anchors):
            existing_anchor = _indexed_mapping(existing_anchors, anchor_index)
            anchor_id = _indexed_entry_id(existing_anchors, anchor_index)
            if anchor_id is None:
                anchor_id = _stable_vector_element_id(
                    "anchor",
                    {
                        "anchor": _canonical_json(anchor),
                        "anchor_index": anchor_index,
                        "object_id": obj.id,
                        "object_index": object_index,
                        "path_id": path_id,
                        "path_index": path_index,
                        "schema_version": _VECTOR_IDS_SCHEMA_VERSION,
                    },
                )
            anchor_meta = dict(existing_anchor) if existing_anchor else {}
            anchor_meta["id"] = anchor_id
            anchors_meta.append(anchor_meta)

        path_meta = dict(existing_path) if existing_path else {}
        path_meta["id"] = path_id
        path_meta["anchors"] = anchors_meta
        paths_meta.append(path_meta)
    return paths_meta


def _ensure_text_run_ids(
    obj: VectorObject,
    existing_ids: Mapping[str, object],
    *,
    object_index: int,
) -> list[dict[str, object]]:
    runs = _text_document_runs(obj.meta)
    if not runs:
        return []

    existing_runs = existing_ids.get("text_runs")
    result: list[dict[str, object]] = []
    for run_index, run in enumerate(runs):
        existing_run = _indexed_mapping(existing_runs, run_index)
        run_id = _indexed_entry_id(existing_runs, run_index)
        if run_id is None:
            run_id = _stable_vector_element_id(
                "text-run",
                {
                    "object_id": obj.id,
                    "object_index": object_index,
                    "run": _canonical_json(run),
                    "run_index": run_index,
                    "schema_version": _VECTOR_IDS_SCHEMA_VERSION,
                },
            )
        run_meta = dict(existing_run) if existing_run else {}
        run_meta["id"] = run_id
        result.append(run_meta)
    return result


def _text_document_runs(meta: Mapping[str, object]) -> list[Mapping[str, object]]:
    raw_text = meta.get("text_tool")
    if not isinstance(raw_text, Mapping):
        return []
    raw_document = raw_text.get("document")
    if not isinstance(raw_document, Mapping):
        return []
    raw_runs = raw_document.get("runs")
    if not isinstance(raw_runs, Sequence) or isinstance(raw_runs, (str, bytes, bytearray)):
        return []
    return [run for run in raw_runs if isinstance(run, Mapping)]


def _indexed_mapping(value: object, index: int) -> Mapping[str, object] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return None
    if index < 0 or index >= len(value):
        return None
    entry = value[index]
    return entry if isinstance(entry, Mapping) else None


def _indexed_entry_id(value: object, index: int) -> str | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return None
    if index < 0 or index >= len(value):
        return None
    entry = value[index]
    raw = entry.get("id") if isinstance(entry, Mapping) else entry
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _stable_vector_element_id(prefix: str, seed: object) -> str:
    digest = hashlib.sha1(
        json.dumps(seed, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    return f"{prefix}-{digest[:12]}"


def _normalize_vector_path(value: object) -> VectorPath | None:
    if isinstance(value, VectorPath):
        return VectorPath(
            anchors=[
                Anchor(pos=anchor.pos, kind=anchor.kind, h_in=anchor.h_in, h_out=anchor.h_out)
                for anchor in value.anchors
            ],
            closed=value.closed,
        )
    if not isinstance(value, Mapping):
        return None
    raw_anchors = value.get("anchors", [])
    anchors: list[Anchor] = []
    if isinstance(raw_anchors, Sequence) and not isinstance(raw_anchors, (str, bytes, bytearray)):
        for raw_anchor in raw_anchors:
            anchor = _normalize_anchor(raw_anchor)
            if anchor is not None:
                anchors.append(anchor)
    if not anchors:
        return None
    return VectorPath(anchors=anchors, closed=bool(value.get("closed", False)))


def _normalize_anchor(value: object) -> Anchor | None:
    if isinstance(value, Anchor):
        return Anchor(pos=value.pos, kind=value.kind, h_in=value.h_in, h_out=value.h_out)
    if not isinstance(value, Mapping):
        return None
    pos = _coerce_point_tuple(value.get("pos"))
    if pos is None:
        return None
    return Anchor(
        pos=pos,
        kind=str(value.get("kind", "corner") or "corner"),
        h_in=_coerce_point_tuple(value.get("h_in")),
        h_out=_coerce_point_tuple(value.get("h_out")),
    )


def _normalize_vector_style(value: object) -> VectorStyle:
    if isinstance(value, VectorStyle):
        return VectorStyle(
            stroke_color=value.stroke_color,
            stroke_width=value.stroke_width,
            fill_enabled=value.fill_enabled,
            fill_color=value.fill_color,
            join=value.join,
            cap=value.cap,
            miter_limit=value.miter_limit,
            dash=value.dash,
        )
    if not isinstance(value, Mapping):
        return VectorStyle()
    stroke_color = normalize_rgb(value.get("stroke_color")) or (0, 0, 0)
    fill_color = normalize_rgb(value.get("fill_color"))
    return VectorStyle(
        stroke_color=stroke_color,
        stroke_width=_coerce_float(value.get("stroke_width"), default=1.0),
        fill_enabled=bool(value.get("fill_enabled", False) and fill_color is not None),
        fill_color=fill_color,
        join=str(value.get("join", "miter") or "miter"),
        cap=str(value.get("cap", "butt") or "butt"),
        miter_limit=_coerce_float(value.get("miter_limit"), default=4.0),
        dash=_coerce_dash_tuple(value.get("dash")),
    )


def _coerce_point_tuple(value: object) -> tuple[float, float] | None:
    if value is None:
        return None
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) < 2:
            return None
        try:
            x = float(value[0])
            y = float(value[1])
        except (TypeError, ValueError):
            return None
        if isfinite(x) and isfinite(y):
            return (x, y)
    return None


def _coerce_float(value: object, *, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if isfinite(number) else default


def _coerce_dash_tuple(value: object) -> tuple[float, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    values: list[float] = []
    for item in value:
        try:
            number = float(item)
        except (TypeError, ValueError):
            continue
        if isfinite(number) and number >= 0:
            values.append(number)
    return tuple(values)


def _point_to_list(value: tuple[float, float] | None) -> list[float] | None:
    if value is None:
        return None
    return [float(value[0]), float(value[1])]


def _color_to_list(value: tuple[int, int, int] | None) -> list[int] | None:
    if value is None:
        return None
    return [int(value[0]), int(value[1]), int(value[2])]


def _absolute_handle(
    pos: tuple[float, float], handle: tuple[float, float] | None
) -> tuple[float, float] | None:
    if handle is None:
        return None
    return (pos[0] + handle[0], pos[1] + handle[1])


def _text_frame_points(meta: Mapping[str, object]) -> list[tuple[float, float]]:
    raw = meta.get("text_tool")
    if not isinstance(raw, Mapping):
        return []
    frame = raw.get("frame")
    if not isinstance(frame, Mapping):
        frame = raw
    try:
        x = float(frame.get("x", 0.0))
        y = float(frame.get("y", 0.0))
        width = float(frame.get("width", 0.0))
        height = float(frame.get("height", 0.0))
    except (TypeError, ValueError):
        return []
    if width <= 0 or height <= 0:
        return []
    angle = _text_rotation(raw)
    corners = [(x, y), (x + width, y), (x + width, y + height), (x, y + height)]
    if angle == 0.0:
        return corners

    import math  # noqa: PLC0415

    cx = x + width * 0.5
    cy = y + height * 0.5
    rad = math.radians(angle)
    cos_a = math.cos(rad)
    sin_a = math.sin(rad)
    return [
        (
            cx + (px - cx) * cos_a - (py - cy) * sin_a,
            cy + (px - cx) * sin_a + (py - cy) * cos_a,
        )
        for px, py in corners
    ]


def _text_rotation(raw: Mapping[str, object]) -> float:
    transform = raw.get("transform")
    value: object = raw.get("rotation_deg", 0.0)
    if isinstance(transform, Mapping):
        value = transform.get("rotation_deg", value)
    try:
        angle = float(value)
    except (TypeError, ValueError):
        return 0.0
    return angle if isfinite(angle) else 0.0


def _point_components(value: tuple[float, float] | None) -> tuple[float, float] | None:
    if value is None or len(value) < 2:
        return None
    try:
        x = float(value[0])
        y = float(value[1])
    except (TypeError, ValueError):
        return None
    if not isfinite(x) or not isfinite(y):
        return None
    return (x, y)


def _valid_point(value: tuple[float, float] | None) -> bool:
    return _point_components(value) is not None


def _point_within_bounds(value: tuple[float, float] | None) -> bool:
    point = _point_components(value)
    if point is None:
        return False
    return abs(point[0]) <= _MAX_VECTOR_COORD_ABS and abs(point[1]) <= _MAX_VECTOR_COORD_ABS


def _handle_within_bounds(pos: tuple[float, float], handle: tuple[float, float]) -> bool:
    handle_point = _point_components(handle)
    if handle_point is None:
        return False
    pos_point = _point_components(pos)
    if pos_point is None:
        return True
    if abs(handle_point[0]) > _MAX_VECTOR_COORD_ABS or abs(handle_point[1]) > _MAX_VECTOR_COORD_ABS:
        return False
    absolute = (pos_point[0] + handle_point[0], pos_point[1] + handle_point[1])
    return _point_within_bounds(absolute)


def _normalized_token(value: object) -> str:
    return str(value or "").strip().lower()


def _valid_nonnegative_float(value: object) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return isfinite(number) and number >= 0


def _valid_positive_float(value: object) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return isfinite(number) and number > 0


def _valid_color(value: tuple[int, int, int] | None) -> bool:
    if value is None or len(value) < 3:
        return False
    try:
        components = [int(component) for component in value[:3]]
    except (TypeError, ValueError):
        return False
    return all(0 <= component <= 255 for component in components)


def _valid_dash(value: Sequence[float]) -> bool:
    for component in value:
        try:
            number = float(component)
        except (TypeError, ValueError):
            return False
        if not isfinite(number) or number < 0:
            return False
    return True


def normalize_rgb(value: object) -> tuple[int, int, int] | None:  # noqa: PLR0911, PLR0912
    """Normalize a UI/tool-provided color to (r,g,b) ints in 0..255.

    Accepted inputs:
    - (r,g,b) or (r,g,b,a) tuples/lists as floats in 0..1 or ints in 0..255
    - hex strings like "#RRGGBB" or "#RGB"
    - None (returns None)
    """

    if value is None:
        return None

    try:
        from PySide6.QtGui import QColor  # noqa: PLC0415  # optional UI dependency
    except ImportError:  # pragma: no cover - optional UI dependency
        QColor = None  # type: ignore[assignment]  # optional UI dependency fallback

    if QColor is not None and isinstance(value, QColor):
        return (value.red(), value.green(), value.blue())

    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if s.startswith("#"):
            hex_part = s[1:]
            if len(hex_part) == 3:
                try:
                    r = int(hex_part[0] * 2, 16)
                    g = int(hex_part[1] * 2, 16)
                    b = int(hex_part[2] * 2, 16)
                except ValueError:
                    return None
                return (r, g, b)
            if len(hex_part) == 6:
                try:
                    r = int(hex_part[0:2], 16)
                    g = int(hex_part[2:4], 16)
                    b = int(hex_part[4:6], 16)
                except ValueError:
                    return None
                return (r, g, b)
        return None

    if isinstance(value, (tuple, list)) and len(value) >= 3:
        raw = value[:3]
        try:
            r_f, g_f, b_f = (float(raw[0]), float(raw[1]), float(raw[2]))
        except (TypeError, ValueError):
            return None

        # Use type-based detection: if all components are Python int (not float),
        # treat as 0-255 integers.  Only scale when all are float and in [0, 1].
        all_int = all(isinstance(c, int) for c in raw)
        if all_int:
            scale = 1.0
        elif max(r_f, g_f, b_f) <= 1.0:
            scale = 255.0
        else:
            scale = 1.0
        r = max(0, min(255, round(r_f * scale)))
        g = max(0, min(255, round(g_f * scale)))
        b = max(0, min(255, round(b_f * scale)))
        return (r, g, b)

    return None


class VectorCreateCommand(Command):
    """
    Lightweight command storing vector objects on the canvas.

    Rendering is intentionally deferred; the command only tracks data and history.
    """

    def __init__(
        self,
        vector_object: VectorObject,
        *,
        layer_id: str | None = None,
        merge_key: str | None = None,
        created_layer_id: str | None = None,
    ) -> None:
        super().__init__(merge_key)
        self.vector_object = vector_object
        self.layer_id = layer_id
        self._store_key = "_vector_objects"
        self._insert_index: int | None = None
        self._insert_layer_id: str | None = None
        self._created_layer_id: str | None = created_layer_id
        self._created_layer_name: str | None = None
        self._created_layer_vector_kind: str | None = None
        self._created_layer_index: int | None = None

    def apply(self, canvas) -> bool:
        existing_layer_ids: set[str] | None = None
        get_layers = getattr(canvas, "get_layers", None)
        if callable(get_layers):
            try:
                existing_layer_ids = {ref.id for ref in get_layers() if getattr(ref, "id", None)}
            except Exception:
                existing_layer_ids = None

        add_vector = getattr(canvas, "add_vector_object", None)
        if callable(add_vector):
            if self._created_layer_id and self.layer_id is None:
                self.layer_id = self._created_layer_id
            if (
                self._created_layer_id
                and self.layer_id == self._created_layer_id
                and existing_layer_ids is not None
                and self._created_layer_id not in existing_layer_ids
            ):
                self._restore_created_layer(canvas)
            result = add_vector(self.vector_object, layer_id=self.layer_id, recompose=False)
            if result is None:
                return False
            self._insert_layer_id, self._insert_index = result
            if (
                self._created_layer_id is None
                and existing_layer_ids is not None
                and self._insert_layer_id not in existing_layer_ids
            ):
                self._created_layer_id = self._insert_layer_id
            if self._created_layer_id and self._insert_layer_id == self._created_layer_id:
                self._capture_created_layer_metadata(canvas, self._created_layer_id)
            self._dirty_region = None
            logger.info(
                "VectorCreateCommand applied id=%s layer=%s index=%s",
                self.vector_object.id,
                self._insert_layer_id,
                self._insert_index,
            )
            return True

        store = getattr(canvas, self._store_key, None)
        if store is None:
            store = []
            try:
                setattr(canvas, self._store_key, store)
            except Exception:
                return False
        self._insert_layer_id = "legacy"
        self._insert_index = len(store)
        store.append(self.vector_object)
        self._dirty_region = None
        logger.info(
            "VectorCreateCommand applied id=%s store_len=%s", self.vector_object.id, len(store)
        )
        return True

    def revert(self, canvas) -> bool:
        remove_vector = getattr(canvas, "remove_vector_object", None)
        if (
            callable(remove_vector)
            and self._insert_layer_id is not None
            and self._insert_index is not None
        ):
            removed = bool(
                remove_vector(
                    self._insert_layer_id,
                    self._insert_index,
                    recompose=True,
                    vec_id=self.vector_object.id,
                )
            )
            if removed:
                self._cleanup_created_layer(canvas)
            return removed

        store = getattr(canvas, self._store_key, None)
        if store is None:
            return True
        # Prefer ID-based lookup over positional index
        vec_id = self.vector_object.id
        removed_idx: int | None = None
        if (
            self._insert_index is not None
            and 0 <= self._insert_index < len(store)
            and getattr(store[self._insert_index], "id", None) == vec_id
        ):
            removed_idx = self._insert_index
        if removed_idx is None:
            for i, obj in enumerate(store):
                if getattr(obj, "id", None) == vec_id:
                    removed_idx = i
                    break
        if removed_idx is not None:
            try:
                del store[removed_idx]
            except Exception:
                return False
        self._cleanup_created_layer(canvas)
        return True

    def _cleanup_created_layer(self, canvas: object) -> None:
        if self._created_layer_id is None:
            return
        layer_id = self._created_layer_id
        self._capture_created_layer_metadata(canvas, layer_id)
        has_vectors = False
        getter = getattr(canvas, "get_vector_objects", None)
        if callable(getter):
            try:
                has_vectors = any(lid == layer_id for lid, _ in getter(include_hidden=True))
            except Exception:
                has_vectors = True
        if has_vectors:
            return
        remover = getattr(canvas, "remove_layer", None)
        if callable(remover):
            with contextlib.suppress(Exception):
                remover(layer_id)

    def _capture_created_layer_metadata(self, canvas: object, layer_id: str) -> None:
        getter = getattr(canvas, "get_layers", None)
        if not callable(getter):
            return
        try:
            layers = list(getter())
        except Exception:
            return
        for index, ref in enumerate(layers):
            if getattr(ref, "id", None) != layer_id:
                continue
            name = getattr(ref, "name", None)
            vector_kind = getattr(ref, "vector_kind", None)
            if isinstance(name, str) and name:
                self._created_layer_name = name
            if isinstance(vector_kind, str) and vector_kind:
                self._created_layer_vector_kind = vector_kind
            self._created_layer_index = index
            return

    def _restore_created_layer(self, canvas: object) -> bool:
        if self._created_layer_id is None:
            return False
        restorer = getattr(canvas, "restore_vector_layer", None)
        if not callable(restorer):
            return False
        try:
            ref = restorer(
                layer_id=self._created_layer_id,
                name=self._created_layer_name,
                vector_kind=self._created_layer_vector_kind or "draw",
                index=self._created_layer_index,
            )
        except Exception:
            logger.debug("VectorCreateCommand failed to restore vector layer", exc_info=True)
            return False
        return getattr(ref, "id", None) == self._created_layer_id


class VectorReplaceCommand(Command):
    """Replace an existing vector object in the canvas vector store."""

    def __init__(
        self,
        target_id: str,
        new_object: VectorObject,
        *,
        layer_id: str | None = None,
    ) -> None:
        super().__init__(merge_key=None)
        self.target_id = target_id
        self.new_object = new_object
        self._old_object: VectorObject | None = None
        self._index: int | None = None
        self._layer_id: str | None = layer_id
        self._story_replacement = None

    def apply(self, canvas) -> bool:  # noqa: PLR0911 - command backend and rejected story guards
        from loomstroke.core.japanese_text.story_commands import (  # noqa: PLC0415
            prepare_story_replacement,
        )

        try:
            self._story_replacement = prepare_story_replacement(
                canvas, self.target_id, self.new_object
            )
        except ValueError as error:
            logger.warning("Linked text replacement rejected: %s", error)
            return False
        if self._story_replacement is not None:
            return self._story_replacement.apply(canvas)
        replace_vector = getattr(canvas, "replace_vector_object", None)
        if callable(replace_vector):
            result = replace_vector(
                self.target_id, self.new_object, layer_id=self._layer_id, recompose=False
            )
            if result is None:
                return False
            self._layer_id, self._index, self._old_object = result
            return True

        store = getattr(canvas, "_vector_objects", None)
        if not store:
            return False
        for idx, obj in enumerate(store):
            if getattr(obj, "id", None) == self.target_id:
                self._old_object = obj
                self._index = idx
                store[idx] = self.new_object
                return True
        return False

    def revert(self, canvas) -> bool:
        if self._story_replacement is not None:
            return self._story_replacement.revert(canvas)
        if self._index is None or self._old_object is None:
            return True

        replace_vector = getattr(canvas, "replace_vector_object", None)
        if callable(replace_vector) and self._layer_id is not None:
            replace_vector(
                self.target_id, self._old_object, layer_id=self._layer_id, recompose=True
            )
            return True

        store = getattr(canvas, "_vector_objects", None)
        if store is None:
            return True
        if 0 <= self._index < len(store):
            store[self._index] = self._old_object
        return True
