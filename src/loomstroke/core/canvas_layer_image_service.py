"""Image layer services combining filters and independent AI Effect outputs."""

from __future__ import annotations

import logging
import time
from typing import Any

from PySide6.QtGui import QImage

from loomstroke.host_api import LayerRef

from .canvas_filter_layer_service import (
    CanvasFilterLayerService,
    CanvasPluginFilterImageApplier,
)
from .comic_panels.ai_image_context import place_panel_output

logger = logging.getLogger(__name__)


class AIEffectLayerImageService:
    """Decode and install opaque AI Effect layer image bytes."""

    def __init__(
        self,
        canvas: Any,
        *,
        events: Any,
    ) -> None:
        self._canvas = canvas
        self._events = events

    def set_ai_effect_layer_image(
        self,
        layer_id: str,
        image_bytes: bytes | None,
    ) -> bool:
        layer = self._canvas._find_layer_by_id_tree(layer_id)
        if layer is None or layer.layer_type != "ai_effect":
            return False

        if image_bytes is None:
            layer.image = None
        else:
            decoded = self._decode_ai_effect_layer_png(layer_id, image_bytes)
            if decoded is None:
                return False
            previous_image = layer.image
            layer.image = decoded
            if not place_panel_output(self._canvas, layer, previous_image):
                layer.offset_x = 0
                layer.offset_y = 0

        layer.texture_version += 1
        self._canvas._invalidate_layer_cache()
        self._canvas._recompose()
        self._canvas._publish_layer_event(
            self._events.LAYER_CHANGED,
            layer=layer,
            change_type="ai_effect_image_changed",
        )
        return True

    @staticmethod
    def _decode_ai_effect_layer_png(layer_id: str, image_bytes: bytes) -> QImage | None:
        decoded = QImage()
        if not decoded.loadFromData(image_bytes) or decoded.isNull():
            logger.warning(
                "Failed to decode PNG for AI Effect layer %s (%d bytes)",
                layer_id,
                len(image_bytes),
            )
            return None
        if decoded.format() != QImage.Format.Format_ARGB32_Premultiplied:
            decoded = decoded.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        return decoded


class AIEffectOutputTombstoneStore:
    """Store removed AI Effect output layers for undo/redo resurrection."""

    def __init__(self, canvas: Any) -> None:
        self._canvas = canvas

    def tombstones(self) -> dict[str, Any]:
        tombstones = getattr(self._canvas, "_ai_effect_output_tombstones", None)
        if tombstones is None:
            tombstones = {}
            self._canvas._ai_effect_output_tombstones = tombstones
        return tombstones

    def pop(self, layer_id: str) -> Any | None:
        return self.tombstones().pop(layer_id, None)


