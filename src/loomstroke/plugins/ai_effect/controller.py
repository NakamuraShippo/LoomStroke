"""AIEffectController — state machine skeleton (Phase 1-6).

Spec reference: ``AIeffect_design/04_python_design/data_model.md`` §1 (state
machine) and ``gateway_protocol.md`` §4 (execution flow).

Phase 1 scope: state transitions only. No actual submit / Gallery /
Undo integration — those arrive in Phase 4 (full controller wiring).

State machine (data_model.md §1):

.. code-block:: text

    idle --[generate]--> queued --[gateway ack]--> generating --[success]--> done
      ^                    |                          |
      |                    +---[cancel / error]-------+---> error or idle
      |                                                      |
      +------[user fixes and retries]------------------------+

"""

from __future__ import annotations

import logging
from enum import Enum
from typing import TYPE_CHECKING

from loomstroke.plugins.ai_effect.errors import AIError

if TYPE_CHECKING:
    from loomstroke.plugins.ai_effect.data_model import AIEffectState

logger = logging.getLogger(__name__)


class LayerState(str, Enum):
    """Per-layer execution state (AIEffectState.state).

    Stored as string values so persistence (future .lstroke integration)
    stays stable. New states may be appended, existing values MUST NOT change.
    """

    IDLE = "idle"
    QUEUED = "queued"
    GENERATING = "generating"
    DONE = "done"
    ERROR = "error"


class InvalidTransitionError(RuntimeError):
    """Raised when a transition is attempted from an incompatible state."""


# Allowed transitions per data_model.md §1 state machine.
# Maps (from_state, event) → to_state.
_TRANSITIONS: dict[tuple[LayerState, str], LayerState] = {
    (LayerState.IDLE, "generate"): LayerState.QUEUED,
    (LayerState.QUEUED, "ack"): LayerState.GENERATING,
    (LayerState.QUEUED, "cancel"): LayerState.IDLE,
    (LayerState.QUEUED, "error"): LayerState.ERROR,
    (LayerState.GENERATING, "success"): LayerState.DONE,
    (LayerState.GENERATING, "cancel"): LayerState.IDLE,
    (LayerState.GENERATING, "error"): LayerState.ERROR,
    (LayerState.DONE, "generate"): LayerState.QUEUED,
    (LayerState.ERROR, "generate"): LayerState.QUEUED,
    (LayerState.ERROR, "cancel"): LayerState.IDLE,
    (LayerState.ERROR, "dismiss"): LayerState.IDLE,
}


def can_transition(from_state: LayerState, event: str) -> bool:
    """Return True if ``event`` is legal from ``from_state``."""
    return (from_state, event) in _TRANSITIONS


def next_state(from_state: LayerState, event: str) -> LayerState:
    """Resolve (from_state, event) → to_state, raising if illegal.

    Use ``can_transition`` first if you need to branch without exceptions.
    """
    try:
        return _TRANSITIONS[(from_state, event)]
    except KeyError as exc:
        raise InvalidTransitionError(
            f"No transition from {from_state.value!r} on event {event!r}. "
            f"Valid events: {sorted(e for s, e in _TRANSITIONS if s == from_state)}"
        ) from exc


