"""Render-scene snapshots consumed by canvas renderers."""

from __future__ import annotations

import math
import os
import time
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtGui import QColor, QImage

from loomstroke.core.appearance import LayerStyleStack
from loomstroke.core.appearance.live_styles import layer_styles, style_signature
from loomstroke.core.canvas_models import (
    CanvasStateSnapshot,
    SelectionStateSnapshot,
    VectorStateSnapshot,
)

from .projection_state import compute_active_flat_idx

_TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}


def render_diagnostics_enabled() -> bool:
    """Return whether extra render timing diagnostics are enabled."""

    return os.environ.get("LOOMSTROKE_RENDER_DIAGNOSTICS", "").strip().lower() in (
        _TRUTHY_ENV_VALUES
    )


class _DirectVectorState:
    """Process-wide toggle mirroring the persisted setting."""

    enabled = False


def set_direct_vector_rendering_enabled(enabled: bool) -> None:
    """Apply the ``performance/direct_vector_rendering_enabled`` setting.

    Called by the settings applier / canvas bootstrap; the environment flag
    below still force-enables the path for development regardless of the
    persisted setting.
    """

    _DirectVectorState.enabled = bool(enabled)


def direct_vector_rendering_enabled() -> bool:
    """Return whether the direct WGPU vector path is enabled.

    True when the ``performance/direct_vector_rendering_enabled`` setting is
    on or when the ``LOOMSTROKE_DIRECT_VECTOR_RENDERING`` dev flag is set
    (spec §27 rollback contract: turning either off routes every vector
    layer back to the compatibility raster path on the next frame).
    """

    if _DirectVectorState.enabled:
        return True
    return os.environ.get("LOOMSTROKE_DIRECT_VECTOR_RENDERING", "").strip().lower() in (
        _TRUTHY_ENV_VALUES
    )


class _ViewCompositingState:
    """Process-wide toggle mirroring the persisted setting."""

    enabled = False


def set_view_resolution_compositing_enabled(enabled: bool) -> None:
    """Apply the ``performance/view_resolution_compositing_enabled`` setting."""

    _ViewCompositingState.enabled = bool(enabled)


def view_resolution_compositing_enabled() -> bool:
    """Return whether view-resolution compositing is enabled (display path).

    Composites at viewport resolution so every direct vector layer stays
    sharp at any zoom (spec: LOOMSTROKE_WGPU_VIEW_RESOLUTION_COMPOSITING_SPEC).
    The ``LOOMSTROKE_VIEW_COMPOSITING`` dev flag force-enables it.
    """

    if _ViewCompositingState.enabled:
        return True
    return os.environ.get("LOOMSTROKE_VIEW_COMPOSITING", "").strip().lower() in (_TRUTHY_ENV_VALUES)


# Sanity ceiling for the raster prescale request; the rasterizer further
# clamps per layer so ``content region x bucket`` fits the texture limit
# (core.vector_text_render_service.clamp_raster_scale_to_max_dim).
VECTOR_RASTER_MAX_BUCKET = 16


def vector_raster_scale_bucket(
    view_scale: float,
    *,
    max_bucket: int = VECTOR_RASTER_MAX_BUCKET,
) -> int:
    """Quantize a view zoom factor into a power-of-two raster prescale.

    Rounds up so text stays sharp until the next bucket.  Zoom-out and
    invalid inputs return 1 (canvas resolution).  The per-layer texture
    size cap is applied later by the rasterizer against the tight content
    region, not here.
    """

    if not math.isfinite(view_scale) or view_scale <= 1.0:
        return 1
    bucket = 1 << max(0, math.ceil(math.log2(view_scale)))
    return max(1, min(bucket, max_bucket))


