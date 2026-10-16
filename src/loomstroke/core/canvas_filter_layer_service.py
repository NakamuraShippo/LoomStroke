"""Image filter layer application and editing for SimpleCanvasService."""

from __future__ import annotations

import logging
import time
from typing import Any

from PySide6.QtGui import QImage, QPainter

from .filters import FILTER_LAYER_TYPE
from .filters.screentone import FILTER_TYPE as SCREENTONE_FILTER_TYPE
from .filters.screentone import screentone_runtime_params

logger = logging.getLogger(__name__)


def _filter_value(layer: Any, name: str, legacy: str) -> Any:
    return getattr(layer, name, getattr(layer, legacy, None))


def _filter_identifier(value: str | None, legacy: str | None, name: str) -> str:
    if legacy is not None:
        if value is not None and value != legacy:
            raise TypeError(f"Conflicting {name} and legacy effect identifier")
        value = legacy
    if value is None:
        raise TypeError(f"{name} is required")
    return value


class CanvasPluginFilterImageApplier:
    """Apply plugin filter layers to QImage inputs."""

    def __init__(self, filter_registry: Any = None, *, effect_registry: Any = None) -> None:
        self._filter_registry = filter_registry if filter_registry is not None else effect_registry

    def apply_filter_to_image(
        self,
        source: QImage,
        filter_layer: Any,
        *,
        document_dpi: float | None = None,
        layer_origin: tuple[int, int] = (0, 0),
    ) -> QImage:
        """Apply *filter_layer* to *source*.

        ``document_dpi`` / ``layer_origin`` are the apply-time context of
        scale-aware filters (CT-02 screentone: screen pitch from the DPI,
        grid phase from the carrier's canvas position); they are injected
        into a copy of the params, never stored on the layer.
        """
        if not _filter_value(filter_layer, "filter_type", "effect_type"):
            return source
        filter_cls = self._filter_registry.get(
            _filter_value(filter_layer, "filter_type", "effect_type")
        )
        if filter_cls is None:
            return source
        image_filter = filter_cls()
        mask = None
        if _filter_value(filter_layer, "filter_mask", "effect_mask") is not None and getattr(
            filter_layer, "mask_enabled", True
        ):
            mask = _filter_value(filter_layer, "filter_mask", "effect_mask")
        params = _filter_value(filter_layer, "filter_params", "effect_params")
        if _filter_value(filter_layer, "filter_type", "effect_type") == SCREENTONE_FILTER_TYPE:
            params = screentone_runtime_params(
                params, document_dpi=document_dpi, layer_origin=layer_origin
            )
        try:
            result = image_filter.apply(source, params, mask)
        except Exception:  # noqa: BLE001
            logger.warning(
                "CPU filter '%s' failed, skipping",
                _filter_value(filter_layer, "filter_type", "effect_type"),
            )
            return source
        return self._blend_filter_result(source, result, filter_layer)

    @staticmethod
    def _blend_filter_result(source: QImage, result: QImage, filter_layer: Any) -> QImage:
        opacity = filter_layer.opacity
        if opacity < 1.0 and result is not source:
            blended = source.copy()
            painter = QPainter(blended)
            painter.setOpacity(opacity)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.drawImage(0, 0, result)
            painter.end()
            return blended
        return result

    def apply_effect_to_image(self, source: QImage, effect_layer: Any, **context: Any) -> QImage:
        return self.apply_filter_to_image(source, effect_layer, **context)

    _blend_effect_result = _blend_filter_result