class AIEffectController:
    """Skeleton controller — state machine only.

    Full implementation (submit flow, Gallery cache, Undo push, retry) lands
    in Phase 4. Phase 1 provides the per-layer state dict and transition
    helpers so later phases can build on a tested foundation.

    Thread safety: this class is meant to run on the Qt main thread only.
    Async work is driven from the outside via ``await controller.step(...)``
    in Phase 4; do not add threading primitives here.
    """

    def __init__(self) -> None:
        # layer_id → current state. Populated on first transition.
        self._states: dict[str, LayerState] = {}
        # layer_id → most recent error (None when not in ERROR state)
        self._errors: dict[str, AIError] = {}

    def state_of(self, layer_id: str) -> LayerState:
        """Return the current state for a layer (IDLE if never seen)."""
        return self._states.get(layer_id, LayerState.IDLE)

    def last_error(self, layer_id: str) -> AIError | None:
        """Return the most recent error, or None if layer is not in ERROR state."""
        if self.state_of(layer_id) is not LayerState.ERROR:
            return None
        return self._errors.get(layer_id)

    def dispatch(
        self,
        layer_id: str,
        event: str,
        *,
        error: AIError | None = None,
        state: AIEffectState | None = None,
    ) -> LayerState:
        """Advance the state machine for ``layer_id`` by ``event``.

        Args:
            layer_id: Target layer UUID.
            event: One of the events in ``_TRANSITIONS`` for the current state.
            error: Required when ``event == "error"``; stored as ``last_error``.
            state: Optional ``AIEffectState`` to keep in sync. When supplied,
                ``state.state`` is updated to the new LayerState value after
                a successful transition. Passing this on every dispatch is the
                only way to keep the two state sources consistent (the
                per-layer ``AIEffectState.state`` field and the Controller's
                internal ``_states`` dict). See code review #1 / #3.

        Returns the new state. Raises ``InvalidTransitionError`` on illegal
        transition (caller should filter with ``can_transition`` when
        unsure, to avoid exception-based control flow).
        """
        current = self.state_of(layer_id)
        new_state = next_state(current, event)
        self._states[layer_id] = new_state
        if state is not None:
            state.state = new_state.value

        if event == "error":
            if error is None:
                raise ValueError("dispatch(event='error') requires an AIError")
            self._errors[layer_id] = error
        elif new_state is not LayerState.ERROR:
            self._errors.pop(layer_id, None)

        logger.debug(
            "AIEffect layer %s: %s --[%s]--> %s",
            layer_id,
            current.value,
            event,
            new_state.value,
        )
        return new_state

    def force_error(
        self,
        layer_id: str,
        error: AIError,
        *,
        state: AIEffectState | None = None,
    ) -> None:
        """Force the layer into ERROR regardless of its current state.

        Used for validation / precondition failures where the layer may be
        in any prior state (IDLE / DONE / already ERROR). Avoids the
        "no transition from ERROR on event 'error'" trap (code review #2).

        Unlike ``dispatch("error")``, this is not subject to the transition
        table — it is a forced reset intended for UI-facing errors.
        """
        self._states[layer_id] = LayerState.ERROR
        self._errors[layer_id] = error
        if state is not None:
            state.state = LayerState.ERROR.value
        logger.debug("AIEffect layer %s: force_error -> %r", layer_id, error.class_.value)

    def force_done(
        self,
        layer_id: str,
        *,
        state: AIEffectState | None = None,
    ) -> None:
        """Force the layer into DONE (used for Command redo, code review #3)."""
        self._states[layer_id] = LayerState.DONE
        self._errors.pop(layer_id, None)
        if state is not None:
            state.state = LayerState.DONE.value

    def force_idle(
        self,
        layer_id: str,
        *,
        state: AIEffectState | None = None,
    ) -> None:
        """Force the layer into IDLE (used for Command undo, code review #3)."""
        self._states[layer_id] = LayerState.IDLE
        self._errors.pop(layer_id, None)
        if state is not None:
            state.state = LayerState.IDLE.value

    def reset(self, layer_id: str) -> None:
        """Force-reset a layer to IDLE (e.g. on layer deletion).

        Unlike ``force_idle``, this also removes the tracking entry so
        ``state_of`` returns the default IDLE for a fresh layer.
        """
        self._states.pop(layer_id, None)
        self._errors.pop(layer_id, None)

    def active_layer_ids(self) -> list[str]:
        """Return layers not in IDLE (i.e. anything in-flight or errored)."""
        return [lid for lid, st in self._states.items() if st is not LayerState.IDLE]
