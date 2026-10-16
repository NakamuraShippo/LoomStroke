"""Canvas service filter API and independent AI Effect API mixin."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeAlias

from loomstroke.core.canvas_filter_layer_service import _filter_identifier
from loomstroke.core.canvas_layer_model import _Layer
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

    class _CanvasServiceFilterApiBase(CanvasServiceAccessorsMixin):
        pass

else:

    class _CanvasServiceFilterApiBase:
        pass


def _filter_layer_service(canvas: Any) -> Any:
    """Resolve the canonical port while supporting legacy mixin owners."""
    getter = getattr(canvas, "_filter_apply", None)
    if not callable(getter):
        getter = canvas._effect_apply
    return getter()


class CanvasServiceFilterApiMixin(_CanvasServiceFilterApiBase):
    def _clear_filter_cache(self, layer: _Layer) -> None:
        """Clear filter cache — the only release path (D-2)."""
        _filter_layer_service(self).clear_filter_cache(layer)

    def _reset_filter_cache_bytes(self) -> None:
        """Recalculate cache usage by scanning all layers (F-2)."""
        _filter_layer_service(self).reset_filter_cache_bytes()

    def _get_or_create_reference_registry(self) -> Any:
        """Return the per-canvas ``LayerReferenceRegistry``, creating it on first use."""
        return self._ai_effect_state().get_or_create_reference_registry()

    def add_filter_layer(
        self,
        parent_layer_id: str | None,
        filter_type: str | None = None,
        params: dict[str, Any] | None = None,
        *,
        position: int = -1,
        effect_type: str | None = None,
    ) -> str | None:
        """Add an filter layer.

        *parent_layer_id* controls placement:
        - raster/vector ID → child filter (applies to that layer only)
        - group ID → sibling filter inside the group
        - ``None`` → sibling filter at root level

        Returns the new filter layer's ID, or ``None`` on failure.
        """
        filter_type = _filter_identifier(filter_type, effect_type, "filter_type")
        if any(getattr(layer, "native_cmyk", None) is not None for layer in self._flatten_layers()):
            logger.warning("Live RGB filters cannot be attached to a native CMYK document")
            return None
        return _filter_layer_service(self).add_filter_layer(
            parent_layer_id,
            filter_type,
            params,
            position=position,
        )

    def remove_filter_layer(
        self, filter_layer_id: str | None = None, *, effect_layer_id: str | None = None
    ) -> bool:
        """Remove an filter layer by ID. Returns True on success."""
        filter_layer_id = _filter_identifier(filter_layer_id, effect_layer_id, "filter_layer_id")
        return _filter_layer_service(self).remove_filter_layer(filter_layer_id)

    def set_filter_params(
        self,
        filter_layer_id: str | None = None,
        params: dict[str, Any] | None = None,
        *,
        effect_layer_id: str | None = None,
    ) -> bool:
        """Update the parameters of an filter layer. Returns True on success."""
        filter_layer_id = _filter_identifier(filter_layer_id, effect_layer_id, "filter_layer_id")
        if params is None:
            raise TypeError("params is required")
        return _filter_layer_service(self).set_filter_params(filter_layer_id, params)

    def get_filter_params(
        self, filter_layer_id: str | None = None, *, effect_layer_id: str | None = None
    ) -> dict[str, Any] | None:
        """Return a copy of the filter's params, or None if not found."""
        filter_layer_id = _filter_identifier(filter_layer_id, effect_layer_id, "filter_layer_id")
        return _filter_layer_service(self).get_filter_params(filter_layer_id)

    def add_ai_effect_layer(
        self,
        state: Any,
        *,
        name: str | None = None,
        parent_group_id: str | None = None,
        position: int = -1,
    ) -> str | None:
        """Create an AI Effect Layer hosting ``state``.

        ``state`` is typed ``Any`` so this module does not import
        :class:`AIEffectState` from the ``plugins.ai_effect`` package —
        the core → plugin dependency direction documented at ``_Layer.ai_effect``
        (L220-227) must stay intact. Callers construct the state from the
        plugin side and hand it in opaque.

        Layering contract: AI Effect Layers are top-level-style layers
        (like raster / vector), not child filters of another layer. They
        may be nested inside a group via ``parent_group_id``. Passing a
        non-group parent id returns ``None`` — mirrors the guard pattern
        in :meth:`add_filter_layer` (L5764-5770).

        Returns the new layer id, or ``None`` when the parent resolution
        fails.
        """
        if any(getattr(layer, "native_cmyk", None) is not None for layer in self._flatten_layers()):
            logger.warning("AI Effect layers require an RGB document")
            return None
        return self._ai_effect_state().add_ai_effect_layer(
            state,
            name=name,
            parent_group_id=parent_group_id,
            position=position,
        )

    def set_ai_effect_layer_state(
        self,
        layer_id: str,
        *,
        param_values: dict[str, Any],
        slot_refs: dict[str, Any],
        param_ui_state: dict[str, Any] | None = None,
    ) -> bool:
        """Replace an AI Effect Layer's mutable state.

        Both dicts are shallow-copied on write so callers passing shared
        references do not observe mutations when we later edit in place.
        ``param_ui_state`` is optional and preserves the existing UI metadata
        when omitted.
        Returns ``False`` when the layer is missing, not an ``ai_effect``
        type, or has no ``AIEffectState`` attached (possible after a
        malformed load).

        Registry sync on slot_refs diff happens in Phase 2-6-3 — this
        method currently replaces the dict without notifying the
        ``LayerReferenceRegistry``.
        """
        return self._ai_effect_state().set_ai_effect_layer_state(
            layer_id,
            param_values=param_values,
            slot_refs=slot_refs,
            param_ui_state=param_ui_state,
        )

    def set_ai_effect_layer_output_mode_override(
        self,
        layer_id: str,
        override: str | None,
    ) -> bool:
        """Set the per-layer output-mode override (Phase 6-h common setting).

        ``override`` accepts ``"self"`` / ``"new_layer_above"`` to force
        every output of this layer to the given mode regardless of what
        the preset author picked, or ``None`` to clear the override and
        let each output follow the preset's per-output ``mode``. Any
        other value is rejected (returns False) so a typo does not
        silently disable the dispatch.

        Returns ``False`` when the layer is missing, not an
        ``ai_effect`` type, has no attached state, or ``override`` is an
        unrecognised string.
        """
        return self._ai_effect_state().set_ai_effect_layer_output_mode_override(layer_id, override)

    def set_ai_effect_layer_image(
        self,
        layer_id: str,
        image_bytes: bytes | None,
    ) -> bool:
        """Replace an AI Effect Layer's ``image`` (``self`` output mode).

        Feeds a generated PNG into the layer; ``None`` clears the image
        (used by :class:`GenerateAIEffectCommand` revert).

        Returns ``False`` on unknown id, wrong layer type, or PNG decode
        failure. Decode failures are logged at WARNING — they indicate a
        Gateway contract violation (caller promised valid PNG bytes) and
        the layer state must not be corrupted by a half-loaded image.
        """
        return _filter_layer_service(self).set_ai_effect_layer_image(layer_id, image_bytes)

    def remove_layers(self, layer_ids: list[str]) -> bool:
        """Bulk-remove layers, tombstoning them for potential Redo.

        Phase 2-6-2 extension of the Protocol ``GenerateAIEffectCanvas``.
        Each removed ``_Layer`` is parked in ``_ai_effect_output_tombstones``
        so :meth:`add_ai_effect_output_layers` can resurrect it on a
        subsequent Redo. Missing IDs are silently skipped to keep
        Undo/Redo idempotent: the caller should never need to know which
        layers have already been cleaned up.

        Returns ``True`` if at least one layer was removed, ``False``
        when no ID was found (also when ``layer_ids`` is empty). Emits a
        single ``LAYER_REMOVED`` event with ``change_type=
        "ai_effect_outputs_removed"`` after a successful bulk delete —
        deliberately one event rather than one per ID so bulk Undo /
        Redo does not flood the event bus.
        """
        return self._layer_tree().remove_layers(layer_ids)

    def add_ai_effect_output_layers(
        self,
        parent_effect_id: str,
        new_layer_ids: list[str],
    ) -> bool:
        """Re-insert previously-tombstoned layers just above their parent.

        Phase 2-6-2 Protocol method. Operates purely against the
        ``_ai_effect_output_tombstones`` cache populated by
        :meth:`remove_layers` — there is no "create new layer" path here
        on purpose. An initial generation is expected to add the output
        layers through a different API (future Phase 2-6 work); this
        method only handles Undo/Redo resurrection.

        Returns ``False`` when:
        - the parent id is missing or is not an ``ai_effect`` layer
        - any of ``new_layer_ids`` has no tombstone entry (fail-fast;
          already-restored entries remain in place)
        """
        return _filter_layer_service(self).add_ai_effect_output_layers(
            parent_effect_id, new_layer_ids
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
        """Mint a new raster layer carrying a freshly-generated image.

        Used by :class:`AIEffectGenerationController` on success when one
        or more outputs declare ``mode="new_layer_above"``. The layer is
        inserted **just above** its parent AI Effect layer (same sibling
        list) and tagged with :class:`LayerOrigin` so the LayerPanel's
        green-border accent kicks in and
        ``_cascade_ai_effect_refs_on_delete`` can discriminate generated
        outputs from user-authored layers.

        Distinct from :meth:`add_ai_effect_output_layers`:

        - ``add_ai_effect_output_layers`` is Undo / Redo resurrection —
          it pulls from the tombstone cache, never allocates pixels.
        - ``create_ai_effect_output_layer`` is the *initial* creation
          path that the Phase 2-6-2 TODO comment deferred. This is the
          missing piece that made first-time ``new_layer_above``
          generations appear to "lose" their outputs — the JobResult
          carried PNG bytes but nothing materialised them onto the
          canvas.

        Returns the new layer id on success, or ``None`` when:
        - the parent id is unknown or not an ``ai_effect`` layer
        - ``png_bytes`` fails to decode (logged at WARNING)
        """
        return _filter_layer_service(self).create_ai_effect_output_layer(
            parent_effect_id,
            name=name,
            png_bytes=png_bytes,
            output_internal_id=output_internal_id,
            generation_index=generation_index,
        )

    def _sync_registry_for_slot_refs(
        self,
        referrer_id: str,
        old_refs: Mapping[str, Any],
        new_refs: Mapping[str, Any],
    ) -> None:
        """Diff old vs new slot_refs and push (un)registrations through.

        Called from ``add_ai_effect_layer`` (with empty ``old_refs``) and
        ``set_ai_effect_layer_state`` (with the pre-write state). Reads
        the target layer id via ``getattr(ref, 'layer_id', None)`` so a
        dict-stand-in used by tests works alongside the real ``SlotRef``
        dataclass.

        A no-op when both diffs are empty — keeps the overhead on the
        common "nothing changed" path to a handful of Python attribute
        reads.
        """
        self._ai_effect_state().sync_registry_for_slot_refs(referrer_id, old_refs, new_refs)

    def _cascade_ai_effect_refs_on_delete(self, layer_id: str) -> None:
        """Null out AI Effect slot_refs that pointed at the deleted layer.

        Invoked from ``remove_layer`` and ``remove_layers`` after the
        target has been detached from the tree but before the
        LAYER_REMOVED event fires. For each referrer / slot pair from
        the registry:

        - set ``AIEffectState.slot_refs[slot_id] = None``
        - unregister the pair
        - emit ``LAYER_CHANGED`` with ``change_type="ai_effect_slot_unlinked"``
          so downstream UI (required-slot pulsation etc.) sees the
          transition

        Silent no-op when the deleted layer is not a registered target
        — avoids the lazy registry creation on the hot path for the
        typical raster delete.
        """
        self._ai_effect_state().cascade_ai_effect_refs_on_delete(layer_id)

    def _clear_effect_cache(self, layer: _Layer) -> None:
        self._clear_filter_cache(layer)

    def _reset_effect_cache_bytes(self) -> None:
        self._reset_filter_cache_bytes()

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


CanvasServiceEffectApiMixin = CanvasServiceFilterApiMixin
_CanvasServiceEffectApiBase = _CanvasServiceFilterApiBase
