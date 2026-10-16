"""Merge-visible and flatten-all helpers for ``SimpleCanvasService``."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from typing import Any, cast

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter

from loomstroke.core.canvas_blend_service import CanvasBlendService
from loomstroke.core.canvas_layer_query_service import CanvasLayerQueryService
from loomstroke.core.commands import BlendMode
from loomstroke.core.layer_3d_rendering import refresh_3d_layer_preview, render_3d_layer
from loomstroke.core.mask_coverage import mask_coverage_alpha8
from loomstroke.core.native_cmyk.merge_service import NativeCmykMergeService


def _layer_affine(layer: Any) -> tuple[float, float, float, float, float, float] | None:
    """Return the layer's explicit affine, or ``None`` when it is identity.

    Mirrors ``WgpuTransformProcessor._explicit_affine``, which is the正本 for
    these semantics: a live preview transform wins over the committed one, and
    a non-identity affine *replaces* the offset placement rather than adding
    to it.  The GPU version lives in the UI layer and cannot be imported here.
    """

    transform = getattr(layer, "_preview_transform", None)
    if transform is None:
        transform = getattr(layer, "transform", None)
    if transform is None:
        return None
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
    if not all(math.isfinite(value) for value in values):
        return None
    a, b, c, d, tx, ty = values
    is_identity = (
        abs(a - 1.0) <= 1e-8
        and abs(b) <= 1e-8
        and abs(c) <= 1e-8
        and abs(d - 1.0) <= 1e-8
        and abs(tx) <= 1e-8
        and abs(ty) <= 1e-8
    )
    return None if is_identity else values


def _invert_affine(
    values: tuple[float, float, float, float, float, float],
) -> tuple[float, float, float, float, float, float] | None:
    """Mirror of ``WgpuTransformProcessor._inverse_affine``."""

    a, b, c, d, tx, ty = values
    det = a * d - b * c
    if abs(det) <= 1e-10 or not math.isfinite(det):
        return None
    inv_a = d / det
    inv_b = -b / det
    inv_c = -c / det
    inv_d = a / det
    return (inv_a, inv_b, inv_c, inv_d, -(inv_a * tx + inv_b * ty), -(inv_c * tx + inv_d * ty))


def _qimage_to_rgba(image: QImage) -> np.ndarray:
    """Return an ``(h, w, 4)`` view of *image* in its native byte order."""

    source = image
    if source.format() != QImage.Format.Format_ARGB32_Premultiplied:
        source = source.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    height, width = source.height(), source.width()
    bits = source.constBits()
    raw = np.frombuffer(bits, dtype=np.uint8).reshape(height, source.bytesPerLine())
    return raw[:, : width * 4].reshape(height, width, 4).copy()


def _rgba_to_qimage(array: np.ndarray) -> QImage:
    height, width = array.shape[:2]
    contiguous = np.ascontiguousarray(array)
    return QImage(
        contiguous.data,
        width,
        height,
        width * 4,
        QImage.Format.Format_ARGB32_Premultiplied,
    ).copy()


class CanvasMergeFlattenService:
    """Coordinate Stamp Visible / Flatten All without owning canvas state."""

    def __init__(
        self,
        owner: Any,
        *,
        layer_type: type[Any],
        merge_entry_type: type[Any],
        merge_visible_entry_type: type[Any],
        flatten_all_entry_type: type[Any],
        history_group_type: type[Any],
        normal_blend_mode: Any,
        compress_qimage: Callable[[QImage], tuple[bytes, int, int, QImage.Format]],
        decompress_qimage: Callable[[bytes, int, int, QImage.Format], QImage],
        events: Any,
    ) -> None:
        self._owner = owner
        self._layer_type = layer_type
        self._merge_entry_type = merge_entry_type
        self._merge_visible_entry_type = merge_visible_entry_type
        self._flatten_all_entry_type = flatten_all_entry_type
        self._history_group_type = history_group_type
        self._normal_blend_mode = normal_blend_mode
        self._compress_qimage = compress_qimage
        self._decompress_qimage = decompress_qimage
        self._events = events

    def merge_layer_down(self, source_id: str, target_id: str) -> bool:
        """Merge source layer down onto target with undo support."""

        owner = self._owner
        if any(
            getattr(layer, "native_cmyk", None) is not None for layer in owner._flatten_layers()
        ):
            return (
                NativeCmykMergeService.apply(
                    owner, "down", source_id=source_id, target_id=target_id
                )
                is not None
            )
        source = owner._get_layer_by_id(source_id)
        target = owner._get_layer_by_id(target_id)
        if source is None or target is None:
            return False
        if getattr(target, "layer_type", "") == "3d":
            return False
        self._refresh_3d_layer_image(source)
        if source.image is None or target.image is None:
            return False

        source_index = self.find_top_level_layer_index(owner._layers, source_id)
        if source_index < 0:
            return False

        src_data, sw, sh, sfmt = self._compress_qimage(source.image)
        tgt_data, tw, th, tfmt = self._compress_qimage(target.image)
        merge_entry = self._merge_entry_type(
            source_id=source.id,
            source_name=source.name,
            source_index=source_index,
            source_image_data=src_data,
            source_img_w=sw,
            source_img_h=sh,
            source_img_fmt=sfmt,
            source_offset_x=source.offset_x,
            source_offset_y=source.offset_y,
            source_blend_mode=source.blend_mode,
            source_opacity=source.opacity,
            source_visible=source.visible,
            source_locked=source.locked,
            target_id=target.id,
            target_image_before=tgt_data,
            target_img_w=tw,
            target_img_h=th,
            target_img_fmt=tfmt,
            active_index_before=owner._active_index,
            source_layer_type=str(getattr(source, "layer_type", "raster") or "raster"),
            source_vector_kind=str(getattr(source, "vector_kind", "draw") or "draw"),
            source_clipping=bool(getattr(source, "clipping", False)),
            source_alpha_lock=bool(getattr(source, "alpha_lock", False)),
            source_origin=getattr(source, "origin", None),
            source_scene_3d=getattr(source, "scene_3d", None),
            source_camera_3d=getattr(source, "camera_3d", None),
            source_lighting_3d=getattr(source, "lighting_3d", None),
            source_display_mode_3d=str(getattr(source, "display_mode_3d", "solid") or "solid"),
            source_gen_3d=getattr(source, "gen_3d", None),
            metadata={
                "source_layer_type": str(getattr(source, "layer_type", "raster") or "raster"),
            },
        )

        self.do_merge_forward(merge_entry)
        owner._recompose()

        group = self._history_group_type(label="Merge Down", entries=[merge_entry])
        owner._push_history_group(group)

        owner._publish_layer_event(
            self._events.LAYER_REMOVED,
            layer=source,
            change_type="removed",
        )
        return True

    def restore_merge(self, entry: Any, *, use_after: bool) -> None:
        """Undo or redo a merge-down operation."""

        if use_after:
            self.do_merge_forward(entry)
        else:
            self.do_merge_undo(entry)

    def do_merge_undo(self, entry: Any) -> None:
        """Undo a merge: restore target image and re-insert source layer."""

        owner = self._owner
        target = owner._get_layer_by_id(entry.target_id)
        if target is not None:
            target.image = self._decompress_qimage(
                entry.target_image_before,
                entry.target_img_w,
                entry.target_img_h,
                entry.target_img_fmt,
            )
            target.texture_version += 1

        src_image = self._decompress_qimage(
            entry.source_image_data,
            entry.source_img_w,
            entry.source_img_h,
            entry.source_img_fmt,
        )
        src_layer = self._layer_type(
            id=entry.source_id,
            name=entry.source_name,
            image=src_image,
            offset_x=entry.source_offset_x,
            offset_y=entry.source_offset_y,
            blend_mode=entry.source_blend_mode,
            opacity=entry.source_opacity,
            visible=entry.source_visible,
            locked=entry.source_locked,
            alpha_lock=bool(getattr(entry, "source_alpha_lock", False)),
            layer_type=str(getattr(entry, "source_layer_type", "raster") or "raster"),
            vector_kind=str(getattr(entry, "source_vector_kind", "draw") or "draw"),
            clipping=bool(getattr(entry, "source_clipping", False)),
        )
        src_layer.origin = getattr(entry, "source_origin", None)
        src_layer.scene_3d = getattr(entry, "source_scene_3d", None)
        src_layer.camera_3d = getattr(entry, "source_camera_3d", None)
        src_layer.lighting_3d = getattr(entry, "source_lighting_3d", None)
        src_layer.display_mode_3d = str(
            getattr(entry, "source_display_mode_3d", "solid") or "solid"
        )
        src_layer.gen_3d = getattr(entry, "source_gen_3d", None)
        idx = min(entry.source_index, len(owner._layers))
        owner._layers.insert(idx, src_layer)
        owner._active_index = entry.active_index_before
        owner._invalidate_layer_cache()
        owner._publish_layer_event(
            self._events.LAYER_ADDED,
            layer=src_layer,
            change_type="added",
        )

    def do_merge_forward(self, entry: Any) -> None:
        """Redo a merge: apply merge and remove source layer."""

        owner = self._owner
        source = owner._get_layer_by_id(entry.source_id)
        target = owner._get_layer_by_id(entry.target_id)
        if source is None or target is None:
            return
        if getattr(target, "layer_type", "") == "3d":
            return
        self._refresh_3d_layer_image(source)
        if source.image is not None and target.image is not None:
            if owner._needs_custom_blend(source.blend_mode):
                tw, th = target.image.width(), target.image.height()
                tmp = QImage(tw, th, QImage.Format.Format_ARGB32_Premultiplied)
                tmp.fill(Qt.GlobalColor.transparent)
                painter = QPainter(tmp)
                dx = source.offset_x - int(getattr(target, "offset_x", 0))
                dy = source.offset_y - int(getattr(target, "offset_y", 0))
                painter.drawImage(dx, dy, source.image)
                painter.end()
                owner._custom_blend(target.image, tmp, source.blend_mode, source.opacity)
            else:
                mode = owner._get_composition_mode(source.blend_mode)
                painter = QPainter(target.image)
                painter.setCompositionMode(mode)
                painter.setOpacity(source.opacity)
                dx = source.offset_x - int(getattr(target, "offset_x", 0))
                dy = source.offset_y - int(getattr(target, "offset_y", 0))
                painter.drawImage(dx, dy, source.image)
                painter.end()
            target.texture_version += 1
        result = owner._find_parent(entry.source_id)
        if result is not None:
            parent_list, idx = result
            parent_list.pop(idx)
            if parent_list is owner._layers and owner._active_index >= len(owner._layers):
                owner._active_index = len(owner._layers) - 1
        owner._invalidate_layer_cache()
        owner._publish_layer_event(
            self._events.LAYER_REMOVED,
            layer=source,
            change_type="removed",
        )

    def merge_visible_layers(self) -> str | None:
        """Stamp visible layers into a new raster layer."""

        owner = self._owner
        if any(
            getattr(layer, "native_cmyk", None) is not None for layer in owner._flatten_layers()
        ):
            return NativeCmykMergeService.apply(owner, "stamp")
        visible = self._collect_visible_content_layers()
        if len(visible) < 2:
            return None

        result_img = self.composite_visible(visible)
        result_index = len(owner._layers)
        result_id = f"layer-merged-{time.time_ns()}"
        result_name = "Merged"
        r_data, rw, rh, rfmt = self._compress_qimage(result_img)
        entry = self._merge_visible_entry_type(
            result_id=result_id,
            result_name=result_name,
            result_image_data=r_data,
            result_img_w=rw,
            result_img_h=rh,
            result_img_fmt=rfmt,
            result_index=result_index,
            active_index_before=owner._active_index,
            active_layer_id_before=owner._active_layer_id or "",
        )

        result_layer = self._layer_type(
            id=result_id,
            name=result_name,
            image=result_img,
            visible=True,
            opacity=1.0,
            blend_mode=self._normal_blend_mode,
        )
        owner._layers.append(result_layer)
        owner._active_index = len(owner._layers) - 1
        owner._active_layer_id = result_id

        owner._recompose()
        owner._invalidate_layer_cache()
        group = self._history_group_type(label="Merge Visible", entries=[entry])
        owner._push_history_group(group)
        owner._publish_layer_event(
            self._events.LAYER_ADDED,
            layer=result_layer,
            change_type="added",
        )
        return result_id

    def flatten_all_layers(self) -> str | None:
        """Flatten all visible content layers into a new raster layer."""

        owner = self._owner
        if any(
            getattr(layer, "native_cmyk", None) is not None for layer in owner._flatten_layers()
        ):
            return NativeCmykMergeService.apply(owner, "flatten")
        visible = self._collect_visible_content_layers()
        if not visible:
            return None

        # Flatten reproduces the canvas, so it bakes the paper in and gives up
        # transparency — the Photoshop split.  Merge Visible keeps compositing
        # onto transparency because its result is a stamp layered over the
        # originals, which are still there.
        #
        # A single layer goes through the same composite as the rest: the
        # flattened layer sits at offset 0, so its offset, transform, mask and
        # opacity have to be baked in rather than dropped.
        result_img = self.composite_visible_over_background(visible)

        result_index = len(owner._layers)
        result_id = f"layer-flat-{time.time_ns()}"
        result_name = "Flattened"
        r_data, rw, rh, rfmt = self._compress_qimage(result_img)
        hidden_layer_ids = [ly.id for _, ly in visible]
        entry = self._flatten_all_entry_type(
            result_id=result_id,
            result_name=result_name,
            result_image_data=r_data,
            result_img_w=rw,
            result_img_h=rh,
            result_img_fmt=rfmt,
            result_index=result_index,
            active_index_before=owner._active_index,
            active_layer_id_before=owner._active_layer_id or "",
            hidden_layer_ids=hidden_layer_ids,
        )

        for _, ly in visible:
            ly.visible = False

        result_layer = self._layer_type(
            id=result_id,
            name=result_name,
            image=result_img,
            visible=True,
            opacity=1.0,
            blend_mode=self._normal_blend_mode,
        )
        owner._layers.append(result_layer)
        owner._active_index = len(owner._layers) - 1
        owner._active_layer_id = result_id

        owner._recompose()
        owner._invalidate_layer_cache()
        group = self._history_group_type(label="Flatten All", entries=[entry])
        owner._push_history_group(group)
        owner._publish_layer_event(
            self._events.LAYER_ADDED,
            layer=result_layer,
            change_type="added",
        )
        return result_id

    def bake_3d_layer_snapshot(self, layer_id: str, *, name: str | None = None) -> str | None:
        """Add the current 3D layer view as a new raster layer."""

        owner = self._owner
        layer = owner._get_layer_by_id(layer_id)
        if layer is None or getattr(layer, "layer_type", "") != "3d":
            return None
        if not self._refresh_3d_layer_image(layer) or layer.image is None:
            return None

        result_name = name or f"{layer.name} Snapshot"
        result_id, _entry = self._append_raster_result_layer(
            image=layer.image.copy(),
            name=result_name,
            label="Bake 3D Layer",
            metadata={"source": "3d_layer_bake", "source_layer_id": layer.id},
        )
        return result_id

    def render_3d_layer_maps(
        self,
        layer_id: str,
        *,
        width: int | None = None,
        height: int | None = None,
    ) -> Any | None:
        """Return depth/normal/outline maps for a 3D layer without mutating layers."""

        owner = self._owner
        layer = owner._get_layer_by_id(layer_id)
        if layer is None or getattr(layer, "layer_type", "") != "3d":
            return None
        result = render_3d_layer(
            layer,
            width=width or int(owner._width),
            height=height or int(owner._height),
        )
        if result is None:
            return None
        return result.maps

    def bake_3d_controlnet_guides(
        self,
        layer_id: str,
        *,
        kinds: Sequence[str] = ("depth", "normal", "outline"),
        name_prefix: str | None = None,
    ) -> dict[str, str]:
        """Bake 3D guide maps into new raster layers for ComfyUI ControlNet input."""

        owner = self._owner
        layer = owner._get_layer_by_id(layer_id)
        if layer is None or getattr(layer, "layer_type", "") != "3d":
            return {}
        maps = self.render_3d_layer_maps(layer_id)
        if maps is None:
            return {}

        prefix = name_prefix or layer.name
        entries: list[Any] = []
        layer_ids: dict[str, str] = {}
        for kind in kinds:
            normalized = str(kind).strip().lower()
            image = self._guide_image(maps, normalized)
            if image is None:
                continue
            result_id, entry = self._append_raster_result_layer(
                image=image,
                name=f"{prefix} {self._guide_label(normalized)}",
                label=None,
                metadata={
                    "source": "3d_controlnet_guide",
                    "source_layer_id": layer.id,
                    "guide_kind": normalized,
                },
                push_history=False,
            )
            layer_ids[normalized] = result_id
            entries.append(entry)

        if entries:
            group = self._history_group_type(label="3D ControlNet Guides", entries=entries)
            owner._push_history_group(group)
        return layer_ids

    def composite_visible(self, visible: list[tuple[int, Any]]) -> QImage:
        """Composite visible layers with clipping mask and effect support."""

        return self._compose_sequence(self._blank_canvas(), visible)

    def composite_visible_over_background(self, visible: list[tuple[int, Any]]) -> QImage:
        """Composite *visible* onto the canvas background, as the canvas does.

        The paper is not a layer — the compositor clears its target to
        ``canvas_state.background`` before drawing anything — so a stack
        composited onto transparency does not reproduce what the user sees.
        Anything blending with what is below it (multiply, overlay, screen,
        difference, ...) lands on a different colour entirely.
        """

        result = self._blank_canvas()
        background = getattr(self._owner, "_background", None)
        if background is not None and background.alpha() > 0:
            painter = QPainter(result)
            painter.fillRect(0, 0, self._owner._width, self._owner._height, background)
            painter.end()
        return self._compose_sequence(result, visible)

    def _compose_sequence(self, result: QImage, visible: list[tuple[int, Any]]) -> QImage:
        """Composite one sibling list onto *result* and return the outcome.

        Shared by the top-level stack and each group's children so both obey
        the same clipping, effect, and blend rules.
        """

        owner = self._owner
        i = 0
        while i < len(visible):
            idx, layer = visible[i]

            if layer.layer_type == "effect" and layer.filter_type:
                result = self.apply_filter_to_image(result, layer)
                i += 1
                continue

            group: list[tuple[int, Any]] = [(idx, layer)]
            j = i + 1
            while j < len(visible):
                _next_idx, next_ly = visible[j]
                if not next_ly.clipping:
                    break
                group.append((_next_idx, next_ly))
                j += 1

            if len(group) == 1:
                self.paint_layer_onto(result, layer)
            else:
                temp = self._blank_canvas()
                self.paint_layer_onto(temp, layer)
                for _, clipped in group[1:]:
                    clipped_img = self.layer_canvas_image(clipped)
                    if clipped_img is None:
                        continue
                    clip_mode = CanvasBlendService.normalize_blend_mode(clipped.blend_mode)
                    if clip_mode in (BlendMode.NORMAL, BlendMode.PASS_THROUGH):
                        painter = QPainter(temp)
                        painter.setCompositionMode(
                            QPainter.CompositionMode.CompositionMode_SourceAtop
                        )
                        painter.setOpacity(clipped.opacity)
                        painter.drawImage(0, 0, clipped_img)
                        painter.end()
                    else:
                        # Blend-mode color math with clipping coverage kept:
                        # the wgpu compositor's atop composite is the parity
                        # counterpart.
                        CanvasBlendService.custom_blend_atop(
                            temp, clipped_img, clip_mode, clipped.opacity
                        )
                owner._custom_blend(result, temp, layer.blend_mode, 1.0)
            i = j
        return result

    def _blank_canvas(self) -> QImage:
        owner = self._owner
        image = QImage(owner._width, owner._height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        return image

    def layer_canvas_image(self, layer: Any) -> QImage | None:
        """Return *layer*'s contribution in canvas space.

        Applies, in the order the wgpu compositor does: the layer's own
        content (group children composited, or image/vector plus child
        effects), its transform, then its layer mask.  Blend mode and opacity
        stay with the caller because a clipping group applies them once for
        the whole group.
        """

        if not getattr(layer, "visible", True):
            return None

        if str(getattr(layer, "layer_type", "raster")) == "group":
            children = CanvasLayerQueryService.collect_visible_content_layers(
                getattr(layer, "children", []) or []
            )
            if not children:
                return None
            canvas_image = self._compose_sequence(self._blank_canvas(), children)
        else:
            source = self.get_filtered_image(layer)
            if source is None:
                return None
            canvas_image = self._place_on_canvas(layer, source)

        self._apply_layer_mask(canvas_image, layer)
        return canvas_image

    def _place_on_canvas(self, layer: Any, source: QImage) -> QImage:
        """Draw a layer-local image into canvas space, honouring its transform."""

        affine = _layer_affine(layer)
        if affine is not None:
            return self._place_with_affine(source, affine)
        canvas_image = self._blank_canvas()
        painter = QPainter(canvas_image)
        painter.drawImage(int(layer.offset_x), int(layer.offset_y), source)
        painter.end()
        return canvas_image

    def _place_with_affine(
        self,
        source: QImage,
        affine: tuple[float, float, float, float, float, float],
    ) -> QImage:
        """Resample *source* through *affine* exactly the way the shader does.

        Not ``QPainter.setTransform`` — Qt and the blend shader disagree about
        which destination pixels a fractional offset covers, which shifts the
        whole layer by one pixel.  The shader maps each destination pixel
        *centre* back through the inverse affine and samples nearest (the
        compositor's sampler is ``nearest``/``nearest``), so this walks the
        same path: bilinear here would also merge a blurrier layer than the
        one on screen.
        """

        inverse = _invert_affine(affine)
        canvas_image = self._blank_canvas()
        if inverse is None:
            return canvas_image
        width, height = self._owner._width, self._owner._height
        source_rgba = _qimage_to_rgba(source)
        source_h, source_w = source_rgba.shape[:2]

        inv_a, inv_b, inv_c, inv_d, inv_tx, inv_ty = inverse
        centres_y, centres_x = np.mgrid[0:height, 0:width]
        pixel_x = centres_x + 0.5
        pixel_y = centres_y + 0.5
        local_x = inv_a * pixel_x + inv_b * pixel_y + inv_tx
        local_y = inv_c * pixel_x + inv_d * pixel_y + inv_ty

        # The shader's nearest sample resolves to ``ceil(local) - 1``, not
        # ``floor``: a source texel spans ``(n, n+1]``.  For any offset that is
        # not an exact half-pixel the two agree, but on a boundary ``floor``
        # picks the next texel over — shifting the whole layer one pixel
        # against the canvas and duplicating the far edge column.
        #
        # An offset of exactly half a pixel puts *every* destination pixel on a
        # texel boundary, and the compositor resolves that tie by f32 rounding
        # that varies with the canvas size, so the two can still land a pixel
        # apart there.  Nothing to match: the tie has no stable answer.
        texel_x = np.ceil(local_x).astype(np.int64) - 1
        texel_y = np.ceil(local_y).astype(np.int64) - 1
        inside = (texel_x >= 0) & (texel_y >= 0) & (local_x <= source_w) & (local_y <= source_h)
        texel_x = np.clip(texel_x, 0, source_w - 1)
        texel_y = np.clip(texel_y, 0, source_h - 1)

        out = np.zeros((height, width, 4), dtype=np.uint8)
        out[inside] = source_rgba[texel_y[inside], texel_x[inside]]
        return _rgba_to_qimage(out)

    @staticmethod
    def _apply_layer_mask(canvas_image: QImage, layer: Any) -> None:
        mask = getattr(layer, "layer_mask", None)
        if mask is None or mask.isNull() or not bool(getattr(layer, "mask_enabled", True)):
            return
        painter = QPainter(canvas_image)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        painter.drawImage(0, 0, mask_coverage_alpha8(mask))
        painter.end()

    def get_filtered_image(self, layer: Any) -> QImage | None:
        """Return layer image with child effects applied."""

        if getattr(layer, "layer_type", "") == "3d":
            self._refresh_3d_layer_image(layer)

        if layer.vector_objects:
            img = cast("QImage", self._owner._rasterize_vector_layer(layer))
        elif layer.image is not None:
            img = cast("QImage", layer.image)
        else:
            return None
        effects = [
            child
            for child in layer.children
            if child.layer_type == "effect" and child.visible and child.filter_type
        ]
        if not effects:
            return img
        img = img.copy()
        origin = (int(getattr(layer, "offset_x", 0) or 0), int(getattr(layer, "offset_y", 0) or 0))
        for child in effects:
            img = self.apply_filter_to_image(img, child, layer_origin=origin)
        return img

    def apply_filter_to_image(
        self,
        source: QImage,
        filter_layer: Any,
        *,
        layer_origin: tuple[int, int] = (0, 0),
    ) -> QImage:
        """Apply a single effect layer to *source* via the owner effect pipeline."""

        return cast(
            "QImage",
            (
                getattr(self._owner, "_filter_apply", None) or self._owner._effect_apply
            )().apply_filter_to_image(source, filter_layer, layer_origin=layer_origin),
        )

    get_effected_image = get_filtered_image

    def apply_effect_to_image(self, source: QImage, effect_layer: Any, **context: Any) -> QImage:
        return self.apply_filter_to_image(source, effect_layer, **context)

    def paint_layer_onto(self, target: QImage, layer: Any) -> None:
        """Paint a single layer onto *target* respecting blend mode and effects."""

        src = self.layer_canvas_image(layer)
        if src is None:
            return
        self._owner._custom_blend(target, src, layer.blend_mode, layer.opacity)

    def restore_merge_visible(self, entry: Any, *, use_after: bool) -> None:
        if use_after:
            self.do_merge_visible_forward(entry)
        else:
            self.do_merge_visible_undo(entry)

    def do_merge_visible_undo(self, entry: Any) -> None:
        """Undo stamp-visible: remove the stamped layer."""

        owner = self._owner
        result = owner._find_parent(entry.result_id)
        if result is not None:
            parent_list, idx = result
            removed = parent_list.pop(idx)
            owner._active_index = entry.active_index_before
            owner._active_layer_id = entry.active_layer_id_before or None
            owner._invalidate_layer_cache()
            owner._publish_layer_event(
                self._events.LAYER_REMOVED,
                layer=removed,
                change_type="removed",
            )

    def do_merge_visible_forward(self, entry: Any) -> None:
        """Redo stamp-visible: re-insert the stamped layer."""

        owner = self._owner
        result_img = self._decompress_qimage(
            entry.result_image_data,
            entry.result_img_w,
            entry.result_img_h,
            entry.result_img_fmt,
        )
        result_layer = self._layer_type(
            id=entry.result_id,
            name=entry.result_name,
            image=result_img,
            visible=True,
            opacity=1.0,
            blend_mode=self._normal_blend_mode,
        )
        ins = min(entry.result_index, len(owner._layers))
        owner._layers.insert(ins, result_layer)
        owner._active_index = ins
        owner._active_layer_id = entry.result_id
        owner._invalidate_layer_cache()
        owner._publish_layer_event(
            self._events.LAYER_ADDED,
            layer=result_layer,
            change_type="added",
        )

    def do_flatten_all_undo(self, entry: Any) -> None:
        """Undo flatten-all: remove the flattened layer and unhide originals."""

        owner = self._owner
        result = owner._find_parent(entry.result_id)
        if result is not None:
            parent_list, idx = result
            removed = parent_list.pop(idx)
            owner._active_index = entry.active_index_before
            owner._active_layer_id = entry.active_layer_id_before or None
            for layer_id in entry.hidden_layer_ids:
                found = owner._find_parent(layer_id)
                if found is not None:
                    siblings, sib_idx = found
                    siblings[sib_idx].visible = True
            owner._invalidate_layer_cache()
            owner._publish_layer_event(
                self._events.LAYER_REMOVED,
                layer=removed,
                change_type="removed",
            )

    def do_flatten_all_forward(self, entry: Any) -> None:
        """Redo flatten-all: re-insert the flattened layer and re-hide originals."""

        owner = self._owner
        result_img = self._decompress_qimage(
            entry.result_image_data,
            entry.result_img_w,
            entry.result_img_h,
            entry.result_img_fmt,
        )
        result_layer = self._layer_type(
            id=entry.result_id,
            name=entry.result_name,
            image=result_img,
            visible=True,
            opacity=1.0,
            blend_mode=self._normal_blend_mode,
        )
        for layer_id in entry.hidden_layer_ids:
            found = owner._find_parent(layer_id)
            if found is not None:
                siblings, sib_idx = found
                siblings[sib_idx].visible = False
        ins = min(entry.result_index, len(owner._layers))
        owner._layers.insert(ins, result_layer)
        owner._active_index = ins
        owner._active_layer_id = entry.result_id
        owner._invalidate_layer_cache()
        owner._publish_layer_event(
            self._events.LAYER_ADDED,
            layer=result_layer,
            change_type="added",
        )

    @staticmethod
    def find_top_level_layer_index(layers: Sequence[Any], layer_id: str) -> int:
        """Return the top-level index for *layer_id*, or ``-1`` when absent."""

        for i, layer in enumerate(layers):
            if layer.id == layer_id:
                return i
        return -1

    @staticmethod
    def collect_visible_content_layers(layers: Sequence[Any]) -> list[tuple[int, Any]]:
        """Return visible layers that contribute content to merge/flatten."""

        return CanvasLayerQueryService.collect_visible_content_layers(layers)

    def _collect_visible_content_layers(self) -> list[tuple[int, Any]]:
        return self.collect_visible_content_layers(self._owner._layers)

    def _append_raster_result_layer(
        self,
        *,
        image: QImage,
        name: str,
        label: str | None,
        metadata: dict[str, Any],
        push_history: bool = True,
    ) -> tuple[str, Any]:
        owner = self._owner
        result_img = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        result_index = len(owner._layers)
        result_id = f"layer-3d-bake-{time.time_ns()}"
        r_data, rw, rh, rfmt = self._compress_qimage(result_img)
        entry = self._merge_visible_entry_type(
            result_id=result_id,
            result_name=name,
            result_image_data=r_data,
            result_img_w=rw,
            result_img_h=rh,
            result_img_fmt=rfmt,
            result_index=result_index,
            active_index_before=owner._active_index,
            active_layer_id_before=owner._active_layer_id or "",
            metadata=dict(metadata),
        )

        result_layer = self._layer_type(
            id=result_id,
            name=name,
            image=result_img,
            visible=True,
            opacity=1.0,
            blend_mode=self._normal_blend_mode,
        )
        owner._layers.append(result_layer)
        owner._active_index = len(owner._layers) - 1
        owner._active_layer_id = result_id
        owner._recompose()
        owner._invalidate_layer_cache()
        owner._publish_layer_event(
            self._events.LAYER_ADDED,
            layer=result_layer,
            change_type="added",
        )

        if push_history and label:
            group = self._history_group_type(label=label, entries=[entry])
            owner._push_history_group(group)
        return result_id, entry

    def _refresh_3d_layer_image(self, layer: Any) -> bool:
        if getattr(layer, "layer_type", "") != "3d":
            return True
        return refresh_3d_layer_preview(
            layer,
            width=int(self._owner._width),
            height=int(self._owner._height),
        )

    @staticmethod
    def _guide_image(maps: Any, kind: str) -> QImage | None:
        if kind == "outline":
            image = getattr(maps, "outline", None)
        elif kind in {"depth", "normal", "segmentation"}:
            image = getattr(maps, kind, None)
        else:
            return None
        if isinstance(image, QImage) and not image.isNull():
            return image.copy()
        return None

    @staticmethod
    def _guide_label(kind: str) -> str:
        return {
            "depth": "Depth Guide",
            "normal": "Normal Guide",
            "outline": "Outline Guide",
            "segmentation": "Segmentation Guide",
        }.get(kind, f"{kind.title()} Guide")
