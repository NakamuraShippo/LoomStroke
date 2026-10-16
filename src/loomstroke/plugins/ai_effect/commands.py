"""GenerateAIEffectCommand — Undo/Redo for AI Effect generations.

Spec reference: ``AIeffect_design/04_python_design/data_model.md`` §5.

Design decisions from the spec:
- **Per-generation undo granularity**: Each completed generation is one
  Command. Parameter / slot changes in-between are draft state and do NOT
  push Commands — they only take effect at the next generation.
- **Self-owned image bytes**: The Command carries PNG-compressed before/after
  images in-process, not Gallery references. Gallery is a cache (500-entry
  LRU); Undo is a guaranteed restoration source. Two-layer redundancy is
  accepted: AI generation is infrequent and OS memory tolerates 20-50 copies.
- **No merge**: One generation = one Command. Rapid on_change triggers produce
  independent Commands; the existing undo-stack cap prunes old ones.

Phase 2-4 scope: dataclass + ``apply``/``revert`` interface against a
``GenerateAIEffectCanvas`` Protocol. Integration with the real
``SimpleCanvasService`` happens in Phase 4 (full controller wiring).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

from loomstroke.core.commands import Command
from loomstroke.plugins.ai_effect.data_model import SlotRef

if TYPE_CHECKING:
    from loomstroke.core.commands import Canvas
    from loomstroke.plugins.ai_effect.controller import AIEffectController
    from loomstroke.plugins.ai_effect.data_model import AIEffectState


class GenerateAIEffectCanvas(Protocol):
    """Minimal canvas surface used by :class:`GenerateAIEffectCommand`.

    The real ``SimpleCanvasService`` will satisfy this protocol once Phase 4
    wires the controller. Tests can provide a FakeCanvas without dragging
    in Qt.
    """

    def set_ai_effect_layer_state(
        self,
        layer_id: str,
        *,
        param_values: dict[str, Any],
        slot_refs: dict[str, SlotRef | None],
        param_ui_state: dict[str, Any] | None = None,
    ) -> bool:
        """Replace the AIEffectLayer's mutable state. Returns True on success."""
        ...

    def set_ai_effect_layer_image(
        self,
        layer_id: str,
        image_bytes: bytes | None,
    ) -> bool:
        """Replace the AIEffectLayer's image (``self`` mode). None clears."""
        ...

    def add_ai_effect_output_layers(
        self,
        parent_effect_id: str,
        new_layer_ids: list[str],
    ) -> bool:
        """Re-add output layers listed by id (for Redo). Returns True on success."""
        ...

    def remove_layers(self, layer_ids: list[str]) -> bool:
        """Remove layers by id (for Undo of ``new_layer_above`` output)."""
        ...