@dataclass(slots=True)
class RenderLayerSnapshot:
    """Layer input for rendering without exposing the mutable layer tree."""

    id: str
    name: str
    visible: bool
    opacity: float
    blend_mode: Any
    texture_version: int
    transform_version: int = 0
    image: QImage | None = None
    native_cmyk: Any | None = None
    offset_x: int = 0
    offset_y: int = 0
    transform: Any | None = None
    _preview_transform: Any | None = None
    _preview_transform_version: int = 0
    locked: bool = False
    alpha_lock: bool = False
    layer_type: str = "raster"
    vector_kind: str = "draw"
    vector_objects: list[Any] = field(default_factory=list)
    children: list[RenderLayerSnapshot] = field(default_factory=list)
    parent_id: str | None = None
    clipping: bool = False
    layer_mask: QImage | None = None
    mask_enabled: bool = True
    mask_edit_mode: str | None = None
    effect_type: str | None = None
    effect_params: dict[str, Any] = field(default_factory=dict)
    effect_mask: QImage | None = None
    effect_mask_texture_version: int = 0
    ai_effect: Any | None = None
    origin: Any | None = None
    scene_3d: Any | None = None
    camera_3d: Any | None = None
    lighting_3d: Any | None = None
    display_mode_3d: str = "solid"
    gen_3d: Any | None = None
    # Direct WGPU vector rendering (additive; spec §10.1). When
    # ``vector_direct_ready`` is set the layer skips QImage rasterization and
    # ``vector_render_layer`` carries the immutable renderer payload.
    vector_render_layer: Any | None = None
    vector_direct_ready: bool = False
    # View-resolution compositing C2: ``image`` holds the canvas-space
    # rectangle starting at ``vector_raster_origin`` rendered at scale-x
    # resolution; the compositor places it via the logical (unscaled) size
    # so canvas-space math stays untouched.
    vector_raster_scale: int = 1
    vector_raster_origin: tuple[int, int] = (0, 0)
    # Transient preview layers (e.g. the raster-rig prototype) that change
    # only a small region per frame: (x, y, w, h) in texture px lets the
    # texture manager upload just that rect instead of the whole image.
    preview_dirty_rect: tuple[int, int, int, int] | None = None
    mask_nearest: bool = False
    mask_region_eligible: bool = False
    initial_dirty_rect: tuple[int, int, int, int] | None = None
    initial_upload_key: tuple[int, int] | None = None
    style_stack: LayerStyleStack = field(default_factory=LayerStyleStack)
    native_display: Any | None = None

    @classmethod
    def from_layer(
        cls,
        layer: Any,
        *,
        parent_id: str | None = None,
        rasterize_vector: Callable[..., QImage | None] | None = None,
        skip_rasterize_layer_ids: set[str] | frozenset[str] | None = None,
        copy_rasterized_vector: bool = True,
        direct_vector_payloads: dict[str, Any] | None = None,
        vector_raster_scale: int = 1,
    ) -> RenderLayerSnapshot:
        layer_id = str(getattr(layer, "id", ""))
        from loomstroke.core.comic_panels.stroke_upload import (  # noqa: PLC0415
            initial_upload_rect,
        )

        layer_type = str(getattr(layer, "layer_type", "raster"))
        image = getattr(layer, "image", None)
        skip_rasterize = layer_id in (skip_rasterize_layer_ids or frozenset())
        direct_payload = (direct_vector_payloads or {}).get(layer_id)
        styles = deepcopy(layer_styles(layer))
        if styles.enabled():
            # Styled vectors keep editable objects, using their existing raster
            # snapshot as input to the GPU styles in document pixel units.
            direct_payload = None
            skip_rasterize = False
            vector_raster_scale = 1
        raster_scale_used = 1
        raster_origin = (0, 0)
        render_transform = getattr(layer, "transform", None)
        if (direct_payload is not None and layer_type in {"vector", "text"}) or (
            skip_rasterize and layer_type in {"text", "vector"}
        ):
            image = None
        elif rasterize_vector is not None and layer_type in {"text", "vector"}:
            # ``scale`` is passed only when upscaling so duck-typed
            # single-argument rasterize callbacks keep working.  Either call
            # may return a VectorRasterResult (tight-region raster with an
            # origin — e.g. the live-edit bypass) instead of a plain QImage.
            if vector_raster_scale > 1:
                result = rasterize_vector(layer, scale=vector_raster_scale)
            else:
                result = rasterize_vector(layer)
            rasterized = getattr(result, "image", result)
            if result is not None and rasterized is not result:
                render_transform = getattr(result, "render_transform", None) or render_transform
                raster_scale_used = int(getattr(result, "scale", vector_raster_scale))
                raster_origin = (
                    int(getattr(result, "origin_x", 0)),
                    int(getattr(result, "origin_y", 0)),
                )
            elif rasterized is not None and vector_raster_scale > 1:
                raster_scale_used = vector_raster_scale
            image = (
                rasterized.copy()
                if copy_rasterized_vector and rasterized is not None
                else rasterized
            )
        snapshot = cls(
            id=layer_id,
            name=str(getattr(layer, "name", "")),
            visible=bool(getattr(layer, "visible", True)),
            opacity=float(getattr(layer, "opacity", 1.0)),
            blend_mode=getattr(layer, "blend_mode", "normal"),
            texture_version=int(getattr(layer, "texture_version", 0)),
            initial_dirty_rect=initial_upload_rect(layer),
            initial_upload_key=(
                (int(getattr(layer, "texture_version", 0)), image.cacheKey())
                if initial_upload_rect(layer) is not None and image is not None
                else None
            ),
            transform_version=int(getattr(layer, "transform_version", 0)),
            image=image,
            style_stack=styles,
            native_cmyk=getattr(layer, "native_cmyk", None),
            offset_x=int(getattr(layer, "offset_x", 0)),
            offset_y=int(getattr(layer, "offset_y", 0)),
            transform=render_transform,
            _preview_transform=getattr(layer, "_preview_transform", None),
            _preview_transform_version=int(getattr(layer, "_preview_transform_version", 0)),
            locked=bool(getattr(layer, "locked", False)),
            alpha_lock=bool(getattr(layer, "alpha_lock", False)),
            layer_type=layer_type,
            vector_kind=str(getattr(layer, "vector_kind", "draw")),
            vector_objects=list(getattr(layer, "vector_objects", []) or []),
            parent_id=parent_id,
            clipping=bool(getattr(layer, "clipping", False)),
            layer_mask=getattr(layer, "layer_mask", None),
            mask_enabled=bool(getattr(layer, "mask_enabled", True)),
            mask_nearest=bool(
                (getattr(layer, "metadata", None) or {})
                .get("loomstroke.comictools.frame_part", {})
                .get("nearest_mask", False)
            ),
            mask_edit_mode=getattr(layer, "mask_edit_mode", None),
            mask_region_eligible=bool(
                {"loomstroke.comictools.frame_part", "loomstroke.comictools.shared_content"}
                & (getattr(layer, "metadata", None) or {}).keys()
            ),
            effect_type=getattr(layer, "filter_type", getattr(layer, "effect_type", None)),
            effect_params=dict(
                getattr(layer, "filter_params", getattr(layer, "effect_params", {})) or {}
            ),
            effect_mask=getattr(layer, "filter_mask", getattr(layer, "effect_mask", None)),
            effect_mask_texture_version=int(
                getattr(
                    layer,
                    "filter_mask_texture_version",
                    getattr(layer, "effect_mask_texture_version", 0),
                )
                or getattr(layer, "filter_mask_version", getattr(layer, "effect_mask_version", 0))
            ),
            ai_effect=getattr(layer, "ai_effect", None),
            origin=getattr(layer, "origin", None),
            scene_3d=getattr(layer, "scene_3d", None),
            camera_3d=getattr(layer, "camera_3d", None),
            lighting_3d=getattr(layer, "lighting_3d", None),
            display_mode_3d=str(getattr(layer, "display_mode_3d", "solid") or "solid"),
            gen_3d=getattr(layer, "gen_3d", None),
            vector_render_layer=(direct_payload if layer_type in {"vector", "text"} else None),
            vector_direct_ready=(direct_payload is not None and layer_type in {"vector", "text"}),
            vector_raster_scale=raster_scale_used,
            vector_raster_origin=raster_origin,
        )
        snapshot.children = [
            cls.from_layer(
                child,
                parent_id=snapshot.id,
                rasterize_vector=rasterize_vector,
                skip_rasterize_layer_ids=skip_rasterize_layer_ids,
                copy_rasterized_vector=copy_rasterized_vector,
                direct_vector_payloads=direct_vector_payloads,
                vector_raster_scale=vector_raster_scale,
            )
            for child in (getattr(layer, "children", []) or [])
        ]
        return snapshot

    @property
    def filter_type(self) -> str | None:
        return self.effect_type

    @filter_type.setter
    def filter_type(self, value: str | None) -> None:
        self.effect_type = value

    @property
    def filter_params(self) -> dict[str, Any]:
        return self.effect_params

    @filter_params.setter
    def filter_params(self, value: dict[str, Any]) -> None:
        self.effect_params = value

    @property
    def filter_mask(self) -> QImage | None:
        return self.effect_mask

    @filter_mask.setter
    def filter_mask(self, value: QImage | None) -> None:
        self.effect_mask = value

    @property
    def filter_mask_texture_version(self) -> int:
        return self.effect_mask_texture_version

    @filter_mask_texture_version.setter
    def filter_mask_texture_version(self, value: int) -> None:
        self.effect_mask_texture_version = value


