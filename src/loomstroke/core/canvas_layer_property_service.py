"""Layer property mutation helpers for ``SimpleCanvasService``."""

from __future__ import annotations

import logging
from typing import Any

from .events import Events
from .native_cmyk.capabilities import NativeCmykCapabilities

logger = logging.getLogger("loomstroke.core.canvas_service_simple")


class CanvasLayerPropertyService:
    """Coordinate non-history layer property mutations."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def _property_layers(self, layer_id: str) -> list[Any]:
        """Allow nested balloon units while retaining ordinary layer lookup semantics."""
        from .comic_panels.membership import UNIT_KEY  # noqa: PLC0415

        target = self._owner._find_layer_by_id_tree(layer_id)
        node = target
        seen = set()
        while node is not None and node.id not in seen:
            seen.add(node.id)
            if UNIT_KEY in (node.metadata or {}):
                return [target]
            node = self._owner._find_layer_by_id_tree(node.parent_id) if node.parent_id else None
        return self._owner._layers

    def set_layer_locked(self, layer_id: str, locked: bool) -> None:
        """Set layer lock state."""
        from .comic_panels.front_gate import set_managed_panel_lock  # noqa: PLC0415

        if set_managed_panel_lock(self._owner, layer_id, locked):
            return
        for layer in self._property_layers(layer_id):
            if layer.id == layer_id:
                if layer.locked == locked:
                    logger.debug("Layer %s lock already %s", layer.name, locked)
                    return
                layer.locked = locked
                logger.info("Layer %s lock set to %s", layer.name, locked)
                return
        logger.warning("Layer %s not found for lock update", layer_id)

    def set_layer_alpha_locked(self, layer_id: str, alpha_locked: bool) -> None:
        """Set layer alpha lock (transparency lock) state."""
        for layer in self._property_layers(layer_id):
            if layer.id == layer_id:
                if layer.alpha_lock == alpha_locked:
                    return
                layer.alpha_lock = alpha_locked
                logger.info("Layer %s alpha_lock set to %s", layer.name, alpha_locked)
                self._owner._publish_layer_event(
                    Events.LAYER_CHANGED,
                    layer=layer,
                    change_type="alpha_lock",
                    old_value=not alpha_locked,
                    new_value=alpha_locked,
                )
                return
        logger.warning("Layer %s not found for alpha_lock update", layer_id)

    def set_layer_opacity(self, layer_id: str, opacity: float) -> None:
        """Set layer opacity."""
        for layer in self._property_layers(layer_id):
            if layer.id == layer_id:
                layer.opacity = max(0.0, min(1.0, opacity))
                logger.debug(f"Layer {layer.name} opacity: {layer.opacity}")
                self._owner._invalidate_layer_cache()
                break

        self._owner._recompose()

    def set_layer_blend_mode(self, layer_id: str, mode: str) -> None:
        """Set layer blend mode."""
        blend_mode = self._owner._normalize_blend_mode(mode)

        layer = self._owner._find_layer_by_id_tree(layer_id)
        if layer is not None:
            if (
                any(
                    getattr(item, "native_cmyk", None) is not None
                    for item in self._owner._flatten_layers()
                )
                and blend_mode.value not in NativeCmykCapabilities.BLEND_MODES
            ):
                raise ValueError("This blend mode requires RGB")
            layer.blend_mode = blend_mode
            logger.debug(f"Layer {layer.name} blend mode: {blend_mode.value}")
            self._owner._invalidate_layer_cache()
            self._owner._recompose()
        else:
            logger.warning("Layer %s not found for blend mode update", layer_id)

    def set_clipping(self, layer_id: str, enabled: bool) -> bool:
        """Enable or disable clipping on a layer."""

        layer = self._owner._find_layer_by_id_tree(layer_id)
        if layer is None:
            return False
        layer.clipping = enabled
        self._owner._invalidate_layer_cache()
        self._owner._recompose()
        self._owner._publish_layer_event(
            Events.LAYER_CHANGED,
            layer=layer,
            change_type="clipping",
            new_value=enabled,
        )
        return True
