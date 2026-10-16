"""Apply / undo Image Size on a ``SimpleCanvasService`` (CT-13 R2/R3).

The service owns the whole transaction: validate, snapshot, scale every
layer (raster, offsets, transforms, layer / filter masks, vector objects),
the selection and the DPI, invalidate caches, notify the renderer and push
one history group.  ``restore_snapshot`` is the undo / redo counterpart and
is reached through the canvas facade from the shared resize-entry dispatch.
"""

from __future__ import annotations

import copy
import logging
from contextlib import suppress
from typing import Any

from PySide6.QtGui import QImage

from loomstroke.core.canvas_composited_pixmap_state_service import (
    CanvasCompositedPixmapStateService,
)
from loomstroke.core.canvas_history_entries import (
    _compress_qimage,
    _decompress_qimage,
    _HistoryGroup,
    _ResizeEntry,
)
from loomstroke.core.command_types import LayerTransform
from loomstroke.core.events import DocumentChangeEvent
from loomstroke.core.native_cmyk.geometry import NativeCmykGeometry

from .image_size_planner import ImageSizePlanner
from .interpolation import InterpolationMode
from .metadata_resample_plan import prepare_metadata
from .raster_resample_service import RasterResampleService
from .resample_snapshot import MaskSnapshot, ResampleSnapshot, SelectionSnapshot
from .vector_geometry_scale_service import VectorGeometryScaleService

logger = logging.getLogger(__name__)

#: ``(width, height, dpi, interpolation)`` after validation.
_Request = tuple[int, int, int, InterpolationMode]


def _filter_mask_attribute(layer: Any) -> str:
    """Choose canonical storage, with support for legacy-only layer objects."""
    return (
        "effect_mask"
        if not hasattr(layer, "filter_mask") and hasattr(layer, "effect_mask")
        else "filter_mask"
    )


