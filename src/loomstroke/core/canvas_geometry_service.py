"""Canvas geometry operations for SimpleCanvasService."""

from __future__ import annotations

import copy
import logging
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter, QTransform

from loomstroke.core.canvas_composited_pixmap_state_service import (
    CanvasCompositedPixmapStateService,
)

from .canvas_meta_transform_registry import CanvasMetaTransformRegistry
from .events import DocumentChangeEvent
from .native_cmyk.geometry import NativeCmykGeometry

logger = logging.getLogger(__name__)


class CanvasGeometryService:
    """Coordinate resize / flip / rotate operations for SimpleCanvasService."""

    def __init__(
        self,
        canvas: Any,
        *,
        resize_snapshot_type: Callable[..., Any],
        resize_entry_type: Callable[..., Any],
        history_group_type: Callable[..., Any],
        compress_qimage: Callable[[QImage], tuple[bytes, int, int, QImage.Format]],
        decompress_qimage: Callable[[bytes, int, int, QImage.Format], QImage],
    ) -> None:
        self._canvas = canvas
        self._resize_snapshot_type = resize_snapshot_type
        self._resize_entry_type = resize_entry_type
        self._history_group_type = history_group_type
        self._compress_qimage = compress_qimage
        self._decompress_qimage = decompress_qimage

    def flip_image(
        self,
        *,
        horizontal: bool = False,
        vertical: bool = False,
    ) -> bool:
        """Flip the entire canvas horizontally and/or vertically."""
        if not horizontal and not vertical:
            return False
        if not self._canvas._layers:
            return False

        before_snapshot = self.take_resize_snapshot()

        width = self._canvas._width
        height = self._canvas._height

        frame_attributes = self._transform_layer_metadata(
            self._flip_vector_mode(horizontal, vertical)
        )
        if frame_attributes is None:
            return False

        self._apply_flip_to_layers(
            width=width,
            height=height,
            orientations=self._flip_orientations(horizontal, vertical),
            horizontal=horizontal,
            vertical=vertical,
            vec_mode=self._flip_vector_mode(horizontal, vertical),
        )
        from .comic_panels.canvas_transform_plan import (  # noqa: PLC0415
            install_frame_page_attributes,
        )

        install_frame_page_attributes(self._canvas, frame_attributes)
        self._clear_geometry_selection_state()
        self._finish_geometry_mutation(reset_composited=False)

        after_snapshot = self.take_resize_snapshot()
        self._push_resize_history(
            self._flip_history_label(horizontal, vertical),
            before_snapshot,
            after_snapshot,
        )
        self._emit_geometry_changed("transform", 0, 0, width, height)

        logger.info(
            "Canvas flipped (horizontal=%s, vertical=%s)",
            horizontal,
            vertical,
        )
        return True

    def rotate_image(self, *, angle: int) -> bool:
        """Rotate the entire canvas by 90, -90, or 180 degrees."""
        if angle not in (90, -90, 180):
            return False
        if not self._canvas._layers:
            return False

        before_snapshot = self.take_resize_snapshot()

        old_width = self._canvas._width
        old_height = self._canvas._height
        new_width, new_height = self._rotation_dimensions(angle, old_width, old_height)

        frame_attributes = self._transform_layer_metadata(self._rotation_vector_mode(angle))
        if frame_attributes is None:
            return False

        transform = QTransform()
        transform.rotate(angle)

        self._apply_rotation_to_layers(
            angle=angle,
            transform=transform,
            old_width=old_width,
            old_height=old_height,
            vec_mode=self._rotation_vector_mode(angle),
        )
        from .comic_panels.canvas_transform_plan import (  # noqa: PLC0415
            install_frame_page_attributes,
        )

        install_frame_page_attributes(self._canvas, frame_attributes)
        self._canvas._width = new_width
        self._canvas._height = new_height
        self._clear_geometry_selection_state()
        self._reset_composited(new_width, new_height)
        self._finish_geometry_mutation(reset_composited=False)

        after_snapshot = self.take_resize_snapshot()
        self._push_resize_history(
            self._rotation_history_label(angle), before_snapshot, after_snapshot
        )
        self._emit_geometry_changed("transform", 0, 0, new_width, new_height)

        logger.info(
            "Canvas rotated by %s° (new size %sx%s)",
            angle,
            new_width,
            new_height,
        )
        return True

    def _transform_layer_metadata(self, mode: str) -> dict[str, Any] | None:
        registry = CanvasMetaTransformRegistry.shared()
        try:
            from .comic_panels.canvas_geometry_guard import (  # noqa: PLC0415
                require_supported_canvas_geometry,
            )

            require_supported_canvas_geometry(self._canvas, mode)
            from .comic_panels.canvas_transform_plan import (  # noqa: PLC0415
                prepare_frame_page_transform,
            )

            frame_metadata, frame_attributes = prepare_frame_page_transform(self._canvas, mode)
            updates = [
                (
                    layer,
                    registry.transform(
                        getattr(layer, "metadata", None),
                        mode,
                        self._canvas._width,
                        self._canvas._height,
                    ),
                    [
                        registry.transform(
                            getattr(obj, "meta", None),
                            mode,
                            self._canvas._width,
                            self._canvas._height,
                        )
                        for obj in layer.vector_objects
                    ],
                )
                for layer in self._canvas._flatten_layers()
            ]
        except (TypeError, ValueError, KeyError, IndexError):
            logger.exception("Canvas transform rejected by metadata validation")
            return None
        for layer, registered_metadata, registered_objects in updates:
            metadata = frame_metadata.get((layer.id, None), registered_metadata)
            object_metadata = [
                frame_metadata.get((layer.id, obj.id), meta)
                for obj, meta in zip(layer.vector_objects, registered_objects, strict=True)
            ]
            if metadata is not None:
                layer.metadata = metadata
            if any(meta is not None for meta in object_metadata):
                layer.vector_objects = [
                    self.rebuild_like(obj, {"meta": meta}) if meta is not None else obj
                    for obj, meta in zip(layer.vector_objects, object_metadata, strict=True)
                ]
        return frame_attributes

    def _flip_orientations(self, horizontal: bool, vertical: bool) -> Qt.Orientation:
        orientations = Qt.Orientation(0)
        if horizontal:
            orientations |= Qt.Orientation.Horizontal
        if vertical:
            orientations |= Qt.Orientation.Vertical
        return orientations

    def _flip_vector_mode(self, horizontal: bool, vertical: bool) -> str:
        if horizontal and vertical:
            return "flip_hv"
        if horizontal:
            return "flip_h"
        return "flip_v"

    def _flip_history_label(self, horizontal: bool, vertical: bool) -> str:
        if horizontal and vertical:
            return "Flip Canvas"
        if horizontal:
            return "Flip Canvas Horizontal"
        return "Flip Canvas Vertical"

    def _apply_flip_to_layers(
        self,
        *,
        width: int,
        height: int,
        orientations: Qt.Orientation,
        horizontal: bool,
        vertical: bool,
        vec_mode: str,
    ) -> None:
        for layer in self._canvas._flatten_layers():
            if layer.image is None and not layer.vector_objects:
                continue
            if layer.image is not None:
                lw = layer.image.width()
                lh = layer.image.height()
                layer.image = layer.image.flipped(orientations)
                NativeCmykGeometry.flip(layer, horizontal, vertical)
                if horizontal:
                    layer.offset_x = width - layer.offset_x - lw
                if vertical:
                    layer.offset_y = height - layer.offset_y - lh
            if layer.vector_objects:
                layer.vector_objects = self.transform_vector_objects_for_canvas(
                    layer.vector_objects,
                    mode=vec_mode,
                    old_w=width,
                    old_h=height,
                )
            layer.texture_version += 1

    def _rotation_dimensions(
        self,
        angle: int,
        old_width: int,
        old_height: int,
    ) -> tuple[int, int]:
        if angle == 180:
            return old_width, old_height
        return old_height, old_width

    def _rotation_vector_mode(self, angle: int) -> str:
        if angle == 180:
            return "rot180"
        if angle == 90:
            return "rot90"
        return "rot-90"

    def _rotation_history_label(self, angle: int) -> str:
        if angle == 180:
            return "Rotate Canvas 180°"
        if angle == 90:
            return "Rotate Canvas 90° CW"
        return "Rotate Canvas 90° CCW"

    def _apply_rotation_to_layers(
        self,
        *,
        angle: int,
        transform: QTransform,
        old_width: int,
        old_height: int,
        vec_mode: str,
    ) -> None:
        for layer in self._canvas._flatten_layers():
            if layer.image is None and not layer.vector_objects:
                continue
            if layer.image is not None:
                self._rotate_layer_image(
                    layer,
                    angle=angle,
                    transform=transform,
                    old_width=old_width,
                    old_height=old_height,
                )
            if layer.vector_objects:
                layer.vector_objects = self.transform_vector_objects_for_canvas(
                    layer.vector_objects,
                    mode=vec_mode,
                    old_w=old_width,
                    old_h=old_height,
                )
            layer.texture_version += 1

    def _rotate_layer_image(
        self,
        layer: Any,
        *,
        angle: int,
        transform: QTransform,
        old_width: int,
        old_height: int,
    ) -> None:
        image = layer.image
        if image is None:
            return
        lw = image.width()
        lh = image.height()
        ox = layer.offset_x
        oy = layer.offset_y
        layer.image = image.transformed(transform, Qt.TransformationMode.FastTransformation)
        NativeCmykGeometry.rotate(layer, angle)
        if angle == 180:
            layer.offset_x = old_width - ox - lw
            layer.offset_y = old_height - oy - lh
        elif angle == 90:
            layer.offset_x = old_height - oy - lh
            layer.offset_y = ox
        else:
            layer.offset_x = oy
            layer.offset_y = old_width - ox - lw

    def take_resize_snapshot(self) -> Any:
        """Capture current canvas size and all layer images for undo/redo."""
        from .comic_panels.membership import PART_KEY  # noqa: PLC0415

        if any((layer.metadata or {}).get(PART_KEY) for layer in self._canvas._flatten_layers()):
            from .image_size.canvas_resample_service import CanvasResampleService  # noqa: PLC0415

            return CanvasResampleService(self._canvas).take_snapshot()
        layer_images: list[tuple[str, bytes, int, int, int, int]] = []
        layer_vectors: dict[str, list[Any]] = {}
        layer_metadata: dict[str, Any] = {}
        for layer in self._canvas._flatten_layers():
            layer_metadata[layer.id] = copy.deepcopy(getattr(layer, "metadata", None))
            if layer.image is not None:
                data, w, h, _fmt = self._compress_qimage(layer.image)
                layer_images.append((layer.id, data, w, h, layer.offset_x, layer.offset_y))
            if layer.vector_objects:
                layer_vectors[layer.id] = list(layer.vector_objects)
        return self._resize_snapshot_type(
            width=self._canvas._width,
            height=self._canvas._height,
            layer_images=layer_images,
            layer_vectors=layer_vectors,
            layer_metadata=layer_metadata,
            native_layers=NativeCmykGeometry.capture(self._canvas._flatten_layers()),
        )

    def restore_resize_snapshot(self, snapshot: Any) -> None:
        """Restore canvas state from a resize snapshot."""
        self._canvas._width = snapshot.width
        self._canvas._height = snapshot.height

        image_lookup: dict[str, tuple[bytes, int, int, int, int]] = {}
        for lid, data, w, h, ox, oy in snapshot.layer_images:
            image_lookup[lid] = (data, w, h, ox, oy)

        for layer in self._canvas._flatten_layers():
            entry = image_lookup.get(layer.id)
            NativeCmykGeometry.restore(layer, snapshot)
            if layer.id in snapshot.layer_metadata:
                layer.metadata = copy.deepcopy(snapshot.layer_metadata[layer.id])
            if entry is not None:
                data, w, h, ox, oy = entry
                layer.image = self._decompress_qimage(
                    data, w, h, QImage.Format.Format_ARGB32_Premultiplied
                )
                layer.offset_x = ox
                layer.offset_y = oy
            elif layer.id in snapshot.layer_vectors:
                pass
            else:
                layer.image = QImage(
                    snapshot.width,
                    snapshot.height,
                    QImage.Format.Format_ARGB32_Premultiplied,
                )
                layer.image.fill(Qt.GlobalColor.transparent)
            vec_list = snapshot.layer_vectors.get(layer.id)
            if vec_list is not None:
                layer.vector_objects = list(vec_list)
            elif layer.vector_objects:
                layer.vector_objects = []
            layer.texture_version += 1
        self._clear_geometry_selection_state()
        self._reset_composited(snapshot.width, snapshot.height)
        self._finish_geometry_mutation(reset_composited=False)
        self._emit_geometry_changed("resize", 0, 0, snapshot.width, snapshot.height)

    def resize_canvas(
        self,
        width: int,
        height: int,
        *,
        anchor: tuple[float, float] | None = None,
        record_history: bool = True,
    ) -> None:
        """Resize all layer buffers to match a new canvas size."""
        if width <= 0 or height <= 0:
            logger.warning("Ignoring resize to invalid size: %sx%s", width, height)
            return

        if record_history:
            from .comic_panels.canvas_geometry_guard import (  # noqa: PLC0415
                require_supported_canvas_geometry,
            )

            require_supported_canvas_geometry(self._canvas, "crop/canvas resize")
        from .comic_panels.canvas_crop_plan import has_managed_frames  # noqa: PLC0415

        if record_history and has_managed_frames(self._canvas):
            from .comic_panels.canvas_crop_service import resize_managed_canvas  # noqa: PLC0415

            resize_managed_canvas(self, (width, height), anchor)
            return
        before_snapshot = self.take_resize_snapshot() if record_history else None

        old_width, old_height = self._canvas._width, self._canvas._height
        self._canvas._width = width
        self._canvas._height = height

        anchor_x, anchor_y = self.normalize_anchor(anchor)
        shift_x = round(anchor_x * (width - old_width))
        shift_y = round(anchor_y * (height - old_height))

        for layer in self._canvas._flatten_layers():
            native = NativeCmykGeometry.resized(layer, width, height, shift_x, shift_y)
            new_image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
            new_image.fill(Qt.GlobalColor.transparent)
            if layer.image is not None:
                painter = QPainter(new_image)
                mode = getattr(QPainter, "CompositionMode_SourceOver", None)
                if mode is None:
                    mode = getattr(QPainter.CompositionMode, "CompositionMode_SourceOver", None)
                if mode is not None:
                    painter.setCompositionMode(mode)
                painter.drawImage(
                    layer.offset_x + shift_x,
                    layer.offset_y + shift_y,
                    layer.image,
                )
                painter.end()
            layer.image = new_image
            if native is not None:
                layer.native_cmyk = native
            layer.offset_x = 0
            layer.offset_y = 0
            if layer.vector_objects and (shift_x or shift_y):
                layer.vector_objects = self.shift_vector_objects(
                    layer.vector_objects,
                    shift_x,
                    shift_y,
                )

        self._clear_geometry_selection_state()
        self._reset_composited(width, height)
        for layer in self._canvas._flatten_layers():
            layer.texture_version += 1
        self._finish_geometry_mutation(reset_composited=False)

        if record_history and before_snapshot is not None:
            after_snapshot = self.take_resize_snapshot()
            self._push_resize_history("Resize Canvas", before_snapshot, after_snapshot)

        logger.info(
            "Canvas resized from %sx%s to %sx%s",
            old_width,
            old_height,
            width,
            height,
        )
        self._emit_geometry_changed("resize", 0, 0, width, height)

    @staticmethod
    def normalize_anchor(anchor: tuple[float, float] | None) -> tuple[float, float]:
        if anchor is None or not isinstance(anchor, tuple) or len(anchor) != 2:
            return (0.0, 0.0)
        try:
            ax = float(anchor[0])
            ay = float(anchor[1])
        except (TypeError, ValueError):
            return (0.0, 0.0)
        ax = max(0.0, min(1.0, ax))
        ay = max(0.0, min(1.0, ay))
        return (ax, ay)

    def shift_vector_objects(
        self,
        objects: list[Any],
        dx: int,
        dy: int,
    ) -> list[Any]:
        if not objects or (dx == 0 and dy == 0):
            return objects
        shifted: list[Any] = []
        for obj in objects:
            shifted.append(self.shift_vector_object(obj, dx, dy))
        return shifted

    def shift_vector_object(self, obj: Any, dx: int, dy: int) -> Any:
        paths = getattr(obj, "paths", None)
        if not isinstance(paths, list):
            return obj
        new_paths: list[Any] = []
        for path in paths:
            anchors = getattr(path, "anchors", None)
            if not anchors:
                new_paths.append(path)
                continue
            new_anchors = [self.shift_vector_anchor(anchor, dx, dy) for anchor in anchors]
            new_paths.append(self.rebuild_like(path, {"anchors": new_anchors}))
        return self.rebuild_like(obj, {"paths": new_paths})

    def shift_vector_anchor(self, anchor: Any, dx: int, dy: int) -> Any:
        pos = getattr(anchor, "pos", None)
        if not isinstance(pos, tuple) or len(pos) != 2:
            return anchor
        new_pos = (float(pos[0]) + dx, float(pos[1]) + dy)
        return self.rebuild_like(anchor, {"pos": new_pos})

    def transform_vector_objects_for_canvas(
        self,
        objects: list[Any],
        *,
        mode: str,
        old_w: int,
        old_h: int,
    ) -> list[Any]:
        if not objects:
            return objects
        return [
            self.transform_vector_object_for_canvas(obj, mode=mode, old_w=old_w, old_h=old_h)
            for obj in objects
        ]

    def transform_vector_object_for_canvas(
        self,
        obj: Any,
        *,
        mode: str,
        old_w: int,
        old_h: int,
    ) -> Any:
        paths = getattr(obj, "paths", None)
        new_paths: list[Any] = []
        if isinstance(paths, list):
            for path in paths:
                anchors = getattr(path, "anchors", None)
                if not anchors:
                    new_paths.append(path)
                    continue
                new_anchors = [
                    self.transform_anchor_for_canvas(a, mode=mode, old_w=old_w, old_h=old_h)
                    for a in anchors
                ]
                new_paths.append(self.rebuild_like(path, {"anchors": new_anchors}))
        new_meta = self.transform_vector_meta_for_canvas(
            getattr(obj, "meta", None), mode=mode, old_w=old_w, old_h=old_h
        )
        updates: dict[str, Any] = {"paths": new_paths}
        if new_meta is not None:
            updates["meta"] = new_meta
        return self.rebuild_like(obj, updates)

    def transform_anchor_for_canvas(
        self,
        anchor: Any,
        *,
        mode: str,
        old_w: int,
        old_h: int,
    ) -> Any:
        pos = getattr(anchor, "pos", None)
        if not isinstance(pos, tuple) or len(pos) != 2:
            return anchor
        h_in = getattr(anchor, "h_in", None)
        h_out = getattr(anchor, "h_out", None)
        new_pos = self.transform_point_for_canvas(pos, mode=mode, old_w=old_w, old_h=old_h)
        new_h_in = self.transform_handle_vec(h_in, mode=mode) if h_in else h_in
        new_h_out = self.transform_handle_vec(h_out, mode=mode) if h_out else h_out
        return self.rebuild_like(
            anchor,
            {"pos": new_pos, "h_in": new_h_in, "h_out": new_h_out},
        )

    @staticmethod
    def transform_point_for_canvas(
        pt: tuple[float, float],
        *,
        mode: str,
        old_w: int,
        old_h: int,
    ) -> tuple[float, float]:
        x, y = float(pt[0]), float(pt[1])
        table: dict[str, tuple[float, float]] = {
            "flip_h": (old_w - x, y),
            "flip_v": (x, old_h - y),
            "flip_hv": (old_w - x, old_h - y),
            "rot90": (old_h - y, x),
            "rot-90": (y, old_w - x),
            "rot180": (old_w - x, old_h - y),
        }
        return table.get(mode, (x, y))

    @staticmethod
    def transform_handle_vec(
        v: tuple[float, float] | None,
        *,
        mode: str,
    ) -> tuple[float, float] | None:
        if not isinstance(v, tuple) or len(v) != 2:
            return v
        vx, vy = float(v[0]), float(v[1])
        table: dict[str, tuple[float, float]] = {
            "flip_h": (-vx, vy),
            "flip_v": (vx, -vy),
            "flip_hv": (-vx, -vy),
            "rot90": (-vy, vx),
            "rot-90": (vy, -vx),
            "rot180": (-vx, -vy),
        }
        return table.get(mode, (vx, vy))

    def transform_vector_meta_for_canvas(
        self,
        meta: Any,
        *,
        mode: str,
        old_w: int,
        old_h: int,
    ) -> Any:
        if not isinstance(meta, dict):
            return None
        text_tool = meta.get("text_tool")
        if not isinstance(text_tool, dict):
            return None
        new_text_tool = dict(text_tool)
        frame = text_tool.get("frame")
        if isinstance(frame, dict):
            try:
                fx = float(frame.get("x", 0.0))
                fy = float(frame.get("y", 0.0))
                fw = float(frame.get("width", 0.0))
                fh = float(frame.get("height", 0.0))
            except (TypeError, ValueError):
                fx = fy = fw = fh = 0.0
            if fw > 0 and fh > 0:
                nx, ny, nw, nh = self.transform_bbox_for_canvas(
                    fx, fy, fw, fh, mode=mode, old_w=old_w, old_h=old_h
                )
                new_frame = dict(frame)
                new_frame["x"] = nx
                new_frame["y"] = ny
                new_frame["width"] = nw
                new_frame["height"] = nh
                new_text_tool["frame"] = new_frame
        if all(k in text_tool for k in ("x", "y", "width", "height")):
            try:
                tx = float(text_tool["x"])
                ty = float(text_tool["y"])
                tw = float(text_tool["width"])
                th = float(text_tool["height"])
            except (TypeError, ValueError):
                tw = th = 0.0
            if tw > 0 and th > 0:
                nx, ny, nw, nh = self.transform_bbox_for_canvas(
                    tx, ty, tw, th, mode=mode, old_w=old_w, old_h=old_h
                )
                new_text_tool["x"] = nx
                new_text_tool["y"] = ny
                new_text_tool["width"] = nw
                new_text_tool["height"] = nh
        cur_flip_h = bool(text_tool.get("visual_flip_h", False))
        cur_flip_v = bool(text_tool.get("visual_flip_v", False))
        if mode == "flip_h":
            new_text_tool["visual_flip_h"] = not cur_flip_h
        elif mode == "flip_v":
            new_text_tool["visual_flip_v"] = not cur_flip_v
        elif mode in {"flip_hv", "rot180"}:
            new_text_tool["visual_flip_h"] = not cur_flip_h
            new_text_tool["visual_flip_v"] = not cur_flip_v
        new_meta = dict(meta)
        new_meta["text_tool"] = new_text_tool
        return new_meta

    @staticmethod
    def transform_bbox_for_canvas(
        x: float,
        y: float,
        w: float,
        h: float,
        *,
        mode: str,
        old_w: int,
        old_h: int,
    ) -> tuple[float, float, float, float]:
        table: dict[str, tuple[float, float, float, float]] = {
            "flip_h": (old_w - x - w, y, w, h),
            "flip_v": (x, old_h - y - h, w, h),
            "flip_hv": (old_w - x - w, old_h - y - h, w, h),
            "rot90": (old_h - y - h, x, h, w),
            "rot-90": (y, old_w - x - w, h, w),
            "rot180": (old_w - x - w, old_h - y - h, w, h),
        }
        return table.get(mode, (x, y, w, h))

    @staticmethod
    def rebuild_like(obj: Any, updates: dict[str, Any]) -> Any:
        dataclass_fields = getattr(obj, "__dataclass_fields__", None)
        if dataclass_fields:
            payload: dict[str, Any] = {}
            for name in dataclass_fields:
                payload[name] = updates.get(name, getattr(obj, name))
            try:
                return type(obj)(**payload)
            except Exception:
                return obj
        replace = getattr(obj, "_replace", None)
        if callable(replace):
            try:
                return replace(**updates)
            except Exception:
                return obj
        try:
            for key, value in updates.items():
                setattr(obj, key, value)
        except Exception:
            return obj
        return obj

    def _push_resize_history(self, label: str, before_snapshot: Any, after_snapshot: Any) -> None:
        entry = self._resize_entry_type(before=before_snapshot, after=after_snapshot)
        group = self._history_group_type(label=label, entries=[entry])
        self._canvas._push_history_group(group)

    def _clear_geometry_selection_state(self) -> None:
        self._canvas._selection_mask = None
        self._canvas._selection_bounds = None
        if getattr(self._canvas, "_vector_selection", None) is not None:
            self._canvas._vector_selection = None

    def _reset_composited(self, width: int, height: int) -> None:
        CanvasCompositedPixmapStateService.reset_composited_pixmap(
            self._canvas,
            width,
            height,
            self._canvas._background,
        )

    def _finish_geometry_mutation(self, *, reset_composited: bool = True) -> None:
        self._canvas._qpath_cache.clear()
        if reset_composited:
            self._reset_composited(self._canvas._width, self._canvas._height)
        self._canvas._invalidate_layer_cache()
        self._canvas._recompose()

    def _emit_geometry_changed(
        self,
        change_type: str,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> None:
        if not self._canvas._event_bus:
            return
        with suppress(Exception):
            self._canvas._emit_doc_changed(
                DocumentChangeEvent(
                    change_type=change_type,
                    affected_area=(x, y, width, height),
                    layer_id=self._canvas.current_layer().id if self._canvas._layers else "",
                )
            )
