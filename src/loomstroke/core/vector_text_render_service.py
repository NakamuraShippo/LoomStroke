"""Vector/text rasterization facade for SimpleCanvasService."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, cast

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter, QPainterPath

from .comic_panels.raster_region import is_comic_record_carrier, uses_comic_raster_region
from .filters.vector_raster_bounds import vector_raster_filter_padding
from .japanese_text.typography import has_typography
from .vector_text_cache import VectorTextCacheManager
from .vector_text_render_primitives import TextObjectRenderContext, TextRenderPrimitives
from .vector_text_selection import HorizontalTextSelectionSpan, TextSelectionRenderPolicy

# Guaranteed wgpu texture-dimension limit; keeps prescaled rasters uploadable
# on every adapter (view-resolution compositing C2).
RASTER_MAX_DIM = 8192


def clamp_raster_scale_to_max_dim(
    scale: int,
    width: int,
    height: int,
    max_dim: int = RASTER_MAX_DIM,
) -> int:
    """Halve a power-of-two prescale until ``extent x scale`` fits ``max_dim``."""

    largest_side = max(1, int(width), int(height))
    result = max(1, int(scale))
    while result > 1 and largest_side * result > max_dim:
        result //= 2
    return result


@dataclass(frozen=True)
class VectorRasterResult:
    """Prescaled raster plus its placement metadata (view compositing C2).

    ``image`` covers the canvas-space rectangle starting at
    ``(origin_x, origin_y)`` with size ``image.size() / scale``; tight
    content regions keep high zoom buckets under the texture size limit.
    """

    image: QImage
    scale: int
    origin_x: int = 0
    origin_y: int = 0


class VectorTextRenderService(TextRenderPrimitives):
    """Coordinate vector/text rasterization and render-only caches."""

    def __init__(self, canvas: Any) -> None:
        self._canvas = canvas
        self._cache = VectorTextCacheManager(canvas)

    def prune_qpath_cache(self, preview_obj: Any | None = None) -> None:
        """Remove QPainterPath cache entries for objects no longer in any layer."""
        self._cache.prune_qpath_cache(preview_obj)

    def rasterize_group_layer(self, layer: Any) -> QImage:
        """Composite a group's visible children onto a transparent canvas image."""
        result = QImage(
            self._canvas._width,
            self._canvas._height,
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        result.fill(Qt.GlobalColor.transparent)
        if layer.layer_type != "group":
            return result
        visible: list[tuple[int, Any]] = []
        for index, child in enumerate(layer.children):
            if not child.visible:
                continue
            if (
                (child.layer_type == "effect" and child.filter_type)
                or child.image is not None
                or child.vector_objects
            ):
                visible.append((index, child))
        if not visible:
            return result
        return cast("QImage", self._canvas._composite_visible(visible))

    def rasterize_vector_layer(
        self,
        layer: Any,
        scale: int = 1,
        region: tuple[int, int, int, int] | None = None,
    ) -> QImage:
        """Rasterize a vector/text layer to a canvas-space QImage.

        ``scale`` > 1 renders at scale-x resolution (view-resolution
        compositing C2); placement stays canvas-space and the compositor
        compensates via the logical texture size.  ``region``
        (x, y, w, h in canvas px) rasterizes only that rectangle so tight
        content bounds keep high buckets under the texture size limit.
        """

        prescale = max(1, int(scale))
        if region is not None:
            region_x, region_y, region_w, region_h = region
        else:
            region_x = region_y = 0
            region_w = int(self._canvas._width)
            region_h = int(self._canvas._height)
        image = QImage(
            region_w * prescale,
            region_h * prescale,
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        image.fill(Qt.GlobalColor.transparent)

        preview_obj = getattr(self._canvas, "_vector_preview", None)
        preview_layer_id = getattr(self._canvas, "_vector_preview_layer_id", None)
        selection = getattr(self._canvas, "_vector_selection", None)
        from .japanese_text.story_preview import preview_objects  # noqa: PLC0415

        objects = preview_objects(layer.vector_objects, preview_obj)
        if preview_obj and (preview_layer_id is None or preview_layer_id == layer.id):
            preview_id = getattr(preview_obj, "id", None)
            objects = [obj for obj in objects if getattr(obj, "id", None) != preview_id]
            objects.append(preview_obj)

        if not objects:
            return image

        self._canvas._qpath_cache.clear()

        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        if prescale != 1:
            painter.scale(prescale, prescale)
        if region_x or region_y:
            painter.translate(-region_x, -region_y)
        self.draw_vector_objects(painter, layer.id, objects, selection)
        painter.end()
        return image

    def rasterize_vector_layer_for_render(  # noqa: PLR0911, PLR0912, PLR0915
        self,
        layer: Any,
        scale: int = 1,
    ) -> QImage | VectorRasterResult | None:
        """Return the layer raster for compositing.

        Ordinary ``scale`` == 1 layers return a canvas-resolution QImage.
        Comic primitives also use tight regions at 1x; non-drawing membership
        carriers return None. ``scale`` > 1 (view compositing C2)
        rasterizes the tight content region at the largest bucket that
        fits the texture limit and returns a :class:`VectorRasterResult`
        (even when clamped back to 1, so callers see the effective scale).
        """

        requested = max(1, int(scale))

        if is_comic_record_carrier(layer):
            # Membership records have no drawing paths and remain in the scene
            # tree, but must not allocate a transparent full-page texture.
            return None

        preview_obj = getattr(self._canvas, "_vector_preview", None)
        preview_layer_id = getattr(self._canvas, "_vector_preview_layer_id", None)
        preview_targets_layer = preview_obj is not None and (
            preview_layer_id is None or preview_layer_id == layer.id
        )
        from .japanese_text.story_preview import preview_objects  # noqa: PLC0415

        projected = preview_objects(layer.vector_objects, preview_obj)
        story_targets_layer = any(
            a is not b for a, b in zip(projected, layer.vector_objects, strict=True)
        )
        selection = getattr(self._canvas, "_vector_selection", None)
        selection_targets_layer = False
        if selection:
            if isinstance(selection, dict):
                raw_layer_id = selection.get("layer_id")
                selection_targets_layer = raw_layer_id is None or str(raw_layer_id) == str(
                    getattr(layer, "id", "")
                )
            else:
                selection_targets_layer = True

        if preview_targets_layer or selection_targets_layer or story_targets_layer:
            # Live-edit bypass: only the layer targeted by the preview /
            # selection re-rasterizes each recompose (a selection used to
            # bypass the cache for EVERY layer, which made typing at 4K
            # re-render the whole document per keystroke).  The raster is
            # limited to the tight content region — including the preview
            # object, so text being typed past the committed bounds is not
            # clipped — which shrinks the per-keystroke cost from a full
            # canvas to the text area.
            extra_objects = (
                (preview_obj,) if preview_targets_layer and preview_obj is not None else ()
            )
            if story_targets_layer:
                extra_objects += tuple(projected)
            region = self._layer_raster_region(layer, extra_objects=extra_objects)
            if region is not None:
                prescale = clamp_raster_scale_to_max_dim(requested, region[2], region[3])
            else:
                prescale = clamp_raster_scale_to_max_dim(
                    requested,
                    int(self._canvas._width),
                    int(self._canvas._height),
                )
            if prescale > 1 or region is not None:
                image = cast(
                    "QImage | None",
                    self._canvas._rasterize_vector_layer(layer, scale=prescale, region=region),
                )
            else:
                image = cast("QImage | None", self._canvas._rasterize_vector_layer(layer))
            if image is None:
                return None
            if requested == 1 and region is None:
                return image
            return VectorRasterResult(
                image=image,
                scale=prescale,
                origin_x=int(region[0]) if region is not None else 0,
                origin_y=int(region[1]) if region is not None else 0,
            )

        prescale = requested
        region = None
        tight_comic = uses_comic_raster_region(layer)
        if requested > 1 or tight_comic:
            region = self._layer_raster_region(layer)
            if region is not None:
                prescale = clamp_raster_scale_to_max_dim(requested, region[2], region[3])
            else:
                prescale = clamp_raster_scale_to_max_dim(
                    requested,
                    int(self._canvas._width),
                    int(self._canvas._height),
                )
            if prescale == 1 and not tight_comic:
                region = None

        origin_x = int(region[0]) if region is not None else 0
        origin_y = int(region[1]) if region is not None else 0
        raster_width = (int(region[2]) if region is not None else self._canvas._width) * prescale
        raster_height = (int(region[3]) if region is not None else self._canvas._height) * prescale

        def rasterize() -> QImage | None:
            # ``scale``/``region`` are passed for tight regions/upscaling so
            # duck-typed single-argument doubles keep working.
            if prescale > 1 or region is not None:
                return cast(
                    "QImage | None",
                    self._canvas._rasterize_vector_layer(layer, scale=prescale, region=region),
                )
            return cast("QImage | None", self._canvas._rasterize_vector_layer(layer))

        def wrap(image: QImage | None) -> QImage | VectorRasterResult | None:
            if image is None or (requested == 1 and region is None):
                return image
            return VectorRasterResult(
                image=image,
                scale=prescale,
                origin_x=origin_x,
                origin_y=origin_y,
            )

        if not (getattr(layer, "vector_objects", []) or []):
            return None

        version = int(getattr(layer, "texture_version", 0))
        cache_key = str(getattr(layer, "id", ""))
        cached = self._canvas._vector_raster_cache.get(cache_key)
        if cached is not None:
            (
                cached_version,
                cached_scale,
                cached_canvas_w,
                cached_canvas_h,
                cached_origin,
                cached_image,
            ) = cached
            if (
                cached_version == version
                and cached_scale == prescale
                and cached_canvas_w == self._canvas._width
                and cached_canvas_h == self._canvas._height
                and cached_origin == (origin_x, origin_y)
                and cached_image.width() == raster_width
                and cached_image.height() == raster_height
            ):
                if requested == 1 and region is None:
                    return cast("QImage | None", cached_image)
                return VectorRasterResult(
                    image=cached_image,
                    scale=prescale,
                    origin_x=cached_origin[0],
                    origin_y=cached_origin[1],
                )

        image = rasterize()
        if image is not None and not image.isNull():
            self._canvas._vector_raster_cache[cache_key] = (
                version,
                prescale,
                self._canvas._width,
                self._canvas._height,
                (origin_x, origin_y),
                image,
            )
        return wrap(image)

    def _layer_raster_region(
        self,
        layer: Any,
        extra_objects: tuple[Any, ...] = (),
    ) -> tuple[int, int, int, int] | None:
        """Tight canvas-space content rectangle for prescaled rasterization.

        ``extra_objects`` (e.g. a live preview object) extend the bounds so
        content being edited past the committed extents is not clipped.
        Returns ``None`` (= full canvas) when any object bound is unknown or
        the layer carries an explicit transform (the compositor's affine
        placement assumes a full-canvas texture).
        """

        if self._layer_has_explicit_transform(layer):
            return None
        if not extra_objects and not vector_raster_filter_padding(layer):
            from .comic_panels.nominal_edge_region import nominal_edge_region  # noqa: PLC0415

            ink = nominal_edge_region(layer, (int(self._canvas._width), int(self._canvas._height)))
            if ink is not None:
                return ink
        objects = list(getattr(layer, "vector_objects", []) or []) + list(extra_objects)
        if not objects:
            return None
        min_x = min_y = math.inf
        max_x = max_y = -math.inf
        for vec in objects:
            bounds = VectorTextRenderService.vector_bbox(vec)
            if bounds is None or not all(math.isfinite(value) for value in bounds):
                return None
            bx0, by0, bx1, by1 = bounds
            payload = VectorTextRenderService.extract_text_payload(vec)
            if payload is not None:
                # Text draws clipped to its (possibly rotated) frame; the
                # circumscribed square covers every rotation angle.
                cx = (bx0 + bx1) * 0.5
                cy = (by0 + by1) * 0.5
                radius = math.hypot(bx1 - bx0, by1 - by0) * 0.5
                bx0, by0, bx1, by1 = cx - radius, cy - radius, cx + radius, cy + radius
                if has_typography(payload):
                    from .japanese_text.typography_painter import (  # noqa: PLC0415
                        japanese_ink_bounds,
                    )

                    ink = japanese_ink_bounds(payload)
                    if not ink.isEmpty():
                        bx0, by0 = min(bx0, ink.left()), min(by0, ink.top())
                        bx1, by1 = max(bx1, ink.right()), max(by1, ink.bottom())
                margin = 4.0
            else:
                style = getattr(vec, "style", None)
                width = max(1.0, float(getattr(style, "stroke_width", 1.0) or 0.0))
                miter = max(1.0, float(getattr(style, "miter_limit", 4.0) or 0.0))
                # Miter tips extend up to miter_limit * width / 2 past the
                # anchor hull; add a little for antialiasing.
                margin = width * max(1.0, miter * 0.5) + 4.0
            min_x = min(min_x, bx0 - margin)
            min_y = min(min_y, by0 - margin)
            max_x = max(max_x, bx1 + margin)
            max_y = max(max_y, by1 + margin)
        effect_padding = vector_raster_filter_padding(layer)
        x0 = max(0, math.floor(min_x - effect_padding))
        y0 = max(0, math.floor(min_y - effect_padding))
        x1 = min(int(self._canvas._width), math.ceil(max_x + effect_padding))
        y1 = min(int(self._canvas._height), math.ceil(max_y + effect_padding))
        if x1 <= x0 or y1 <= y0:
            return None
        return (x0, y0, x1 - x0, y1 - y0)

    @staticmethod
    def _layer_has_explicit_transform(layer: Any) -> bool:
        """Whether the layer carries a non-identity affine transform.

        Mirrors ``WgpuTransformProcessor._explicit_affine``: real layers hold
        an identity ``LayerTransform`` instance by default, which must count
        as "no transform" or tight-region rasterization never activates.
        """

        transform = getattr(layer, "_preview_transform", None)
        if transform is None:
            transform = getattr(layer, "transform", None)
        if transform is None:
            return False
        to_affine = getattr(transform, "to_affine", None)
        if callable(to_affine):
            affine = to_affine()
            values = (
                float(getattr(affine, "a", 1.0)),
                float(getattr(affine, "b", 0.0)),
                float(getattr(affine, "c", 0.0)),
                float(getattr(affine, "d", 1.0)),
                float(getattr(affine, "tx", 0.0)),
                float(getattr(affine, "ty", 0.0)),
            )
        else:
            values = (
                float(getattr(transform, "scale_x", 1.0)),
                0.0,
                0.0,
                float(getattr(transform, "scale_y", 1.0)),
                float(getattr(transform, "tx", 0.0)),
                float(getattr(transform, "ty", 0.0)),
            )
        a, b, c, d, tx, ty = values
        is_identity = (
            abs(a - 1.0) <= 1e-8
            and abs(b) <= 1e-8
            and abs(c) <= 1e-8
            and abs(d - 1.0) <= 1e-8
            and abs(tx) <= 1e-8
            and abs(ty) <= 1e-8
        )
        return not is_identity

    def get_cached_qpaths(self, vec: Any) -> list[QPainterPath]:
        """Return cached QPainterPath list for a VectorObject, building if needed."""
        return self._cache.get_cached_qpaths(vec)


__all__ = (
    "RASTER_MAX_DIM",
    "HorizontalTextSelectionSpan",
    "TextObjectRenderContext",
    "TextSelectionRenderPolicy",
    "VectorRasterResult",
    "VectorTextRenderService",
    "clamp_raster_scale_to_max_dim",
)