@dataclass(frozen=True, slots=True)
class RenderSceneSnapshot:
    """Complete renderer input for one paint pass."""

    canvas_state: CanvasStateSnapshot
    layers: tuple[RenderLayerSnapshot, ...]
    flat_layers: tuple[RenderLayerSnapshot, ...]
    parent_ids: tuple[str | None, ...]
    active_layer_id: str | None
    active_flat_index: int
    selection: SelectionStateSnapshot
    vector: VectorStateSnapshot
    selection_preview_image: QImage | None = None
    selection_preview_bounds: tuple[int, int, int, int] | None = None
    scene_build_ms: float = 0.0
    scene_flatten_ms: float = 0.0
    channel_view: tuple[str, str] | None = None
    frame_preview_source_layers: tuple[RenderLayerSnapshot, ...] | None = None
    native_selection_preview: Any | None = None

    @property
    def width(self) -> int:
        return self.canvas_state.width

    @property
    def height(self) -> int:
        return self.canvas_state.height

    @property
    def background(self) -> QColor:
        return self.canvas_state.background


def build_render_scene_snapshot(
    canvas: Any,
    *,
    rasterize_vector_layers: bool = False,
    include_selection_preview: bool = False,
    skip_rasterize_layer_ids: set[str] | frozenset[str] | None = None,
    vector_raster_scale: int = 1,
    include_frame_preview: bool = False,
) -> RenderSceneSnapshot:
    diagnostics_enabled = render_diagnostics_enabled()
    build_start = time.perf_counter() if diagnostics_enabled else 0.0
    canvas_state = canvas.get_canvas_state_snapshot()
    selection = canvas.get_selection_state_snapshot()
    vector = canvas.get_vector_state_snapshot()
    native_buffer = next(
        (
            layer.native_cmyk
            for layer in getattr(
                canvas, "_flatten_layers", lambda: getattr(canvas, "_layers", ())
            )()
            if getattr(layer, "native_cmyk", None) is not None
        ),
        None,
    )
    native_document = native_buffer is not None
    if native_document:
        rasterize_vector_layers = True
        vector_raster_scale = 1
        skip_rasterize_layer_ids = None
    rasterize_vector: Callable[..., QImage | None] | None = None
    copy_rasterized_vector = True
    if rasterize_vector_layers:
        render_rasterize = getattr(canvas, "_rasterize_vector_layer_for_render", None)
        if callable(render_rasterize):
            rasterize_vector = render_rasterize
            copy_rasterized_vector = False
            if native_document:
                from .native_cmyk_coverage_raster import native_coverage_rasterizer  # noqa: PLC0415

                rasterize_vector = native_coverage_rasterizer(
                    canvas, native_buffer, render_rasterize
                )
        else:
            rasterize_vector = getattr(canvas, "_rasterize_vector_layer", None)
    skipped_ids = set(skip_rasterize_layer_ids or ())
    if rasterize_vector_layers:
        skipped_ids.update(_empty_vector_layer_ids_without_preview(canvas))
    raster_scale = max(1, int(vector_raster_scale))
    if raster_scale > 1 and _scene_requires_bitmap_flatten(
        canvas, include_selection_preview=include_selection_preview
    ):
        # The CPU full-frame flatten draws layer images at canvas
        # coordinates and cannot compensate a prescaled texture.
        raster_scale = 1
    direct_vector_payloads: dict[str, Any] = {}
    if rasterize_vector_layers and direct_vector_rendering_enabled() and not native_document:
        direct_vector_payloads = _collect_direct_vector_payloads(
            canvas,
            exclude_ids=skipped_ids,
            include_selection_preview=include_selection_preview,
        )
    layers = tuple(
        RenderLayerSnapshot.from_layer(
            layer,
            rasterize_vector=rasterize_vector if callable(rasterize_vector) else None,
            skip_rasterize_layer_ids=skipped_ids,
            copy_rasterized_vector=copy_rasterized_vector,
            direct_vector_payloads=direct_vector_payloads or None,
            vector_raster_scale=raster_scale,
        )
        for layer in getattr(canvas, "_layers", [])
    )
    layers = _apply_transient_overrides(canvas, layers)
    frame_preview_source_layers = None
    if include_frame_preview:
        preview = getattr(canvas, "_panel_frame_render_preview", None)
        if preview is not None:
            frame_preview_source_layers = layers
            layers = preview.apply_to_layers(layers, scale=raster_scale)
    flatten_start = time.perf_counter() if diagnostics_enabled else 0.0
    flat_layers, parent_ids = _flatten_layers_with_parents(list(layers))
    flatten_ms = (time.perf_counter() - flatten_start) * 1000.0 if diagnostics_enabled else 0.0
    active_layer_id = canvas_state.active_layer_id or _active_layer_id_from_index(
        layers,
        canvas_state.active_index,
    )
    build_ms = (time.perf_counter() - build_start) * 1000.0 if diagnostics_enabled else 0.0
    return RenderSceneSnapshot(
        canvas_state=canvas_state,
        layers=layers,
        flat_layers=tuple(flat_layers),
        parent_ids=tuple(parent_ids),
        active_layer_id=active_layer_id,
        active_flat_index=compute_active_flat_idx(flat_layers, active_layer_id),
        selection=selection,
        vector=vector,
        selection_preview_image=(
            getattr(canvas, "_selection_preview_image", None) if include_selection_preview else None
        ),
        selection_preview_bounds=(
            getattr(canvas, "_selection_preview_bounds", None)
            if include_selection_preview
            else None
        ),
        scene_build_ms=build_ms,
        scene_flatten_ms=flatten_ms,
        channel_view=getattr(getattr(canvas, "_channels_service", None), "view", None),
        frame_preview_source_layers=frame_preview_source_layers,
        native_selection_preview=getattr(canvas, "_native_selection_preview", None),
    )