class AIEffectOutputMaterializer:
    """Create and restore raster output layers produced by AI Effect."""

    def __init__(
        self,
        canvas: Any,
        *,
        layer_type: type[Any],
        events: Any,
        tombstone_store: AIEffectOutputTombstoneStore,
    ) -> None:
        self._canvas = canvas
        self._layer_type = layer_type
        self._events = events
        self._tombstone_store = tombstone_store

    def add_ai_effect_output_layers(
        self,
        parent_effect_id: str,
        new_layer_ids: list[str],
    ) -> bool:
        parent = self._canvas._find_layer_by_id_tree(parent_effect_id)
        if parent is None or parent.layer_type != "ai_effect":
            return False
        parent_result = self._canvas._find_parent(parent_effect_id)
        if parent_result is None:
            return False
        sibling_list, parent_idx = parent_result

        restored: list[Any] = []
        for offset, layer_id in enumerate(new_layer_ids, start=1):
            cached = self._tombstone_store.pop(layer_id)
            if cached is None:
                logger.warning(
                    "add_ai_effect_output_layers: no tombstone for %s, aborting",
                    layer_id,
                )
                return False
            insert_at = parent_idx + offset
            if insert_at > len(sibling_list):
                sibling_list.append(cached)
            else:
                sibling_list.insert(insert_at, cached)
            cached.parent_id = parent.parent_id
            restored.append(cached)

        if not restored:
            return True

        self._canvas._invalidate_layer_cache()
        self._canvas._recompose()
        logger.info(
            "add_ai_effect_output_layers restored %d layer(s) above %s",
            len(restored),
            parent_effect_id,
        )
        self._canvas._publish_layer_event(
            self._events.LAYER_CHANGED,
            layer=parent,
            change_type="ai_effect_outputs_added",
        )
        return True

    def create_ai_effect_output_layer(
        self,
        parent_effect_id: str,
        *,
        name: str,
        png_bytes: bytes,
        output_internal_id: str | None = None,
        generation_index: int = 0,
    ) -> str | None:
        parent = self._canvas._find_layer_by_id_tree(parent_effect_id)
        if parent is None or parent.layer_type != "ai_effect":
            logger.warning(
                "create_ai_effect_output_layer: parent %s missing or wrong type",
                parent_effect_id,
            )
            return None

        parent_result = self._canvas._find_parent(parent_effect_id)
        if parent_result is None:
            return None
        sibling_list, parent_idx = parent_result

        decoded = self._decode_ai_effect_output_png(parent_effect_id, png_bytes)
        if decoded is None:
            return None

        layer = self._create_ai_effect_output_layer(
            parent,
            parent_effect_id=parent_effect_id,
            name=name,
            image=decoded,
            output_internal_id=output_internal_id,
            generation_index=generation_index,
        )

        insert_at = parent_idx + 1
        if insert_at >= len(sibling_list):
            sibling_list.append(layer)
        else:
            sibling_list.insert(insert_at, layer)

        if sibling_list is self._canvas._layers and self._canvas._active_index >= insert_at:
            self._canvas._active_index += 1

        self._canvas._invalidate_layer_cache()
        self._canvas._recompose()
        logger.info(
            "create_ai_effect_output_layer: minted %s (name=%s) above %s",
            layer.id,
            name,
            parent_effect_id,
        )
        self._canvas._publish_layer_event(
            self._events.LAYER_ADDED,
            layer=layer,
            change_type="ai_effect_output_created",
        )
        return str(layer.id)

    @staticmethod
    def _decode_ai_effect_output_png(parent_effect_id: str, png_bytes: bytes) -> QImage | None:
        decoded = QImage()
        if not decoded.loadFromData(png_bytes) or decoded.isNull():
            logger.warning(
                "create_ai_effect_output_layer: decode failed (%d bytes) for parent %s",
                len(png_bytes),
                parent_effect_id,
            )
            return None
        if decoded.format() != QImage.Format.Format_ARGB32_Premultiplied:
            decoded = decoded.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        return decoded

    def _create_ai_effect_output_layer(
        self,
        parent: Any,
        *,
        parent_effect_id: str,
        name: str,
        image: QImage,
        output_internal_id: str | None,
        generation_index: int,
    ) -> Any:
        from loomstroke.plugins.ai_effect.data_model import LayerOrigin  # noqa: PLC0415

        new_id = f"ai-effect-output-{len(self._canvas._layers)}-{time.time_ns()}"
        layer = self._layer_type(
            id=new_id,
            name=name,
            visible=True,
            opacity=1.0,
            image=image,
            layer_type="raster",
            parent_id=parent.parent_id,
            origin=LayerOrigin(
                type="ai_effect_output",
                parent_effect_id=parent_effect_id,
                output_slot_id=output_internal_id,
                generation_index=int(generation_index),
            ),
        )
        place_panel_output(self._canvas, layer)
        return layer


class CanvasLayerImageService(CanvasFilterLayerService):
    """Image helpers retained by SimpleCanvasService for filters and AI outputs."""

    def __init__(
        self,
        canvas: Any,
        *,
        filter_registry: Any = None,
        effect_registry: Any = None,
        layer_type: type[Any],
        events: Any,
    ) -> None:
        super().__init__(
            canvas,
            filter_registry=filter_registry,
            effect_registry=effect_registry,
            layer_type=layer_type,
            events=events,
        )
        self._ai_layer_image = AIEffectLayerImageService(canvas, events=events)
        self._ai_output_tombstones = AIEffectOutputTombstoneStore(canvas)
        self._ai_output_materializer = AIEffectOutputMaterializer(
            canvas,
            layer_type=layer_type,
            events=events,
            tombstone_store=self._ai_output_tombstones,
        )

    def set_ai_effect_layer_image(
        self,
        layer_id: str,
        image_bytes: bytes | None,
    ) -> bool:
        return self._ai_layer_image.set_ai_effect_layer_image(layer_id, image_bytes)

    def add_ai_effect_output_layers(
        self,
        parent_effect_id: str,
        new_layer_ids: list[str],
    ) -> bool:
        return self._ai_output_materializer.add_ai_effect_output_layers(
            parent_effect_id,
            new_layer_ids,
        )

    def create_ai_effect_output_layer(
        self,
        parent_effect_id: str,
        *,
        name: str,
        png_bytes: bytes,
        output_internal_id: str | None = None,
        generation_index: int = 0,
    ) -> str | None:
        return self._ai_output_materializer.create_ai_effect_output_layer(
            parent_effect_id,
            name=name,
            png_bytes=png_bytes,
            output_internal_id=output_internal_id,
            generation_index=generation_index,
        )

    @staticmethod
    def layer_ref(layer: Any) -> LayerRef:
        return LayerRef(
            id=layer.id,
            name=layer.name,
            layer_type=layer.layer_type,
            vector_kind=layer.vector_kind,
        )


CanvasEffectApplyService = CanvasLayerImageService
CanvasPluginEffectImageApplier = CanvasPluginFilterImageApplier