class CanvasFilterLayerService:
    """Command-backed helpers for ordinary image filter layers."""

    def __init__(
        self,
        canvas: Any,
        *,
        filter_registry: Any = None,
        effect_registry: Any = None,
        layer_type: type[Any],
        events: Any,
    ) -> None:
        self._canvas = canvas
        self._filter_registry = filter_registry if filter_registry is not None else effect_registry
        self._layer_type = layer_type
        self._events = events
        self._plugin_filter_applier = CanvasPluginFilterImageApplier(self._filter_registry)

    @property
    def _cache_bytes(self) -> int:
        return int(
            getattr(
                self._canvas, "_filter_cache_bytes", getattr(self._canvas, "_effect_cache_bytes", 0)
            )
        )

    @_cache_bytes.setter
    def _cache_bytes(self, value: int) -> None:
        name = (
            "_filter_cache_bytes"
            if hasattr(self._canvas, "_filter_cache_bytes")
            else "_effect_cache_bytes"
        )
        setattr(self._canvas, name, value)

    def apply_filter_to_image(
        self,
        source: QImage,
        filter_layer: Any,
        *,
        layer_origin: tuple[int, int] = (0, 0),
    ) -> QImage:
        return self._plugin_filter_applier.apply_filter_to_image(
            source,
            filter_layer,
            document_dpi=float(getattr(self._canvas, "_dpi", 96) or 96),
            layer_origin=layer_origin,
        )

    def clear_filter_cache(self, layer: Any) -> None:
        if layer.filter_cache is not None:
            self._cache_bytes -= layer.filter_cache.width() * layer.filter_cache.height() * 4
            layer.filter_cache = None

    def reset_filter_cache_bytes(self) -> None:
        total = 0
        for layer in self._canvas._flatten_layers():
            if layer.filter_cache is not None:
                total += layer.filter_cache.width() * layer.filter_cache.height() * 4
        self._cache_bytes = total

    def add_filter_layer(
        self,
        parent_layer_id: str | None,
        filter_type: str | None = None,
        params: dict[str, Any] | None = None,
        *,
        position: int = -1,
        effect_type: str | None = None,
    ) -> str | None:
        filter_type = _filter_identifier(filter_type, effect_type, "filter_type")
        filter_get = getattr(self._filter_registry, "get", None)
        if not callable(filter_get) or filter_get(filter_type) is None:
            return None

        if parent_layer_id is None:
            target_list = self._canvas._layers
        else:
            parent = self._canvas._find_layer_by_id_tree(parent_layer_id)
            if parent is None or parent.layer_type == FILTER_LAYER_TYPE:
                return None
            target_list = parent.children

        filter_id = f"effect-{time.time_ns()}"
        filter_layer = self._layer_type(
            id=filter_id,
            name=filter_type.replace("_", " ").title(),
            layer_type=FILTER_LAYER_TYPE,
            effect_type=filter_type,
            effect_params=dict(params) if params else {},
        )
        filter_layer.parent_id = parent_layer_id

        if position < 0 or position > len(target_list):
            target_list.append(filter_layer)
        else:
            target_list.insert(position, filter_layer)

        self._canvas._invalidate_layer_cache()
        self._canvas._recompose()
        self._canvas._publish_layer_event(
            self._events.LAYER_CHANGED,
            layer=filter_layer,
            change_type="effect_added",
        )
        return filter_id

    def remove_filter_layer(
        self, filter_layer_id: str | None = None, *, effect_layer_id: str | None = None
    ) -> bool:
        filter_layer_id = _filter_identifier(filter_layer_id, effect_layer_id, "filter_layer_id")
        result = self._canvas._find_parent(filter_layer_id)
        if result is None:
            return False
        siblings, idx = result
        layer = siblings[idx]
        if layer.layer_type != FILTER_LAYER_TYPE:
            return False
        self.clear_filter_cache(layer)
        siblings.pop(idx)
        self._canvas._invalidate_layer_cache()
        self._canvas._recompose()
        self._canvas._publish_layer_event(
            self._events.LAYER_CHANGED,
            layer=layer,
            change_type="effect_removed",
        )
        return True

    def set_filter_params(
        self,
        filter_layer_id: str | None = None,
        params: dict[str, Any] | None = None,
        *,
        effect_layer_id: str | None = None,
    ) -> bool:
        filter_layer_id = _filter_identifier(filter_layer_id, effect_layer_id, "filter_layer_id")
        if params is None:
            raise TypeError("params is required")
        layer = self._canvas._find_layer_by_id_tree(filter_layer_id)
        if layer is None or layer.layer_type != FILTER_LAYER_TYPE:
            return False
        layer.filter_params = dict(params)
        self.clear_filter_cache(layer)
        self._canvas._invalidate_layer_cache()
        self._canvas._recompose()
        self._canvas._publish_layer_event(
            self._events.LAYER_CHANGED,
            layer=layer,
            change_type="effect_params_changed",
        )
        return True

    def get_filter_params(
        self, filter_layer_id: str | None = None, *, effect_layer_id: str | None = None
    ) -> dict[str, Any] | None:
        filter_layer_id = _filter_identifier(filter_layer_id, effect_layer_id, "filter_layer_id")
        layer = self._canvas._find_layer_by_id_tree(filter_layer_id)
        if layer is None or layer.layer_type != FILTER_LAYER_TYPE:
            return None
        return dict(layer.filter_params)

    def apply_effect_to_image(self, source: QImage, effect_layer: Any, **context: Any) -> QImage:
        return self.apply_filter_to_image(source, effect_layer, **context)

    def clear_effect_cache(self, layer: Any) -> None:
        self.clear_filter_cache(layer)

    def reset_effect_cache_bytes(self) -> None:
        self.reset_filter_cache_bytes()

    def add_effect_layer(
        self,
        parent_layer_id: str | None,
        effect_type: str,
        params: dict[str, Any] | None = None,
        *,
        position: int = -1,
    ) -> str | None:
        return self.add_filter_layer(parent_layer_id, effect_type, params, position=position)

    def remove_effect_layer(self, effect_layer_id: str) -> bool:
        return self.remove_filter_layer(effect_layer_id)

    def set_effect_params(self, effect_layer_id: str, params: dict[str, Any]) -> bool:
        return self.set_filter_params(effect_layer_id, params)

    def get_effect_params(self, effect_layer_id: str) -> dict[str, Any] | None:
        return self.get_filter_params(effect_layer_id)