def _collect_direct_vector_payloads(
    canvas: Any,
    *,
    exclude_ids: set[str],
    include_selection_preview: bool = False,
) -> dict[str, Any]:
    """Return direct-eligible vector layer payloads keyed by layer id.

    Layers already routed elsewhere (SSO skip ids, empty layers) and layers
    targeted by a live preview keep the compatibility raster path.  When the
    scene would take the compositor's full-frame QImage flatten fallback the
    whole scene stays on the raster path, because the CPU flatten cannot
    redraw a layer that skipped rasterization.
    """

    # Deferred imports: loading wgpu_backend at module import time would form
    # a cycle (wgpu_backend.compositor imports render_scene).
    from loomstroke.core.vector_render_snapshot_builder import (  # noqa: PLC0415
        build_vector_scene_snapshot,
    )
    from loomstroke.ui.canvas.wgpu_backend.vector.eligibility import (  # noqa: PLC0415
        is_direct_fill_eligible_layer,
    )

    # Selection/editing UI is drawn by the screen-space overlay (spec §15),
    # so a selection no longer forces the selected layer onto the raster path.
    # Exception: hide_source_text (text transform/inline flows) hides the
    # committed text while a tool preview draws instead — only the raster
    # path implements that hiding, so keep the targeted layer on it.
    hide_source_layer_id: str | None = None
    selection = getattr(canvas, "_vector_selection", None)
    if isinstance(selection, dict) and selection.get("hide_source_text"):
        raw_hide_layer = selection.get("layer_id")
        if not raw_hide_layer:
            return {}
        hide_source_layer_id = str(raw_hide_layer)
    if _scene_requires_bitmap_flatten(canvas, include_selection_preview=include_selection_preview):
        return {}
    try:
        vector_scene = build_vector_scene_snapshot(canvas)
    except Exception:
        return {}
    payloads: dict[str, Any] = {}
    preview = vector_scene.preview
    for vector_layer in vector_scene.layers:
        if vector_layer.layer_id in exclude_ids:
            continue
        if vector_layer.layer_id == hide_source_layer_id:
            continue
        if preview is not None and preview.layer_id in (None, vector_layer.layer_id):
            continue
        if not is_direct_fill_eligible_layer(vector_layer):
            continue
        payloads[vector_layer.layer_id] = vector_layer
    return payloads


