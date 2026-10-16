"""Cancels a submitted job through an injected canceller; never reverts a finished commit."""

from __future__ import annotations

from collections.abc import Callable

from .sam3_async_submit_state import Sam3AsyncSubmitState
from .sam3_cancel_result import Sam3CancelResult

__all__ = ["Sam3SubmitCancelBridge"]

Canceller = Callable[[str], bool]
"""``cancel(effect_layer_id_or_job_id) -> bool`` (e.g. ``GenerationFlow.cancel_layer``)."""

Outcome = tuple[Sam3AsyncSubmitState | None, Sam3CancelResult]


class Sam3SubmitCancelBridge:
    def __init__(self, canceller: Canceller | None) -> None:
        self._canceller = canceller

    @property
    def supported(self) -> bool:
        return callable(self._canceller)

    def cancel(self, state: Sam3AsyncSubmitState | None) -> Outcome:
        blocked = self._precheck(state)
        if blocked is not None:
            return blocked
        assert state is not None and self._canceller is not None
        target = state.effect_layer_id or state.job_id or ""
        try:
            cancelled = bool(self._canceller(target))
        except Exception:
            return state, Sam3CancelResult(False, state.status, "cancel_failed")
        if not cancelled:
            # Nothing in flight any more: the commit is finishing; never tear it down.
            finishing = state.with_status("finishing", cancelable=False)
            return finishing, Sam3CancelResult(False, "finishing", "submit_not_inflight")
        return state.with_status("cancelled", cancelable=False), Sam3CancelResult(True, "cancelled")

    def _precheck(self, state: Sam3AsyncSubmitState | None) -> Outcome | None:
        if state is None:
            return None, Sam3CancelResult(False, "none", "submit_state_missing")
        if state.is_terminal:
            return state, Sam3CancelResult(False, state.status, "submit_already_terminal")
        if state.status == "finishing":
            return state, Sam3CancelResult(False, state.status, "submit_finishing")
        if not self.supported:
            unsupported = state.with_status(state.status, cancelable=False)
            return unsupported, Sam3CancelResult(False, state.status, "cancel_unsupported")
        if not (state.effect_layer_id or state.job_id):
            return state, Sam3CancelResult(False, state.status, "submit_target_missing")
        return None
