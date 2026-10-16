"""Main-thread capture of immutable vector scene snapshots.

Builds ``core.vector_render_models.VectorSceneSnapshot`` from the live
``SimpleCanvasService`` state (spec §6/§7 of
``docs/dev/plan/future/vector/LOOMSTROKE_WGPU_RETAINED_VECTOR_RENDERING_DESIGN_SPEC.md``).

Must be called on the Qt main thread; the returned snapshot holds no live
document references, no Qt objects, and is safe to hand to compile workers
or the WGPU backend.  Building a snapshot never mutates the canvas.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from loomstroke.core.vector_correction.stroke_profile import StrokeProfile
from loomstroke.core.vector_render_models import (
    IDENTITY_TRANSFORM,
    AffineTuple,
    FilterRenderRef,
    MaskRenderRef,
    VectorAnchorSnapshot,
    VectorObjectSnapshot,
    VectorPathSnapshot,
    VectorPreviewSnapshot,
    VectorRenderLayerSnapshot,
    VectorSceneSnapshot,
    VectorStyleSnapshot,
    stable_signature,
)
from loomstroke.core.vector_text_glyph_adapter import build_text_object_snapshot

_VECTOR_IDS_META_KEY = "loomstroke:ids"
_STROKE_PROFILE_META_KEY = "loomstroke:stroke_profile"
_TEXT_META_KEY = "text_tool"
_VECTOR_LAYER_TYPES = frozenset({"vector", "text"})


def build_vector_scene_snapshot(canvas: Any) -> VectorSceneSnapshot:
    """Capture all vector layers of *canvas* as an immutable scene snapshot."""

    layers: list[VectorRenderLayerSnapshot] = []
    for layer, parent_id in _iter_layers_with_parent(getattr(canvas, "_layers", []) or []):
        if str(getattr(layer, "layer_type", "raster")) not in _VECTOR_LAYER_TYPES:
            continue
        layers.append(_snapshot_layer(layer, parent_id, getattr(canvas, "_vector_preview", None)))

    canvas_width = int(getattr(canvas, "_width", 0) or 0)
    canvas_height = int(getattr(canvas, "_height", 0) or 0)
    preview = _snapshot_preview(canvas)
    scene_version = stable_signature(
        (
            canvas_width,
            canvas_height,
            tuple(
                (
                    layer.layer_id,
                    layer.layer_version,
                    layer.object_order_version,
                    tuple(
                        (
                            obj.object_id,
                            obj.geometry_version,
                            obj.style_version,
                            obj.transform_version,
                        )
                        for obj in layer.objects
                    ),
                )
                for layer in layers
            ),
            preview.obj.geometry_version if preview is not None else None,
        )
    )
    return VectorSceneSnapshot(
        scene_version=scene_version,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        layers=tuple(layers),
        preview=preview,
    )


def snapshot_vector_object(
    vector_object: Any,
    *,
    layer_kind: str = "draw",
    is_preview: bool = False,
) -> VectorObjectSnapshot | None:
    """Convert one live ``VectorObject`` into an immutable snapshot.

    Returns ``None`` for entries that do not expose the vector object
    contract (no ``paths``); malformed coordinates are kept as-is and
    rejected later by the geometry compile stage (spec §8.1).
    """

    raw_paths = getattr(vector_object, "paths", None)
    if not isinstance(raw_paths, Sequence) or isinstance(raw_paths, (str, bytes)):
        return None

    meta = getattr(vector_object, "meta", None)
    meta = meta if isinstance(meta, Mapping) else {}
    path_ids = _stable_path_ids(meta)
    paths = tuple(
        _snapshot_path(path, path_ids[index] if index < len(path_ids) else "")
        for index, path in enumerate(raw_paths)
    )
    style = _snapshot_style(getattr(vector_object, "style", None))
    text_payload = meta.get(_TEXT_META_KEY)
    object_kind = "text" if isinstance(text_payload, Mapping) else str(layer_kind)
    text_snapshot = None
    if isinstance(text_payload, Mapping):
        # TextLayoutAdapter (spec §12.2): resolved glyph runs when the direct
        # path supports every feature the object uses, else None (raster
        # fallback per spec §12.5).
        text_snapshot = build_text_object_snapshot(
            str(getattr(vector_object, "id", "") or ""),
            dict(text_payload),
        )
    geometry_version = stable_signature(
        tuple(
            (
                tuple((anchor.pos, anchor.kind, anchor.h_in, anchor.h_out) for anchor in p.anchors),
                p.closed,
            )
            for p in paths
        )
    )
    return VectorObjectSnapshot(
        object_id=str(getattr(vector_object, "id", "") or ""),
        object_kind=object_kind,
        paths=paths,
        style=style,
        transform=IDENTITY_TRANSFORM,
        geometry_version=geometry_version,
        style_version=stable_signature(
            (
                style.stroke_color,
                style.stroke_width,
                style.fill_enabled,
                style.fill_color,
                style.join,
                style.cap,
                style.miter_limit,
                style.dash,
            )
        ),
        transform_version=stable_signature(IDENTITY_TRANSFORM),
        resource_version=0,
        metadata_signature=stable_signature(meta),
        is_preview=bool(is_preview or getattr(vector_object, "preview", False)),
        text=text_snapshot,
        has_variable_width=_has_variable_width(meta, style),
    )


def _snapshot_layer(layer: Any, parent_id: str | None, preview=None) -> VectorRenderLayerSnapshot:
    from .japanese_text.story_preview import preview_objects  # noqa: PLC0415

    vector_kind = str(getattr(layer, "vector_kind", "draw") or "draw")
    objects: list[VectorObjectSnapshot] = []
    for vector_object in preview_objects(getattr(layer, "vector_objects", []) or [], preview):
        snapshot = snapshot_vector_object(vector_object, layer_kind=vector_kind)
        if snapshot is not None:
            objects.append(snapshot)

    blend_mode = getattr(layer, "blend_mode", "normal")
    blend_name = str(getattr(blend_mode, "value", blend_mode) or "normal")
    return VectorRenderLayerSnapshot(
        layer_id=str(getattr(layer, "id", "") or ""),
        vector_kind=vector_kind,
        visible=bool(getattr(layer, "visible", True)),
        opacity=float(getattr(layer, "opacity", 1.0)),
        blend_mode=blend_name,
        parent_id=parent_id,
        clipping=bool(getattr(layer, "clipping", False)),
        layer_transform=_effective_layer_transform(layer),
        layer_version=int(getattr(layer, "texture_version", 0) or 0),
        object_order_version=stable_signature(tuple(obj.object_id for obj in objects)),
        mask_ref=_mask_ref(layer),
        effect_refs=_filter_refs(layer),
        objects=tuple(objects),
    )


def _snapshot_preview(canvas: Any) -> VectorPreviewSnapshot | None:
    preview_obj = getattr(canvas, "_vector_preview", None)
    if preview_obj is None:
        return None
    snapshot = snapshot_vector_object(preview_obj, is_preview=True)
    if snapshot is None:
        return None
    preview_layer_id = getattr(canvas, "_vector_preview_layer_id", None)
    return VectorPreviewSnapshot(
        layer_id=str(preview_layer_id) if preview_layer_id is not None else None,
        obj=snapshot,
        hidden_object_id=snapshot.object_id or None,
    )


def _snapshot_path(path: Any, path_id: str) -> VectorPathSnapshot:
    anchors = tuple(
        VectorAnchorSnapshot(
            pos=_point(getattr(anchor, "pos", (0.0, 0.0))),
            kind=str(getattr(anchor, "kind", "corner") or "corner"),
            h_in=_optional_point(getattr(anchor, "h_in", None)),
            h_out=_optional_point(getattr(anchor, "h_out", None)),
        )
        for anchor in getattr(path, "anchors", ()) or ()
    )
    return VectorPathSnapshot(
        path_id=path_id,
        anchors=anchors,
        closed=bool(getattr(path, "closed", False)),
    )


def _snapshot_style(style: Any) -> VectorStyleSnapshot:
    if style is None:
        return VectorStyleSnapshot()
    fill_color = getattr(style, "fill_color", None)
    return VectorStyleSnapshot(
        stroke_color=_color(getattr(style, "stroke_color", (0, 0, 0))),
        stroke_width=float(getattr(style, "stroke_width", 1.0)),
        fill_enabled=bool(getattr(style, "fill_enabled", False)),
        fill_color=_color(fill_color) if fill_color is not None else None,
        join=str(getattr(style, "join", "miter") or "miter"),
        cap=str(getattr(style, "cap", "butt") or "butt"),
        miter_limit=float(getattr(style, "miter_limit", 4.0)),
        dash=tuple(float(v) for v in getattr(style, "dash", ()) or ()),
    )


def _has_variable_width(meta: Mapping[str, Any], style: VectorStyleSnapshot) -> bool:
    raw = meta.get(_STROKE_PROFILE_META_KEY)
    if raw is None:
        return False
    profile = StrokeProfile.from_raw(raw, float(style.stroke_width or 1.0))
    return profile is not None and profile.is_variable


def _stable_path_ids(meta: Mapping[str, Any]) -> tuple[str, ...]:
    ids_meta = meta.get(_VECTOR_IDS_META_KEY)
    if not isinstance(ids_meta, Mapping):
        return ()
    raw_paths = ids_meta.get("paths")
    if not isinstance(raw_paths, Sequence):
        return ()
    result: list[str] = []
    for entry in raw_paths:
        raw_id = entry.get("id") if isinstance(entry, Mapping) else None
        result.append(str(raw_id) if isinstance(raw_id, str) else "")
    return tuple(result)


def _effective_layer_transform(layer: Any) -> AffineTuple:
    """Mirror ``WgpuTransformProcessor.compute_draw_region`` positioning.

    A non-identity (preview) transform wins and offsets are ignored;
    otherwise ``offset_x``/``offset_y`` translate the layer (the move tool
    drags vector layers via offsets, see ``basic_move.move_tool``).
    """

    affine = _explicit_affine(layer)
    if affine is not None:
        return affine
    offset_x = float(int(getattr(layer, "offset_x", 0) or 0))
    offset_y = float(int(getattr(layer, "offset_y", 0) or 0))
    if offset_x == 0.0 and offset_y == 0.0:
        return IDENTITY_TRANSFORM
    return (1.0, 0.0, 0.0, 1.0, offset_x, offset_y)


def _explicit_affine(layer: Any) -> AffineTuple | None:
    transform = getattr(layer, "_preview_transform", None) or getattr(layer, "transform", None)
    to_affine = getattr(transform, "to_affine", None)
    if not callable(to_affine):
        return None
    try:
        affine = to_affine()
        values = (
            float(affine.a),
            float(affine.b),
            float(affine.c),
            float(affine.d),
            float(affine.tx),
            float(affine.ty),
        )
    except Exception:
        return None
    # Same identity tolerance as WgpuTransformProcessor._is_identity_affine.
    a, b, c, d, tx, ty = values
    if (
        abs(a - 1.0) <= 1e-8
        and abs(b) <= 1e-8
        and abs(c) <= 1e-8
        and abs(d - 1.0) <= 1e-8
        and abs(tx) <= 1e-8
        and abs(ty) <= 1e-8
    ):
        return None
    return values


def _mask_ref(layer: Any) -> MaskRenderRef | None:
    mask = getattr(layer, "layer_mask", None)
    if mask is None:
        return None
    return MaskRenderRef(kind="pixel", enabled=bool(getattr(layer, "mask_enabled", True)))


def _filter_refs(layer: Any) -> tuple[FilterRenderRef, ...]:
    refs: list[FilterRenderRef] = []
    for child in getattr(layer, "children", []) or []:
        if str(getattr(child, "layer_type", "")) != "effect":
            continue
        filter_type = getattr(child, "filter_type", getattr(child, "effect_type", None))
        if not filter_type:
            continue
        refs.append(
            FilterRenderRef(
                effect_type=str(filter_type),
                visible=bool(getattr(child, "visible", True)),
            )
        )
    return tuple(refs)


_effect_refs = _filter_refs
EffectRenderRef = FilterRenderRef


def _iter_layers_with_parent(
    layers: Sequence[Any],
    parent_id: str | None = None,
) -> list[tuple[Any, str | None]]:
    result: list[tuple[Any, str | None]] = []
    for layer in layers:
        result.append((layer, parent_id))
        children = getattr(layer, "children", None)
        if children:
            result.extend(_iter_layers_with_parent(children, str(getattr(layer, "id", ""))))
    return result


def _point(value: Any) -> tuple[float, float]:
    try:
        return (float(value[0]), float(value[1]))
    except (TypeError, ValueError, IndexError):
        return (0.0, 0.0)


def _optional_point(value: Any) -> tuple[float, float] | None:
    if value is None:
        return None
    return _point(value)


def _color(value: Any) -> tuple[int, int, int]:
    try:
        return (int(value[0]), int(value[1]), int(value[2]))
    except (TypeError, ValueError, IndexError):
        return (0, 0, 0)