def _scene_requires_bitmap_flatten(canvas: Any, *, include_selection_preview: bool) -> bool:
    """Mirror ``WgpuCompositor._requires_bitmap_flatten`` on the live canvas."""

    from loomstroke.ui.canvas.wgpu_backend.compositor import (  # noqa: PLC0415
        WgpuCompositor,
        is_cpu_only_filter_layer,
    )
    from loomstroke.ui.canvas.wgpu_backend.shader_library import (  # noqa: PLC0415
        WgpuShaderLibrary,
    )

    if (
        include_selection_preview
        and getattr(canvas, "_selection_preview_image", None) is not None
        and getattr(canvas, "_selection_preview_bounds", None) is not None
    ):
        return True

    max_depth = WgpuCompositor.MAX_WGPU_GROUP_DEPTH

    def visit(layer: Any, depth: int) -> bool:
        if not getattr(layer, "visible", True) or float(getattr(layer, "opacity", 1.0)) <= 0.0:
            return False
        children = list(getattr(layer, "children", []) or [])
        if is_cpu_only_filter_layer(layer) or any(
            is_cpu_only_filter_layer(child) for child in children
        ):
            return True
        blend_mode = getattr(layer, "blend_mode", "normal")
        blend_name = str(getattr(blend_mode, "value", blend_mode) or "normal")
        if not WgpuShaderLibrary.is_blend_mode_enabled(blend_name):
            return True
        if (
            str(getattr(layer, "layer_type", "raster")) == "group"
            and children
            and depth >= max_depth
        ):
            return True
        return any(visit(child, depth + 1) for child in children)

    return any(visit(root, 0) for root in getattr(canvas, "_layers", []) or [])


