"""Canvas service ImageApi mixin."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, TypeAlias, cast

from numpy.typing import NDArray
from PySide6.QtCore import QRect
from PySide6.QtGui import (
    QImage,
    QPainter,
)

from loomstroke.core.canvas_apply_image_service import CanvasApplyImageService
from loomstroke.core.canvas_geometry_service import CanvasGeometryService
from loomstroke.core.canvas_history_entries import (
    _FlattenAllEntry,
    _MergeEntry,
    _MergeVisibleEntry,
    _ResizeSnapshot,
)
from loomstroke.core.canvas_layer_model import _Layer
from loomstroke.core.commands import (
    ApplyImageCommand,
)
from loomstroke.core.events import DocumentChangeEvent
from loomstroke.core.image_size.resample_snapshot import ResampleSnapshot
from loomstroke.host_api import LayerRef

logger = logging.getLogger(__name__)
_PEN_LAG_DIAG = os.environ.get("LOOMSTROKE_PEN_LAG_DIAG", "").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
LayerValue: TypeAlias = LayerRef | Mapping[str, int] | bool | str | int | None


if TYPE_CHECKING:
    from loomstroke.core.canvas_service_simple_accessors import CanvasServiceAccessorsMixin

    class _CanvasServiceImageApiBase(CanvasServiceAccessorsMixin):
        _width: int
        _height: int
        _dpi: int
        _tile_size: int
        _max_layers: int
        _layers: list[_Layer]
        _selection_mask: QImage | None

        def _find_layer_by_id_tree(
            self, layer_id: str, root: list[_Layer] | None = None
        ) -> _Layer | None: ...

        def history_undo(self) -> bool: ...

else:

    class _CanvasServiceImageApiBase:
        pass


class CanvasServiceImageApiMixin(_CanvasServiceImageApiBase):
    def update_canvas_config(
        self,
        *,
        width: int | None = None,
        height: int | None = None,
        dpi: int | None = None,
        tile_size: int | None = None,
        max_layers: int | None = None,
        anchor: tuple[float, float] | None = None,
    ) -> dict[str, Any]:
        """Apply runtime canvas configuration updates."""
        applied: dict[str, Any] = {}

        new_width = self._width if width is None else max(1, int(width))
        new_height = self._height if height is None else max(1, int(height))

        if new_width != self._width or new_height != self._height:
            self._resize_canvas(new_width, new_height, anchor=anchor)
            applied["width"] = self._width
            applied["height"] = self._height

        if dpi is not None:
            self._dpi = max(1, int(dpi))
            applied["dpi"] = self._dpi

        if tile_size is not None:
            self._tile_size = max(1, int(tile_size))
            applied["tile_size"] = self._tile_size

        if max_layers is not None:
            self._max_layers = max(1, int(max_layers))
            applied["max_layers"] = self._max_layers
            if len(self._layers) > self._max_layers:
                logger.warning(
                    "Current layer count %s exceeds max_layers=%s",
                    len(self._layers),
                    self._max_layers,
                )

        return applied

    def set_color_profile(self, profile: Any) -> bool:
        """Assign the document's working ICC profile (CT-09 / CT-11 "Assign").

        Metadata only — pixels are untouched.  ``profile`` is an
        ``io.file_io.ColorProfile`` (with bytes) or ``None`` for untagged.
        Returns ``True`` when the profile changed; the change marks the
        document dirty through a ``color_profile`` document-changed event.
        """
        if profile is not None and not hasattr(profile, "data"):
            raise TypeError("color profile must be a ColorProfile or None")
        current = getattr(self, "color_profile", None)
        if current == profile:
            return False
        self.color_profile = profile
        if getattr(self, "_event_bus", None):
            emit = getattr(self, "_emit_doc_changed", None)
            if callable(emit):
                emit(
                    DocumentChangeEvent(
                        change_type="color_profile",
                        affected_area=None,
                        layer_id=None,
                    )
                )
        return True

    def flip_image(
        self,
        *,
        horizontal: bool = False,
        vertical: bool = False,
    ) -> bool:
        """Flip the entire canvas (all layers) horizontally and/or vertically.

        Canvas dimensions are preserved.  Each layer's rasterized image is
        mirrored in place via :meth:`QImage.mirrored`, and layer offsets are
        adjusted so that layers smaller than the canvas end up at the
        mirrored location.  Vector objects (including text frames) are
        rebuilt with transformed anchor positions and handles so shape /
        vector / text layers are mirrored in the same operation.

        Records a single history group so undo/redo restore all layers
        atomically.

        Args:
            horizontal: Mirror left/right.
            vertical: Mirror top/bottom.

        Returns:
            ``True`` if the flip was applied, ``False`` if both axes were
            false or there was nothing to flip.
        """
        return self._geometry().flip_image(horizontal=horizontal, vertical=vertical)

    def rotate_image(self, *, angle: int) -> bool:
        """Rotate the entire canvas (all layers) by 90°, -90° or 180°.

        For 90° / -90° the canvas dimensions are swapped; for 180° they are
        preserved.  Each layer's rasterized image is rotated via
        :class:`QTransform` and the layer offsets are transformed so that
        layers smaller than the canvas land at the correct rotated
        position.  Vector objects (including text frames) are rebuilt with
        transformed anchor positions and handles so shape / vector / text
        layers rotate in the same operation.

        Records a single history group so undo/redo restore all layers
        atomically.

        Args:
            angle: Rotation angle in degrees.  Only 90, -90 and 180 are
                supported.  Positive values rotate clockwise; negative
                values rotate counter-clockwise.

        Returns:
            ``True`` if rotated, ``False`` if angle is unsupported or
            there are no layers.
        """
        return self._geometry().rotate_image(angle=angle)

    def resample_canvas(
        self,
        *,
        new_width: int,
        new_height: int,
        dpi: int | None = None,
        mode: str = "bicubic",
    ) -> bool:
        """Scale the whole canvas to new pixel dimensions (Image Size, CT-13).

        Unlike :meth:`update_canvas_config` / ``_resize_canvas`` (which crop
        or extend at an anchor), every layer raster, layer / effect mask,
        vector and text object, layer transform and the selection are
        interpolated so the composite image is scaled to the new size.
        ``dpi`` optionally replaces the document DPI; passing the current
        size with a new ``dpi`` records an undoable DPI-only change.

        Args:
            new_width: Target pixel width.
            new_height: Target pixel height.
            dpi: Optional new DPI metadata (``None`` keeps the current DPI).
            mode: Interpolation id — ``nearest`` / ``bilinear`` / ``bicubic``
                / ``lanczos`` / ``hamming`` / ``box``.

        Returns:
            ``True`` on success (or when nothing changed), ``False`` for an
            invalid size, DPI or interpolation id.
        """
        return self._resample().resample(
            new_width=new_width,
            new_height=new_height,
            dpi=dpi,
            mode=mode,
        )

    def _take_resize_snapshot(self) -> _ResizeSnapshot:
        """Capture current canvas size and all layer images for undo/redo."""
        return cast("_ResizeSnapshot", self._geometry().take_resize_snapshot())

    def _restore_resize_snapshot(self, snapshot: _ResizeSnapshot) -> None:
        """Restore canvas state from a resize (or Image Size) snapshot."""
        if isinstance(snapshot, ResampleSnapshot):
            self._resample().restore_snapshot(snapshot)
            return
        self._geometry().restore_resize_snapshot(snapshot)

    def _restore_merge(self, entry: _MergeEntry, *, use_after: bool) -> None:
        """Undo or redo a merge-down operation."""
        self._merge_flatten().restore_merge(entry, use_after=use_after)

    def _do_merge_undo(self, entry: _MergeEntry) -> None:
        """Undo a merge: restore target image and re-insert source layer."""
        self._merge_flatten().do_merge_undo(entry)

    def _do_merge_forward(self, entry: _MergeEntry) -> None:
        """Redo a merge: apply merge and remove source layer."""
        self._merge_flatten().do_merge_forward(entry)

    def merge_layer_down(self, source_id: str, target_id: str) -> bool:
        """Merge source layer down onto target with undo support.

        Args:
            source_id: ID of the layer to merge (will be removed).
            target_id: ID of the layer to merge onto.

        Returns:
            True on success.
        """
        return self._merge_flatten().merge_layer_down(source_id, target_id)

    def merge_visible_layers(self) -> str | None:
        """Stamp Visible — composite all visible layers into a new layer.

        All original layers are preserved.  A new merged raster layer is
        inserted at the top of the stack.  Clipping masks are respected
        during compositing.

        Returns:
            The stamped layer id on success, ``None`` if fewer than two
            visible layers with images exist.
        """
        return self._merge_flatten().merge_visible_layers()

    def _composite_visible(self, visible: list[tuple[int, _Layer]]) -> QImage:
        """Composite visible layers with clipping mask and effect support.

        Layers with ``clipping=True`` are clipped to the alpha channel
        of the nearest non-clipping layer below them (the "base").
        Standalone effect layers (``layer_type == "effect"``) are applied
        to the accumulated result.  Child effect layers on each raster
        layer are applied via the CPU effect pipeline before compositing.
        """
        return self._merge_flatten().composite_visible(visible)

    def _get_filtered_image(self, layer: _Layer) -> QImage | None:
        """Return layer image with child effects applied (CPU pipeline)."""
        return self._merge_flatten().get_filtered_image(layer)

    def _apply_filter_to_image(self, source: QImage, filter_layer: _Layer) -> QImage:
        """Apply a single effect layer to *source* via CPU effect registry."""
        return self._merge_flatten().apply_filter_to_image(source, filter_layer)

    _get_effected_image = _get_filtered_image

    def _apply_effect_to_image(self, source: QImage, effect_layer: _Layer) -> QImage:
        return self._apply_filter_to_image(source, effect_layer)

    def _paint_layer_onto(self, target: QImage, layer: _Layer) -> None:
        """Paint a single layer onto *target* respecting blend mode and effects."""
        self._merge_flatten().paint_layer_onto(target, layer)

    def flatten_all_layers(self) -> str | None:
        """Flatten All — composite all visible layers and discard originals.

        All layers are replaced by a single merged raster layer.
        Hidden layers are discarded.

        Returns:
            The flattened layer id on success, ``None`` on failure.
        """
        return self._merge_flatten().flatten_all_layers()

    def bake_3d_layer_snapshot(self, layer_id: str, *, name: str | None = None) -> str | None:
        """Add the current 3D layer view as a new raster layer."""
        return self._merge_flatten().bake_3d_layer_snapshot(layer_id, name=name)

    def render_3d_layer_maps(
        self,
        layer_id: str,
        *,
        width: int | None = None,
        height: int | None = None,
    ) -> Any | None:
        """Render depth/normal/outline maps for a 3D layer."""
        return self._merge_flatten().render_3d_layer_maps(layer_id, width=width, height=height)

    def bake_3d_controlnet_guides(
        self,
        layer_id: str,
        *,
        kinds: Sequence[str] = ("depth", "normal", "outline"),
        name_prefix: str | None = None,
    ) -> dict[str, str]:
        """Add 3D guide maps as raster layers for ComfyUI ControlNet input."""
        return self._merge_flatten().bake_3d_controlnet_guides(
            layer_id,
            kinds=kinds,
            name_prefix=name_prefix,
        )

    def _restore_merge_visible(self, entry: _MergeVisibleEntry, *, use_after: bool) -> None:
        self._merge_flatten().restore_merge_visible(entry, use_after=use_after)

    def _do_merge_visible_undo(self, entry: _MergeVisibleEntry) -> None:
        """Undo stamp-visible: remove the stamped layer."""
        self._merge_flatten().do_merge_visible_undo(entry)

    def _do_merge_visible_forward(self, entry: _MergeVisibleEntry) -> None:
        """Redo stamp-visible: re-insert the stamped layer at top."""
        self._merge_flatten().do_merge_visible_forward(entry)

    def _do_flatten_all_undo(self, entry: _FlattenAllEntry) -> None:
        """Undo flatten-all: remove the flattened layer and unhide originals."""
        self._merge_flatten().do_flatten_all_undo(entry)

    def _do_flatten_all_forward(self, entry: _FlattenAllEntry) -> None:
        """Redo flatten-all: re-insert the flattened layer at top and re-hide originals."""
        self._merge_flatten().do_flatten_all_forward(entry)

    def _resize_canvas(
        self,
        width: int,
        height: int,
        *,
        anchor: tuple[float, float] | None = None,
        record_history: bool = True,
    ) -> None:
        """Resize all layer buffers to match a new canvas size."""
        self._geometry().resize_canvas(
            width,
            height,
            anchor=anchor,
            record_history=record_history,
        )

    @staticmethod
    def _normalize_anchor(anchor: tuple[float, float] | None) -> tuple[float, float]:
        return CanvasGeometryService.normalize_anchor(anchor)

    def _shift_vector_objects(
        self,
        objects: list[Any],
        dx: int,
        dy: int,
    ) -> list[Any]:
        return self._geometry().shift_vector_objects(objects, dx, dy)

    def _shift_vector_object(self, obj: Any, dx: int, dy: int) -> Any:
        return self._geometry().shift_vector_object(obj, dx, dy)

    def _shift_vector_anchor(self, anchor: Any, dx: int, dy: int) -> Any:
        return self._geometry().shift_vector_anchor(anchor, dx, dy)

    def _transform_vector_objects_for_canvas(
        self,
        objects: list[Any],
        *,
        mode: str,
        old_w: int,
        old_h: int,
    ) -> list[Any]:
        """Return a new list of vector_objects transformed by a canvas-level
        flip / rotate.  ``mode`` is one of
        ``"flip_h" | "flip_v" | "flip_hv" | "rot90" | "rot-90" | "rot180"``.

        Each VectorObject / VectorPath / Anchor is rebuilt as a new instance
        (frozen dataclasses); originals are left intact so that snapshots
        taken before the transform remain valid for undo.
        """
        return self._geometry().transform_vector_objects_for_canvas(
            objects, mode=mode, old_w=old_w, old_h=old_h
        )

    def _transform_vector_object_for_canvas(
        self,
        obj: Any,
        *,
        mode: str,
        old_w: int,
        old_h: int,
    ) -> Any:
        return self._geometry().transform_vector_object_for_canvas(
            obj, mode=mode, old_w=old_w, old_h=old_h
        )

    def _transform_anchor_for_canvas(
        self,
        anchor: Any,
        *,
        mode: str,
        old_w: int,
        old_h: int,
    ) -> Any:
        return self._geometry().transform_anchor_for_canvas(
            anchor, mode=mode, old_w=old_w, old_h=old_h
        )

    @staticmethod
    def _transform_point_for_canvas(
        pt: tuple[float, float],
        *,
        mode: str,
        old_w: int,
        old_h: int,
    ) -> tuple[float, float]:
        return CanvasGeometryService.transform_point_for_canvas(
            pt, mode=mode, old_w=old_w, old_h=old_h
        )

    @staticmethod
    def _transform_handle_vec(
        v: tuple[float, float] | None,
        *,
        mode: str,
    ) -> tuple[float, float] | None:
        """Transform a bezier handle (relative to its anchor) — direction
        only, no translation."""
        return CanvasGeometryService.transform_handle_vec(v, mode=mode)

    @staticmethod
    def _transform_bbox_for_canvas(
        x: float,
        y: float,
        w: float,
        h: float,
        *,
        mode: str,
        old_w: int,
        old_h: int,
    ) -> tuple[float, float, float, float]:
        return CanvasGeometryService.transform_bbox_for_canvas(
            x, y, w, h, mode=mode, old_w=old_w, old_h=old_h
        )

    def _transform_vector_meta_for_canvas(
        self,
        meta: Any,
        *,
        mode: str,
        old_w: int,
        old_h: int,
    ) -> Any:
        """Transform text_tool vector metadata for canvas-level geometry changes."""
        return self._geometry().transform_vector_meta_for_canvas(
            meta, mode=mode, old_w=old_w, old_h=old_h
        )

    @staticmethod
    def _rebuild_like(obj: Any, updates: dict[str, Any]) -> Any:
        return CanvasGeometryService.rebuild_like(obj, updates)

    def _apply_image_command(self, cmd: ApplyImageCommand) -> bool:
        return self._apply_image_service_ref().apply_image_command(cmd)

    def apply_image(
        self,
        image: NDArray[Any],
        *,
        position: tuple[int, int] = (0, 0),
        blend_mode: str = "normal",
        opacity: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        return self._apply_image_service_ref().apply_image(
            image=image,
            position=position,
            blend_mode=blend_mode,
            opacity=opacity,
            metadata=metadata,
        )

    def _prepare_apply_image_context(
        self,
        *,
        image: NDArray[Any],
        position: tuple[int, int],
        blend_mode: str,
        opacity: float,
        metadata: dict[str, Any] | None,
    ) -> (
        tuple[
            _Layer,
            QImage,
            QRect,
            QRect,
            QRect,
            QRect,
            QImage | None,
            QPainter.CompositionMode,
            float,
            dict[str, Any],
        ]
        | None
    ):
        return cast(
            "tuple[_Layer, QImage, QRect, QRect, QRect, QRect, QImage | None, "
            "QPainter.CompositionMode, float, dict[str, Any]] | None",
            self._apply_image_service_ref().prepare_apply_image_context(
                image=image,
                position=position,
                blend_mode=blend_mode,
                opacity=opacity,
                metadata=metadata,
            ),
        )

    def _prepare_apply_image(
        self,
        *,
        image: NDArray[Any],
        position: tuple[int, int],
        opacity: float,
    ) -> tuple[QImage, QRect, QRect] | None:
        return self._apply_image_service_ref().prepare_apply_image(
            image=image,
            position=position,
            opacity=opacity,
        )

    def _clip_rect_with_selection(
        self,
        clip_rect: QRect,
        selection_bounds: QRect | None,
    ) -> QRect | None:
        return CanvasApplyImageService.clip_rect_with_selection(clip_rect, selection_bounds)

    def _image_apply_layer_context(
        self,
        layer: _Layer,
        clip_rect: QRect,
    ) -> tuple[QRect, QRect] | None:
        return self._apply_image_service_ref().image_apply_layer_context(layer, clip_rect)

    def _paint_selected_image(
        self,
        *,
        layer: _Layer,
        qimage: QImage,
        dest_rect: QRect,
        clip_rect: QRect,
        local_clip: QRect,
        composition_mode: QPainter.CompositionMode,
        opacity: float,
    ) -> None:
        self._apply_image_service_ref().paint_selected_image(
            layer=layer,
            qimage=qimage,
            dest_rect=dest_rect,
            clip_rect=clip_rect,
            local_clip=local_clip,
            composition_mode=composition_mode,
            opacity=opacity,
            selection_mask=self._selection_mask,
        )

    def _paint_image_direct(
        self,
        *,
        layer: _Layer,
        qimage: QImage,
        local_dest: QRect,
        composition_mode: QPainter.CompositionMode,
        opacity: float,
    ) -> None:
        CanvasApplyImageService.paint_image_direct(
            layer=layer,
            qimage=qimage,
            local_dest=local_dest,
            composition_mode=composition_mode,
            opacity=opacity,
        )

    def _log_image_applied(self, *, layer: _Layer, clip_rect: QRect) -> None:
        CanvasApplyImageService.log_image_applied(layer=layer, clip_rect=clip_rect)

    def undo_image(
        self,
        *,
        position: tuple[int, int] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Compatibility shim that reuses the global undo stack."""

        _ = position, metadata  # Unused for now; retained for API compatibility
        return self.history_undo()

    def _resolve_target_layer(self, metadata: dict[str, Any] | None) -> _Layer | None:
        return cast("_Layer | None", self._apply_image_service_ref().resolve_target_layer(metadata))

    def _get_layer_by_id(self, layer_id: str) -> _Layer | None:
        return self._find_layer_by_id_tree(layer_id)

    @staticmethod
    def _layer_offset(layer: _Layer) -> tuple[int, int]:
        return int(layer.offset_x), int(layer.offset_y)

    def _numpy_to_qimage(self, image: NDArray[Any], opacity: float) -> QImage | None:
        return self._apply_image_service_ref().numpy_to_qimage(image, opacity)
