"""LayerReferenceRegistry — bidirectional tracking of SlotRef → _Layer links.

Spec reference: ``AIeffect_design/04_python_design/data_model.md`` §4.

The AI Effect Layer mockup ([layers_panel v6]) shows a "x2 reference count"
badge on layers that are *referenced by* AIEffectLayers. This registry is
the source of truth for those counts and powers the cascade logic when a
referenced layer is deleted.

Usage pattern (integration with AIEffectController in Phase 4):

    registry = LayerReferenceRegistry()

    # When user drops layer 'src-1' into AIEffectLayer 'fx-1' slot 'slot_0':
    registry.register(referrer_id="fx-1", slot_id="slot_0", target_id="src-1")

    # When UI needs to render "xN" badge on src-1:
    count = registry.ref_count("src-1")  # → 1

    # When 'src-1' is deleted — the controller queries and clears slot_refs:
    for referrer_id, slot_id in registry.referrers_of("src-1"):
        # set AIEffectState.slot_refs[slot_id] = None, repaint UI, etc.
        registry.unregister(referrer_id, slot_id, "src-1")

Phase 2-3 provides the registry only — wiring to AIEffectController and
canvas_service delete events is Phase 4 work.
"""

from __future__ import annotations


class LayerReferenceRegistry:
    """Tracks which AIEffectLayer slots reference which source layers.

    Design choices:
    - Keyed by layer UUID (matches _Layer.id, weakref-free)
    - Back-reference only (target → [(referrer, slot_id), ...]); forward
      lookup lives in AIEffectState.slot_refs already
    - ``register`` is idempotent on the same triple (no double-counting)
    - ``referrers_of`` returns a defensive copy so callers can mutate during
      iteration (common pattern when deleting cascades)
    """

    def __init__(self) -> None:
        self._back_refs: dict[str, list[tuple[str, str]]] = {}

    def register(self, referrer_id: str, slot_id: str, target_id: str) -> None:
        """Record that ``referrer_id`` references ``target_id`` via ``slot_id``.

        Idempotent: calling twice with the same triple does not add a
        duplicate entry.
        """
        entries = self._back_refs.setdefault(target_id, [])
        triple = (referrer_id, slot_id)
        if triple not in entries:
            entries.append(triple)

    def unregister(self, referrer_id: str, slot_id: str, target_id: str) -> bool:
        """Remove a specific reference. Returns True if an entry was removed."""
        entries = self._back_refs.get(target_id)
        if not entries:
            return False
        triple = (referrer_id, slot_id)
        try:
            entries.remove(triple)
        except ValueError:
            return False
        if not entries:
            del self._back_refs[target_id]
        return True

    def unregister_referrer(self, referrer_id: str) -> list[tuple[str, str]]:
        """Remove all references *originating from* ``referrer_id``.

        Called when the referrer AIEffectLayer itself is deleted. Returns a
        list of ``(target_id, slot_id)`` pairs that were removed so the
        caller can repaint or log affected layers.
        """
        removed: list[tuple[str, str]] = []
        for target_id in list(self._back_refs.keys()):
            entries = self._back_refs[target_id]
            remaining = [(r, s) for (r, s) in entries if r != referrer_id]
            for r, s in entries:
                if r == referrer_id:
                    removed.append((target_id, s))
            if remaining:
                self._back_refs[target_id] = remaining
            else:
                del self._back_refs[target_id]
        return removed

    def ref_count(self, layer_id: str) -> int:
        """Return how many AIEffectLayer slots reference ``layer_id``."""
        return len(self._back_refs.get(layer_id, ()))

    def referrers_of(self, layer_id: str) -> list[tuple[str, str]]:
        """Return ``[(referrer_id, slot_id), ...]`` referencing ``layer_id``.

        Returns a defensive copy — safe to mutate the registry during
        iteration of the returned list.
        """
        return list(self._back_refs.get(layer_id, ()))

    def clear(self) -> None:
        """Remove all entries. Primarily for test teardown."""
        self._back_refs.clear()

    def __len__(self) -> int:
        """Return the total number of (referrer, slot) pairs across all targets."""
        return sum(len(entries) for entries in self._back_refs.values())