@dataclass
class GenerateAIEffectCommand(Command):
    """One AI Effect generation as an undoable Command.

    Populated by the AIEffectController on successful job completion
    (Phase 4). Failed / cancelled generations do NOT push Commands — they
    don't change layer state in a way that needs undo.
    """

    layer_id: str = ""
    """Target AIEffectLayer ``_Layer.id``."""

    mode: str = "self"
    """``"self"`` (overwrite AIEffectLayer image) or ``"new_layer_above"``
    (sibling output layer)."""

    # ---- Before state (restored by revert/undo) ----
    prev_param_values: dict[str, Any] = field(default_factory=dict)
    prev_slot_refs: dict[str, SlotRef | None] = field(default_factory=dict)
    prev_image_bytes: bytes | None = None
    """Populated only when ``mode == "self"`` (PNG-compressed)."""
    prev_output_layer_ids: list[str] = field(default_factory=list)
    """Populated only when ``mode == "new_layer_above"`` and either
    overwrite-mode or keep_history pruning removed layers before this gen."""

    # ---- After state (restored by apply/redo) ----
    new_param_values: dict[str, Any] = field(default_factory=dict)
    new_slot_refs: dict[str, SlotRef | None] = field(default_factory=dict)
    new_image_bytes: bytes | None = None
    new_output_layer_ids: list[str] = field(default_factory=list)

    # ---- Informational (not used for restoration) ----
    param_hash: str = ""
    """Gallery lookup key at the time of generation. Not used by
    apply/revert — stored for audit and UI display only."""
    finished_at: datetime | None = None

    #: Optional back-references that allow apply/revert to keep the
    #: AIEffectController state machine in sync with the undo/redo action
    #: (code review #3). When both are supplied, ``apply`` forces DONE and
    #: ``revert`` forces IDLE. Otherwise the state machine is left alone —
    #: suitable for tests that don't care about the controller.
    state_machine: AIEffectController | None = None
    ai_effect_state: AIEffectState | None = None

    def __post_init__(self) -> None:
        Command.__init__(self, merge_key=None)

    # ------------------------------------------------------------------
    # Command interface
    # ------------------------------------------------------------------

    def apply(self, canvas: Canvas) -> bool:
        """Apply the generation result (used for redo).

        Order matters: restore state first (param_values / slot_refs),
        then image or output layers. This keeps the AIEffectLayer visually
        consistent if the user observes an in-progress apply.

        If ``state_machine`` and ``ai_effect_state`` are wired, the
        controller is forced to DONE so the UI / subsequent generate
        triggers see a coherent state (code review #3).
        """
        gen_canvas = _as_generate_canvas(canvas)
        ok = gen_canvas.set_ai_effect_layer_state(
            self.layer_id,
            param_values=self.new_param_values,
            slot_refs=self.new_slot_refs,
        )
        if not ok:
            return False

        payload_ok = self._apply_canvas_payload(gen_canvas, redo=True)
        if payload_ok and self.state_machine is not None:
            self.state_machine.force_done(self.layer_id, state=self.ai_effect_state)
        return payload_ok

    def revert(self, canvas: Canvas) -> bool:
        """Restore pre-generation state (used for undo).

        If ``state_machine`` and ``ai_effect_state`` are wired, the
        controller is forced to IDLE so the UI / subsequent generate
        triggers see a coherent state (code review #3).
        """
        gen_canvas = _as_generate_canvas(canvas)
        ok = gen_canvas.set_ai_effect_layer_state(
            self.layer_id,
            param_values=self.prev_param_values,
            slot_refs=self.prev_slot_refs,
        )
        if not ok:
            return False

        payload_ok = self._apply_canvas_payload(gen_canvas, redo=False)
        if payload_ok and self.state_machine is not None:
            self.state_machine.force_idle(self.layer_id, state=self.ai_effect_state)
        return payload_ok

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply_canvas_payload(  # noqa: PLR0911
        self, gen_canvas: GenerateAIEffectCanvas, *, redo: bool
    ) -> bool:
        """Apply the image / output-layer mutations for redo (redo=True) or undo."""
        if self.mode == "self":
            image_bytes = self.new_image_bytes if redo else self.prev_image_bytes
            if not gen_canvas.set_ai_effect_layer_image(self.layer_id, image_bytes):
                return False
            if self.new_output_layer_ids:
                if redo:
                    return gen_canvas.add_ai_effect_output_layers(
                        self.layer_id, self.new_output_layer_ids
                    )
                return gen_canvas.remove_layers(self.new_output_layer_ids)
            return True
        if self.mode == "new_layer_above":
            if redo:
                if self.new_output_layer_ids:
                    return gen_canvas.add_ai_effect_output_layers(
                        self.layer_id, self.new_output_layer_ids
                    )
                return True
            # undo
            if self.new_output_layer_ids and not gen_canvas.remove_layers(
                self.new_output_layer_ids
            ):
                return False
            if self.prev_output_layer_ids:
                return gen_canvas.add_ai_effect_output_layers(
                    self.layer_id, self.prev_output_layer_ids
                )
            return True
        return False


def _as_generate_canvas(canvas: Canvas) -> GenerateAIEffectCanvas:
    """Structural adapter — raises AttributeError if canvas lacks required hooks.

    Phase 4 will replace this duck-cast with a real integration once
    SimpleCanvasService implements the 4 methods above.
    """
    required = (
        "set_ai_effect_layer_state",
        "set_ai_effect_layer_image",
        "add_ai_effect_output_layers",
        "remove_layers",
    )
    missing = [name for name in required if not callable(getattr(canvas, name, None))]
    if missing:
        raise AttributeError(
            f"Canvas does not implement required AI Effect hooks: {missing}. "
            "Phase 4 integration will add these to SimpleCanvasService."
        )
    return canvas  # type: ignore[return-value]