def _empty_vector_layer_ids_without_preview(canvas: Any) -> set[str]:
    preview_obj = getattr(canvas, "_vector_preview", None)
    preview_layer_id = getattr(canvas, "_vector_preview_layer_id", None)
    skipped: set[str] = set()

    def visit(layer: Any) -> None:
        layer_id = str(getattr(layer, "id", ""))
        layer_type = str(getattr(layer, "layer_type", "raster"))
        has_preview = bool(
            preview_obj is not None and (preview_layer_id is None or preview_layer_id == layer_id)
        )
        if (
            layer_type in {"text", "vector"}
            and not (getattr(layer, "vector_objects", []) or [])
            and not has_preview
        ):
            skipped.add(layer_id)
        for child in getattr(layer, "children", []) or []:
            visit(child)

    for root in getattr(canvas, "_layers", []) or []:
        visit(root)
    return skipped


def compute_render_scene_signature(scene: RenderSceneSnapshot) -> tuple[Any, ...]:
    """Return a cheap presenter-level key for frame reuse/stale-frame guards."""

    layer_parts: list[tuple[Any, ...]] = []
    parent_ids = scene.parent_ids
    preview = scene.vector.preview_object
    for index, layer in enumerate(scene.flat_layers):
        parent_id = parent_ids[index] if index < len(parent_ids) else None
        blend_mode = getattr(layer, "blend_mode", "normal")
        blend_name = getattr(blend_mode, "value", blend_mode)
        image = getattr(layer, "image", None)
        image_key = (
            image.cacheKey()
            if image is not None
            and not image.isNull()
            and str(getattr(layer, "layer_type", "raster")) == "raster"
            else 0
        )
        layer_parts.append(
            (
                getattr(layer, "id", ""),
                parent_id,
                str(getattr(layer, "layer_type", "raster")),
                bool(getattr(layer, "visible", True)),
                int(getattr(layer, "texture_version", 0)),
                int(getattr(layer, "transform_version", 0)),
                int(getattr(layer, "_preview_transform_version", 0)),
                int(getattr(layer, "offset_x", 0)),
                int(getattr(layer, "offset_y", 0)),
                round(float(getattr(layer, "opacity", 1.0)), 6),
                str(blend_name),
                bool(getattr(layer, "clipping", False)),
                bool(getattr(layer, "mask_enabled", True)),
                int(
                    getattr(
                        layer,
                        "filter_mask_texture_version",
                        getattr(layer, "effect_mask_texture_version", 0),
                    )
                ),
                image_key,
                style_signature(layer),
                _scene_3d_signature(getattr(layer, "scene_3d", None)),
                _stable_signature(getattr(layer, "camera_3d", None)),
                str(getattr(layer, "display_mode_3d", "solid") or "solid"),
            )
        )
    return (
        scene.width,
        scene.height,
        scene.canvas_state.background_rgba,
        scene.canvas_state.layer_cache_version,
        scene.active_layer_id,
        scene.active_flat_index,
        scene.vector.preview_layer_id,
        getattr(preview, "id", None) if preview is not None else None,
        id(preview) if preview is not None else 0,
        tuple(layer_parts),
    )


