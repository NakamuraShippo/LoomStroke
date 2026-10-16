"""Document snapshot boundary for save, autosave, and page switching.

The builder is intentionally pure after the main-thread capture step: QImage
data is converted to numpy arrays before the returned ``DocumentSnapshot`` is
handed to worker-thread encoding.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray
from PySide6.QtGui import QImage

from loomstroke.io.layer_io import DocumentMetadata, DocumentSnapshot, LayerSnapshot, PageSnapshot

from .canvas import Canvas as LegacyCanvas
from .canvas_service_simple import SimpleCanvasService, _Layer
from .commands import BlendMode
from .layer import Layer
from .layer_transform_metadata import capture_layer_transform_metadata, read_layer_transform
from .mask_coverage import alpha8_snapshot_rgba
from .native_cmyk.buffer import NativeCmykBuffer

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DocumentSnapshotBuildResult:
    """Snapshot plus page bookkeeping produced on the Qt main thread."""

    snapshot: DocumentSnapshot
    layers: list[LayerSnapshot]
    pages: list[PageSnapshot]
    active_page_id: str | None


class DocumentSnapshotBuilder:
    """Main-thread document snapshot builder."""

    def __init__(
        self,
        canvas: Any,
        prepare_pages: (
            Callable[
                [DocumentMetadata, Sequence[LayerSnapshot]],
                tuple[list[PageSnapshot], str | None],
            ]
            | None
        ) = None,
    ) -> None:
        self._canvas = canvas
        self._prepare_pages = prepare_pages

    def build(self, metadata: DocumentMetadata) -> DocumentSnapshotBuildResult:
        filter_pen = getattr(
            self._canvas, "_filter_pen_service", getattr(self._canvas, "_effect_pen_service", None)
        )
        if filter_pen is not None:
            filter_pen.finalize()
        layers = self.capture_canvas_layers(self._canvas)
        if self._prepare_pages is None:
            pages = [PageSnapshot(id="p_0001", index=0, layers=layers)]
            active_page_id: str | None = "p_0001"
        else:
            pages, active_page_id = self._prepare_pages(metadata, layers)
        snapshot = DocumentSnapshot(metadata=metadata, pages=pages)
        return DocumentSnapshotBuildResult(
            snapshot=snapshot,
            layers=layers,
            pages=pages,
            active_page_id=active_page_id,
        )

    @classmethod
    def capture_canvas_layers(cls, canvas: Any) -> list[LayerSnapshot]:
        if isinstance(canvas, SimpleCanvasService):
            return cls.capture_simple_canvas_layers(canvas)
        if isinstance(canvas, LegacyCanvas):
            return cls.capture_legacy_canvas_layers(canvas)
        raise RuntimeError("Unsupported canvas implementation")

    @classmethod
    def capture_simple_canvas_layers(cls, canvas: SimpleCanvasService) -> list[LayerSnapshot]:
        return cls.capture_layer_list(list(getattr(canvas, "_layers", [])), canvas)

    @classmethod
    def capture_layer_list(
        cls,
        layers: Sequence[Any],
        canvas: SimpleCanvasService,
    ) -> list[LayerSnapshot]:
        return [cls.capture_single_layer(layer, canvas) for layer in layers]

    @classmethod
    def capture_single_layer(cls, layer: Any, canvas: SimpleCanvasService) -> LayerSnapshot:
        image = cls._blank_image(canvas._width, canvas._height)
        if getattr(layer, "image", None) is not None:
            image = cls.qimage_to_array(layer.image)

        blend_mode = (
            layer.blend_mode.value
            if isinstance(getattr(layer, "blend_mode", None), BlendMode)
            else str(getattr(layer, "blend_mode", "normal"))
        )

        layer_mask_image = None
        if getattr(layer, "layer_mask", None) is not None:
            mask = layer.layer_mask
            if mask.format() == QImage.Format.Format_Alpha8:
                layer_mask_image = alpha8_snapshot_rgba(mask)
            else:
                layer_mask_image = cls.qimage_to_array(mask)

        filter_mask_image = None
        filter_mask = getattr(layer, "filter_mask", getattr(layer, "effect_mask", None))
        if filter_mask is not None:
            filter_mask_image = cls.qimage_to_array(filter_mask)

        children = cls.capture_layer_list(getattr(layer, "children", []) or [], canvas)
        ai_effect_dict, origin_dict = cls._ai_effect_payloads(layer)
        gen_3d_dict = cls._gen_3d_payload(layer)

        return LayerSnapshot(
            id=layer.id,
            name=layer.name,
            image=image,
            visible=bool(layer.visible),
            opacity=float(layer.opacity),
            blend_mode=str(blend_mode),
            locked=bool(layer.locked),
            alpha_lock=bool(getattr(layer, "alpha_lock", False)),
            position=(int(getattr(layer, "offset_x", 0)), int(getattr(layer, "offset_y", 0))),
            # Retain plugin metadata and the committed non-destructive
            # transform without modifying the live layer or source image.
            metadata=capture_layer_transform_metadata(layer),
            layer_type=getattr(layer, "layer_type", "raster"),
            vector_kind=getattr(layer, "vector_kind", "draw"),
            vector_objects=list(getattr(layer, "vector_objects", []) or []),
            children=children,
            clipping=bool(getattr(layer, "clipping", False)),
            layer_mask_image=layer_mask_image,
            mask_enabled=bool(getattr(layer, "mask_enabled", True)),
            effect_type=getattr(layer, "filter_type", getattr(layer, "effect_type", None)),
            effect_params=dict(
                getattr(layer, "filter_params", getattr(layer, "effect_params", {})) or {}
            ),
            effect_mask_image=filter_mask_image,
            ai_effect=ai_effect_dict,
            origin=origin_dict,
            scene_3d=getattr(layer, "scene_3d", None),
            camera_3d=cls._dict_payload(getattr(layer, "camera_3d", None)),
            lighting_3d=cls._dict_payload(getattr(layer, "lighting_3d", None)),
            display_mode_3d=str(getattr(layer, "display_mode_3d", "solid") or "solid"),
            gen_3d=gen_3d_dict,
            native_cmyk=(
                layer.native_cmyk.snapshot()
                if getattr(layer, "native_cmyk", None) is not None
                else None
            ),
        )

    @classmethod
    def capture_legacy_canvas_layers(cls, canvas: LegacyCanvas) -> list[LayerSnapshot]:
        snapshots: list[LayerSnapshot] = []
        for layer in canvas.get_layers():
            if not isinstance(layer, Layer):
                continue
            image = np.asarray(layer.get_image_data(dtype=np.uint8), dtype=np.uint8)
            blend_mode = (
                layer.blend_mode.value
                if isinstance(layer.blend_mode, BlendMode)
                else str(layer.blend_mode)
            )
            snapshots.append(
                LayerSnapshot(
                    id=layer.id,
                    name=layer.name,
                    image=image,
                    visible=layer.visible,
                    opacity=float(layer.opacity),
                    blend_mode=str(blend_mode),
                    locked=layer.locked,
                    alpha_lock=getattr(layer, "alpha_lock", False),
                    metadata={},
                )
            )
        return snapshots

    @classmethod
    def snapshots_to_simple_layers(
        cls,
        snapshots: Sequence[LayerSnapshot],
    ) -> list[_Layer]:
        return [cls.snapshot_to_simple_layer(snapshot) for snapshot in snapshots]

    @classmethod
    def snapshot_to_simple_layer(cls, snapshot: LayerSnapshot) -> _Layer:
        layer = _Layer(
            id=snapshot.id,
            name=snapshot.name,
            visible=snapshot.visible,
            opacity=float(snapshot.opacity),
            blend_mode=cls.to_blend_mode(snapshot.blend_mode),
            image=cls.array_to_qimage_native_size(snapshot.image),
            locked=snapshot.locked,
            alpha_lock=getattr(snapshot, "alpha_lock", False),
            layer_type=getattr(snapshot, "layer_type", "raster"),
            vector_kind=getattr(snapshot, "vector_kind", "draw"),
            vector_objects=list(getattr(snapshot, "vector_objects", []) or []),
            clipping=getattr(snapshot, "clipping", False),
            mask_enabled=getattr(snapshot, "mask_enabled", True),
        )
        layer.offset_x = int(snapshot.position[0])
        if snapshot.native_cmyk is not None:
            layer.native_cmyk = NativeCmykBuffer(snapshot.native_cmyk)
        layer.offset_y = int(snapshot.position[1])
        snapshot_metadata = getattr(snapshot, "metadata", None)
        layer.metadata = dict(snapshot_metadata) if snapshot_metadata else None
        layer.transform = read_layer_transform(snapshot_metadata, snapshot.position)

        mask_img = getattr(snapshot, "layer_mask_image", None)
        if mask_img is not None:
            layer.layer_mask = cls.array_to_qimage_native_size(mask_img)

        layer.filter_type = getattr(snapshot, "filter_type", getattr(snapshot, "effect_type", None))
        layer.filter_params = dict(
            getattr(snapshot, "filter_params", getattr(snapshot, "effect_params", {})) or {}
        )
        filter_mask_image = getattr(
            snapshot, "filter_mask_image", getattr(snapshot, "effect_mask_image", None)
        )
        if filter_mask_image is not None:
            layer.filter_mask = cls.array_to_qimage_native_size(filter_mask_image)

        cls._restore_ai_effect_payloads(layer, snapshot)
        cls._restore_3d_payloads(layer, snapshot)

        children_snapshots = getattr(snapshot, "children", [])
        if children_snapshots:
            layer.children = cls.snapshots_to_simple_layers(children_snapshots)
            for child in layer.children:
                child.parent_id = layer.id
        return layer

    @staticmethod
    def _ai_effect_payloads(layer: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        ai_effect_dict: dict[str, Any] | None = None
        origin_dict: dict[str, Any] | None = None
        raw_ai_effect = getattr(layer, "ai_effect", None)
        if raw_ai_effect is not None:
            from loomstroke.plugins.ai_effect.data_model import AIEffectState  # noqa: PLC0415

            if isinstance(raw_ai_effect, AIEffectState):
                ai_effect_dict = raw_ai_effect.to_dict()
            elif isinstance(raw_ai_effect, dict):
                ai_effect_dict = dict(raw_ai_effect)
        raw_origin = getattr(layer, "origin", None)
        if raw_origin is not None:
            from loomstroke.plugins.ai_effect.data_model import LayerOrigin  # noqa: PLC0415

            if isinstance(raw_origin, LayerOrigin):
                origin_dict = raw_origin.to_dict()
            elif isinstance(raw_origin, dict):
                origin_dict = dict(raw_origin)
        return ai_effect_dict, origin_dict

    @staticmethod
    def _dict_payload(value: Any) -> dict[str, Any] | None:
        if isinstance(value, dict):
            return dict(value)
        to_dict = getattr(value, "to_dict", None)
        if callable(to_dict):
            raw = to_dict()
            if isinstance(raw, dict):
                return dict(raw)
        return None

    @classmethod
    def _gen_3d_payload(cls, layer: Any) -> dict[str, Any] | None:
        raw_gen = getattr(layer, "gen_3d", None)
        payload = cls._dict_payload(raw_gen)
        if payload is None:
            return None
        try:
            from loomstroke.core.assets_3d.provider_contract import (  # noqa: PLC0415
                mask_sensitive_metadata,
            )

            masked = mask_sensitive_metadata(payload)
            return masked if isinstance(masked, dict) else payload
        except ImportError:  # pragma: no cover - optional 3D package unavailable
            return payload

    @staticmethod
    def _restore_ai_effect_payloads(layer: _Layer, snapshot: LayerSnapshot) -> None:
        raw_ai_effect = getattr(snapshot, "ai_effect", None)
        if isinstance(raw_ai_effect, dict):
            from loomstroke.plugins.ai_effect.data_model import AIEffectState  # noqa: PLC0415

            try:
                layer.ai_effect = AIEffectState.from_dict(raw_ai_effect)
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("Failed to restore ai_effect for layer %s: %s", layer.id, exc)
        raw_origin = getattr(snapshot, "origin", None)
        if isinstance(raw_origin, dict):
            from loomstroke.plugins.ai_effect.data_model import LayerOrigin  # noqa: PLC0415

            try:
                layer.origin = LayerOrigin.from_dict(raw_origin)
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("Failed to restore origin for layer %s: %s", layer.id, exc)

    @staticmethod
    def _restore_3d_payloads(layer: _Layer, snapshot: LayerSnapshot) -> None:
        layer.scene_3d = getattr(snapshot, "scene_3d", None)
        layer.camera_3d = getattr(snapshot, "camera_3d", None)
        layer.lighting_3d = getattr(snapshot, "lighting_3d", None)
        layer.display_mode_3d = str(getattr(snapshot, "display_mode_3d", "solid") or "solid")
        layer.gen_3d = getattr(snapshot, "gen_3d", None)

    @staticmethod
    def _blank_image(width: int, height: int) -> NDArray[np.uint8]:
        return np.zeros((height, width, 4), dtype=np.uint8)

    @staticmethod
    def ensure_rgba(data: NDArray[Any]) -> NDArray[np.uint8]:
        arr = np.asarray(data)
        if arr.ndim != 3:
            raise ValueError("image data must be HxWxC")
        if arr.shape[2] == 3:
            alpha = np.full((arr.shape[0], arr.shape[1], 1), 255, dtype=arr.dtype)
            arr = np.concatenate([arr, alpha], axis=2)
        if arr.dtype != np.uint8:
            arr = np.clip(arr, 0, 255).astype(np.uint8)
        return arr

    @classmethod
    def qimage_to_array(cls, image: QImage) -> NDArray[np.uint8]:
        converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
        byte_length = converted.bytesPerLine() * converted.height()
        buffer = converted.bits()
        if hasattr(buffer, "setsize"):
            buffer.setsize(byte_length)
            data_view = buffer
        else:
            data_view = buffer if isinstance(buffer, memoryview) else memoryview(buffer)
        arr = np.frombuffer(data_view, dtype=np.uint8, count=byte_length).reshape(
            converted.height(), converted.bytesPerLine()
        )
        arr = arr[:, : converted.width() * 4]
        return arr.reshape(converted.height(), converted.width(), 4).copy()

    @classmethod
    def array_to_qimage_native_size(cls, data: NDArray[np.uint8]) -> QImage:
        arr = np.ascontiguousarray(cls.ensure_rgba(data))
        height, width = arr.shape[:2]
        qimage = QImage(arr.data, width, height, arr.strides[0], QImage.Format.Format_RGBA8888)
        return qimage.copy().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

    @staticmethod
    def to_blend_mode(name: str) -> BlendMode:
        normalized = str(name).replace(" ", "_").upper()
        try:
            return BlendMode[normalized]
        except KeyError:
            return BlendMode.NORMAL