class CanvasResampleService:
    """Resample all canvas content to a new pixel size and/or DPI, undoably."""

    HISTORY_LABEL = "Image Size"

    def __init__(
        self,
        canvas: Any,
        *,
        raster: RasterResampleService | None = None,
        vectors: VectorGeometryScaleService | None = None,
        planner: ImageSizePlanner | None = None,
    ) -> None:
        self._canvas = canvas
        self._raster = raster or RasterResampleService()
        self._vectors = vectors or VectorGeometryScaleService()
        self._planner = planner or ImageSizePlanner()

    # -- public ----------------------------------------------------------------

    def resample(
        self,
        *,
        new_width: int,
        new_height: int,
        dpi: int | None = None,
        mode: str | InterpolationMode = InterpolationMode.BICUBIC,
    ) -> bool:
        """Scale the canvas to ``new_width`` x ``new_height`` (and set *dpi*).

        Returns ``True`` when applied or when there was nothing to change,
        ``False`` for invalid input (size, DPI or unknown kernel).  A no-op
        records no history; a DPI-only change records a history group too.
        """
        request = self._normalize_request(new_width, new_height, dpi, mode)
        if request is None:
            return False
        width, height, new_dpi, interpolation = request
        canvas = self._canvas
        old_width = int(canvas._width)
        old_height = int(canvas._height)
        old_dpi = int(getattr(canvas, "_dpi", 96) or 96)
        size_changed = (width, height) != (old_width, old_height)
        dpi_changed = new_dpi != old_dpi
        if not size_changed and not dpi_changed:
            return True

        if size_changed:
            scale_x, scale_y = self._planner.scale_factors((old_width, old_height), (width, height))
            try:
                from ..comic_panels.canvas_geometry_guard import (  # noqa: PLC0415
                    require_supported_canvas_geometry,
                )

                require_supported_canvas_geometry(canvas, "image resample")
                prepared = prepare_metadata(canvas, self._vectors, scale_x, scale_y, interpolation)
                from ..comic_panels.resample_metadata import (  # noqa: PLC0415
                    validate_prepared_resample,
                )

                validate_prepared_resample(canvas, prepared, scale_x, scale_y)
            except (ValueError, TypeError, KeyError, IndexError):
                logger.exception("Cannot resample canvas metadata; document left unchanged")
                return False
        before = self.take_snapshot()
        if size_changed:
            self._apply_scale(width, height, scale_x, scale_y, interpolation, prepared)
        canvas._dpi = new_dpi
        if size_changed:
            self._finish(width, height, change_type="resize")
        else:
            self._emit_document_changed("dpi", width, height)
        after = self.take_snapshot()
        self._push_history(before, after)
        logger.info(
            "Canvas resampled from %sx%s@%s to %sx%s@%s (%s)",
            old_width,
            old_height,
            old_dpi,
            width,
            height,
            new_dpi,
            interpolation.value,
        )
        return True

    def take_snapshot(self) -> ResampleSnapshot:
        """Capture size, DPI, every layer buffer / mask / transform and the selection."""
        canvas = self._canvas
        layer_images: list[tuple[str, bytes, int, int, int, int]] = []
        layer_vectors: dict[str, list[Any]] = {}
        layer_masks: list[MaskSnapshot] = []
        filter_masks: list[MaskSnapshot] = []
        transforms: dict[str, Any] = {}
        image_formats: dict[str, QImage.Format] = {}
        for layer in canvas._flatten_layers():
            image = getattr(layer, "image", None)
            if image is not None:
                data, w, h, image_format = _compress_qimage(image)
                image_formats[layer.id] = image_format
                layer_images.append(
                    (layer.id, data, w, h, int(layer.offset_x), int(layer.offset_y))
                )
            if getattr(layer, "vector_objects", None):
                layer_vectors[layer.id] = list(layer.vector_objects)
            mask = getattr(layer, "layer_mask", None)
            if mask is not None:
                layer_masks.append((layer.id, *_compress_qimage(mask)))
            filter_mask = getattr(layer, "filter_mask", getattr(layer, "effect_mask", None))
            if filter_mask is not None:
                filter_masks.append((layer.id, *_compress_qimage(filter_mask)))
            transform = getattr(layer, "transform", None)
            if transform is not None:
                transforms[layer.id] = transform
        return ResampleSnapshot(
            width=int(canvas._width),
            height=int(canvas._height),
            layer_images=layer_images,
            layer_vectors=layer_vectors,
            dpi=int(getattr(canvas, "_dpi", 96) or 96),
            layer_masks=layer_masks,
            effect_masks=filter_masks,
            layer_transforms=transforms,
            image_formats=image_formats,
            selection=self._selection_snapshot(),
            layer_metadata={
                layer.id: copy.deepcopy(getattr(layer, "metadata", None))
                for layer in canvas._flatten_layers()
            },
            native_layers=NativeCmykGeometry.capture(canvas._flatten_layers()),
        )

    def restore_snapshot(self, snapshot: ResampleSnapshot) -> None:
        """Undo / redo: put every captured value back and refresh the renderer."""
        canvas = self._canvas
        canvas._width = int(snapshot.width)
        canvas._height = int(snapshot.height)
        canvas._dpi = int(snapshot.dpi)

        images = {item[0]: item[1:] for item in snapshot.layer_images}
        masks = {item[0]: item[1:] for item in snapshot.layer_masks}
        filter_masks = {item[0]: item[1:] for item in snapshot.filter_masks}
        for layer in canvas._flatten_layers():
            image_entry = images.get(layer.id)
            NativeCmykGeometry.restore(layer, snapshot)
            if layer.id in snapshot.layer_metadata:
                layer.metadata = copy.deepcopy(snapshot.layer_metadata[layer.id])
            if image_entry is not None:
                data, w, h, offset_x, offset_y = image_entry
                layer.image = _decompress_qimage(
                    data,
                    w,
                    h,
                    snapshot.image_formats.get(layer.id, QImage.Format.Format_ARGB32_Premultiplied),
                )
                layer.offset_x = int(offset_x)
                layer.offset_y = int(offset_y)
            vectors = snapshot.layer_vectors.get(layer.id)
            if vectors is not None:
                layer.vector_objects = list(vectors)
            elif getattr(layer, "vector_objects", None):
                layer.vector_objects = []
            self._restore_mask(layer, "layer_mask", masks.get(layer.id))
            if self._restore_mask(layer, _filter_mask_attribute(layer), filter_masks.get(layer.id)):
                self._bump_filter_mask_version(layer)
            transform = snapshot.layer_transforms.get(layer.id)
            if transform is not None and transform != getattr(layer, "transform", None):
                layer.transform = transform
                self._bump_transform_version(layer)
            self._clear_filter_cache(layer)
            layer.texture_version += 1
            self._mark_mask_dirty(layer)

        self._restore_selection(snapshot.selection)
        self._finish(int(snapshot.width), int(snapshot.height), change_type="resize")

    # -- validation -------------------------------------------------------------

    def _normalize_request(
        self,
        new_width: Any,
        new_height: Any,
        dpi: Any,
        mode: Any,
    ) -> _Request | None:
        """Parse and validate the facade arguments; ``None`` (logged) when invalid."""
        interpolation = InterpolationMode.normalize(mode)
        if interpolation is None:
            logger.warning("Ignoring resample with unknown interpolation mode %r", mode)
            return None
        try:
            width = int(new_width)
            height = int(new_height)
        except (TypeError, ValueError):
            logger.warning("Ignoring resample to non-integer size %r x %r", new_width, new_height)
            return None
        issues = self._planner.validate_dimensions(width, height)
        if issues:
            logger.warning("Ignoring resample to invalid size %sx%s (%s)", width, height, issues)
            return None
        if dpi is None:
            new_dpi = int(getattr(self._canvas, "_dpi", 96) or 96)
        else:
            try:
                new_dpi = int(dpi)
            except (TypeError, ValueError):
                new_dpi = 0
            if self._planner.validate_dpi(new_dpi):
                logger.warning("Ignoring resample with invalid dpi %r", dpi)
                return None
        return width, height, new_dpi, interpolation

    # -- apply ------------------------------------------------------------------

    def _apply_scale(
        self,
        width: int,
        height: int,
        scale_x: float,
        scale_y: float,
        mode: InterpolationMode,
        prepared: list[Any],
    ) -> None:
        canvas = self._canvas
        old_width = int(canvas._width)
        old_height = int(canvas._height)
        for layer, metadata, objects in prepared:
            self._scale_layer(
                layer,
                old_size=(old_width, old_height),
                new_size=(width, height),
                scale_x=scale_x,
                scale_y=scale_y,
                mode=mode,
            )
            if metadata is not None:
                layer.metadata = metadata
            layer.vector_objects = objects
        selection = getattr(canvas, "_selection_mask", None)
        canvas._width = width
        canvas._height = height
        if selection is not None and not selection.isNull():
            resized = self._raster.resample_alpha8(selection, width, height, mode)
            canvas.set_selection_mask(resized, None)
        canvas._selection_preview_image = None
        canvas._selection_preview_bounds = None
        if getattr(canvas, "_vector_selection", None) is not None:
            canvas._vector_selection = None

    def _scale_layer(
        self,
        layer: Any,
        *,
        old_size: tuple[int, int],
        new_size: tuple[int, int],
        scale_x: float,
        scale_y: float,
        mode: InterpolationMode,
    ) -> None:
        image = getattr(layer, "image", None)
        if image is not None and not image.isNull():
            offset_x = int(layer.offset_x)
            offset_y = int(layer.offset_y)
            target_w, target_h = self._scaled_buffer_size(
                (image.width(), image.height()),
                (offset_x, offset_y),
                old_size=old_size,
                new_size=new_size,
                scale_x=scale_x,
                scale_y=scale_y,
            )
            layer.image = self._raster.resample_premultiplied(image, target_w, target_h, mode)
            NativeCmykGeometry.resample(layer, target_w, target_h, mode)
            layer.offset_x = self._planner.round_half_up(offset_x * scale_x)
            layer.offset_y = self._planner.round_half_up(offset_y * scale_y)

        mask = getattr(layer, "layer_mask", None)
        if mask is not None and not mask.isNull():
            mask_w, mask_h = self._scaled_buffer_size(
                (mask.width(), mask.height()),
                (0, 0),
                old_size=old_size,
                new_size=new_size,
                scale_x=scale_x,
                scale_y=scale_y,
            )
            from ..comic_panels.resample_metadata import frame_mask_mode  # noqa: PLC0415

            layer.layer_mask = self._raster.resample_coverage_mask(
                mask, mask_w, mask_h, frame_mask_mode(layer, mode)
            )

        filter_mask = getattr(layer, "filter_mask", getattr(layer, "effect_mask", None))
        if filter_mask is not None and not filter_mask.isNull():
            mask_w, mask_h = self._scaled_buffer_size(
                (filter_mask.width(), filter_mask.height()),
                (0, 0),
                old_size=old_size,
                new_size=new_size,
                scale_x=scale_x,
                scale_y=scale_y,
            )
            setattr(
                layer,
                _filter_mask_attribute(layer),
                self._raster.resample_coverage_mask(filter_mask, mask_w, mask_h, mode),
            )
            self._bump_filter_mask_version(layer)

        transform = getattr(layer, "transform", None)
        if transform is not None and transform != LayerTransform.identity():
            layer.transform = LayerTransform(
                tx=float(transform.tx) * scale_x,
                ty=float(transform.ty) * scale_y,
                scale_x=transform.scale_x,
                scale_y=transform.scale_y,
                rotation=transform.rotation,
                origin_x=float(transform.origin_x) * scale_x,
                origin_y=float(transform.origin_y) * scale_y,
            )
            self._bump_transform_version(layer)

        self._clear_filter_cache(layer)
        layer.texture_version += 1
        self._mark_mask_dirty(layer)

    def _scaled_buffer_size(
        self,
        buffer_size: tuple[int, int],
        offset: tuple[int, int],
        *,
        old_size: tuple[int, int],
        new_size: tuple[int, int],
        scale_x: float,
        scale_y: float,
    ) -> tuple[int, int]:
        """Canvas-sized buffers land exactly on the new canvas size; others scale."""
        if buffer_size == old_size and offset == (0, 0):
            return new_size
        return (
            max(1, self._planner.round_half_up(buffer_size[0] * scale_x)),
            max(1, self._planner.round_half_up(buffer_size[1] * scale_y)),
        )

    # -- selection --------------------------------------------------------------

    def _selection_snapshot(self) -> SelectionSnapshot | None:
        canvas = self._canvas
        mask = getattr(canvas, "_selection_mask", None)
        if mask is None or mask.isNull():
            return None
        bounds = getattr(canvas, "_selection_bounds", None)
        if bounds is None:
            bounds = canvas._selection_bounds_from_mask(mask)
        if bounds is None:
            return None
        data, w, h, fmt = _compress_qimage(mask)
        return (data, w, h, fmt, (bounds.x(), bounds.y(), bounds.width(), bounds.height()))

    def _restore_selection(self, selection: SelectionSnapshot | None) -> None:
        canvas = self._canvas
        if selection is None:
            canvas.set_selection_mask(None, None)
        else:
            data, w, h, fmt, bounds = selection
            canvas.set_selection_mask(_decompress_qimage(data, w, h, fmt), bounds)
        canvas._selection_preview_image = None
        canvas._selection_preview_bounds = None
        if getattr(canvas, "_vector_selection", None) is not None:
            canvas._vector_selection = None

    # -- layer bookkeeping -------------------------------------------------------

    @staticmethod
    def _restore_mask(layer: Any, attribute: str, entry: Any) -> bool:
        """Put a captured mask back; returns ``True`` when the attribute changed."""
        if entry is None:
            if getattr(layer, attribute, None) is None:
                return False
            setattr(layer, attribute, None)
            return True
        data, w, h, fmt = entry
        setattr(layer, attribute, _decompress_qimage(data, w, h, fmt))
        return True

    def _clear_filter_cache(self, layer: Any) -> None:
        clear = getattr(
            self._canvas, "_clear_filter_cache", getattr(self._canvas, "_clear_effect_cache", None)
        )
        if callable(clear):
            clear(layer)
        elif getattr(layer, "filter_cache", getattr(layer, "effect_cache", None)) is not None:
            attribute = "filter_cache" if hasattr(layer, "filter_cache") else "effect_cache"
            setattr(layer, attribute, None)

    @staticmethod
    def _bump_filter_mask_version(layer: Any) -> None:
        attribute = (
            "filter_mask_version"
            if hasattr(layer, "filter_mask_version")
            else "effect_mask_version"
        )
        if hasattr(layer, attribute):
            setattr(layer, attribute, int(getattr(layer, attribute, 0)) + 1)

    @staticmethod
    def _bump_transform_version(layer: Any) -> None:
        if hasattr(layer, "transform_version"):
            layer.transform_version = int(getattr(layer, "transform_version", 0)) + 1

    def _mark_mask_dirty(self, layer: Any) -> None:
        if (
            getattr(layer, "layer_mask", None) is None
            and getattr(layer, "filter_mask", getattr(layer, "effect_mask", None)) is None
        ):
            return
        dirty = getattr(self._canvas, "_gpu_mask_dirty_layers", None)
        if isinstance(dirty, set):
            dirty.add(layer.id)

    # -- renderer / history -------------------------------------------------------

    def _finish(self, width: int, height: int, *, change_type: str) -> None:
        canvas = self._canvas
        cache = getattr(canvas, "_qpath_cache", None)
        if cache is not None:
            cache.clear()
        CanvasCompositedPixmapStateService.reset_composited_pixmap(
            canvas, width, height, canvas._background
        )
        canvas._invalidate_layer_cache()
        canvas._recompose()
        self._emit_document_changed(change_type, width, height)

    def _emit_document_changed(self, change_type: str, width: int, height: int) -> None:
        canvas = self._canvas
        if not getattr(canvas, "_event_bus", None):
            return
        with suppress(Exception):
            canvas._emit_doc_changed(
                DocumentChangeEvent(
                    change_type=change_type,
                    affected_area=(0, 0, int(width), int(height)),
                    layer_id=canvas.current_layer().id if canvas._layers else "",
                )
            )

    def _push_history(self, before: ResampleSnapshot, after: ResampleSnapshot) -> None:
        entry = _ResizeEntry(before=before, after=after)
        group = _HistoryGroup(label=self.HISTORY_LABEL, entries=[entry])
        self._canvas._push_history_group(group)

    # Compatibility helpers for existing canvas integrations.
    _clear_effect_cache = _clear_filter_cache
    _bump_effect_mask_version = _bump_filter_mask_version