def _apply_transient_overrides(
    canvas: Any, layers: tuple[RenderLayerSnapshot, ...]
) -> tuple[RenderLayerSnapshot, ...]:
    """Draw-time rewrite of the layer snapshots by a transient, non-document
    preview (duck-typed object on ``canvas._transient_raster_rig_preview``
    exposing ``apply_to_layers``). The document model is never touched;
    without the attribute the snapshot is returned unchanged."""

    override = getattr(canvas, "_transient_raster_rig_preview", None)
    apply = getattr(override, "apply_to_layers", None) if override is not None else None
    if not callable(apply):
        return layers
    try:
        return tuple(apply(layers))
    except Exception:  # pragma: no cover - a broken preview must not kill rendering
        return layers


def _flatten_layers_with_parents(
    layers: list[RenderLayerSnapshot],
    parent_id: str | None = None,
) -> tuple[list[RenderLayerSnapshot], list[str | None]]:
    flat: list[RenderLayerSnapshot] = []
    parents: list[str | None] = []
    for layer in layers:
        flat.append(layer)
        parents.append(parent_id)
        if layer.children:
            child_flat, child_parents = _flatten_layers_with_parents(layer.children, layer.id)
            flat.extend(child_flat)
            parents.extend(child_parents)
    return flat, parents


def _active_layer_id_from_index(
    layers: tuple[RenderLayerSnapshot, ...],
    active_index: int,
) -> str | None:
    if 0 <= active_index < len(layers):
        return layers[active_index].id
    return None


def _scene_3d_signature(scene: Any) -> tuple[Any, ...] | None:
    if scene is None:
        return None
    meshes = tuple(getattr(scene, "meshes", ()) or ())
    primitive_counts: list[tuple[int, int]] = []
    for mesh in meshes:
        for primitive in getattr(mesh, "primitives", ()) or ():
            vertices = getattr(primitive, "vertices", None)
            indices = getattr(primitive, "indices", None)
            primitive_counts.append(
                (
                    int(getattr(vertices, "size", 0)),
                    int(getattr(indices, "size", 0)),
                )
            )
    return (
        id(scene),
        getattr(scene, "id", None),
        len(meshes),
        tuple(primitive_counts),
    )


def _stable_signature(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple((str(key), _stable_signature(val)) for key, val in sorted(value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_stable_signature(item) for item in value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)
