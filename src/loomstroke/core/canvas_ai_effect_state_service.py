"""AI Effect layer state helpers for SimpleCanvasService."""

from __future__ import annotations

import copy
import logging
import time
from collections.abc import Callable, Mapping
from typing import Any

logger = logging.getLogger("loomstroke.core.canvas_service_simple")


class CanvasAIEffectStateService:
    """Manage AI Effect layer state and reference-registry side effects."""

    def __init__(
        self,
        canvas: Any,
        *,
        layer_type: type[Any],
        events: Any,
        time_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        self._canvas = canvas
        self._layer_type = layer_type
        self._events = events
        self._time_ns = time_ns

    def add_ai_effect_layer(
        self,
        state: Any,
        *,
        name: str | None = None,
        parent_group_id: str | None = None,
        position: int = -1,
    ) -> str | None:
        if parent_group_id is None:
            target_list = self._canvas._layers
        else:
            parent = self._canvas._find_layer_by_id_tree(parent_group_id)
            if parent is None or parent.layer_type != "group":
                return None
            target_list = parent.children

        layer_id = f"ai-effect-{self._time_ns()}"
        layer = self._layer_type(
            id=layer_id,
            name=name if name is not None else "AI Effect",
            layer_type="ai_effect",
            ai_effect=state,
            image=None,
        )
        layer.parent_id = parent_group_id

        if position < 0 or position > len(target_list):
            target_list.append(layer)
        else:
            target_list.insert(position, layer)

        initial_slot_refs = getattr(state, "slot_refs", None) if state is not None else None
        if isinstance(initial_slot_refs, dict) and initial_slot_refs:
            self.sync_registry_for_slot_refs(layer_id, {}, initial_slot_refs)

        self._canvas._invalidate_layer_cache()
        self._canvas._recompose()
        logger.info("AI Effect Layer created: %s", layer_id)
        self._canvas._publish_layer_event(
            self._events.LAYER_CHANGED,
            layer=layer,
            change_type="ai_effect_added",
        )
        return layer_id

    def set_ai_effect_layer_state(
        self,
        layer_id: str,
        *,
        param_values: dict[str, Any],
        slot_refs: dict[str, Any],
        param_ui_state: dict[str, Any] | None = None,
    ) -> bool:
        layer = self._canvas._find_layer_by_id_tree(layer_id)
        if layer is None or layer.layer_type != "ai_effect" or layer.ai_effect is None:
            return False

        old_slot_refs: dict[str, Any] = {}
        existing_refs = getattr(layer.ai_effect, "slot_refs", None)
        if isinstance(existing_refs, dict):
            old_slot_refs = dict(existing_refs)

        layer.ai_effect.param_values = dict(param_values)
        layer.ai_effect.slot_refs = dict(slot_refs)
        if param_ui_state is not None:
            layer.ai_effect.param_ui_state = copy.deepcopy(param_ui_state)

        self.sync_registry_for_slot_refs(layer_id, old_slot_refs, slot_refs)
        self._canvas._invalidate_layer_cache()
        self._canvas._publish_layer_event(
            self._events.LAYER_CHANGED,
            layer=layer,
            change_type="ai_effect_state_changed",
        )
        return True

    def set_ai_effect_layer_output_mode_override(
        self,
        layer_id: str,
        override: str | None,
    ) -> bool:
        if override is not None and override not in ("self", "new_layer_above"):
            return False
        layer = self._canvas._find_layer_by_id_tree(layer_id)
        if layer is None or layer.layer_type != "ai_effect" or layer.ai_effect is None:
            return False
        if layer.ai_effect.output_mode_override == override:
            return True
        layer.ai_effect.output_mode_override = override
        self._canvas._invalidate_layer_cache()
        self._canvas._publish_layer_event(
            self._events.LAYER_CHANGED,
            layer=layer,
            change_type="ai_effect_output_mode_override_changed",
        )
        return True

    def get_or_create_reference_registry(self) -> Any:
        registry = getattr(self._canvas, "_reference_registry", None)
        if registry is None:
            from loomstroke.plugins.ai_effect.reference_registry import (  # noqa: PLC0415
                LayerReferenceRegistry,
            )

            registry = LayerReferenceRegistry()
            self._canvas._reference_registry = registry
        return registry

    def sync_registry_for_slot_refs(
        self,
        referrer_id: str,
        old_refs: Mapping[str, Any],
        new_refs: Mapping[str, Any],
    ) -> None:
        slot_ids = set(old_refs.keys()) | set(new_refs.keys())
        if not slot_ids:
            return
        registry: Any = None
        for slot_id in slot_ids:
            old_ref = old_refs.get(slot_id)
            new_ref = new_refs.get(slot_id)
            old_target = getattr(old_ref, "layer_id", None) if old_ref is not None else None
            new_target = getattr(new_ref, "layer_id", None) if new_ref is not None else None
            if old_target == new_target:
                continue
            if registry is None:
                registry = self.get_or_create_reference_registry()
            if old_target is not None:
                registry.unregister(referrer_id, slot_id, old_target)
                logger.debug(
                    "Registry: %s slot %s unregistered from %s",
                    referrer_id,
                    slot_id,
                    old_target,
                )
            if new_target is not None:
                registry.register(referrer_id, slot_id, new_target)
                logger.debug(
                    "Registry: %s slot %s registered to %s",
                    referrer_id,
                    slot_id,
                    new_target,
                )

    def cascade_ai_effect_refs_on_delete(self, layer_id: str) -> None:
        registry = getattr(self._canvas, "_reference_registry", None)
        if registry is None:
            return
        referrers = registry.referrers_of(layer_id)
        if not referrers:
            return
        for referrer_id, slot_id in referrers:
            ai_effect_layer = self._canvas._find_layer_by_id_tree(referrer_id)
            if ai_effect_layer is None or ai_effect_layer.ai_effect is None:
                registry.unregister(referrer_id, slot_id, layer_id)
                continue
            slot_refs = getattr(ai_effect_layer.ai_effect, "slot_refs", None)
            if isinstance(slot_refs, dict):
                slot_refs[slot_id] = None
            registry.unregister(referrer_id, slot_id, layer_id)
            logger.info(
                "AI Effect %s slot %s unlinked from deleted layer %s",
                referrer_id,
                slot_id,
                layer_id,
            )
            self._canvas._publish_layer_event(
                self._events.LAYER_CHANGED,
                layer=ai_effect_layer,
                change_type="ai_effect_slot_unlinked",
            )
