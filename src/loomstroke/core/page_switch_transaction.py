"""Checked standard page installation and assignment-only failure recovery."""

from __future__ import annotations

import logging
from enum import Enum, auto
from inspect import getattr_static
from typing import Any

from .canvas_history_clear_continuation import _HistoryClearContinuation, _HistoryClearSuperseded
from .canvas_service_simple_image_api import _DEFAULT_UPDATE_CANVAS_CONFIG
from .canvas_service_simple_layer_api import _DEFAULT_REPLACE_LAYERS
from .page_mutation_preflight import PageMutationPreflight
from .page_switch_preparation import _prepare_outgoing_switch
from .page_switch_recovery import _PageSwitchRecovery

logger = logging.getLogger(__name__)


class _PageSwitchOutcome(Enum):
    COMMITTED = auto()
    REFUSED = auto()
    SUPERSEDED = auto()


def _supports_page_recovery(canvas: Any) -> bool:
    """Unknown public installers keep their original checked legacy path."""
    return (
        getattr_static(canvas, "update_canvas_config", None) is _DEFAULT_UPDATE_CANVAS_CONFIG
        and getattr_static(canvas, "replace_layers", None) is _DEFAULT_REPLACE_LAYERS
    )


class _PageSwitchTransaction:
    """One call's scope, expected active page, and model recovery boundary."""

    def __init__(
        self,
        service: Any,
        scope: PageMutationPreflight,
        source_id: str,
        target: Any,
        failure_context: tuple[str, str] | None = None,
    ):
        self._service, self._scope, self._source_id = service, scope, source_id
        self._target, self._target_id = target, target.page.id
        self._expected_active = source_id
        self._canvas = service._canvas
        self._failure_context = failure_context or ("set_active", self._target_id)

    def _is_current(self) -> bool:
        return self._scope.is_current() and self._service._active_page_id == self._expected_active

    def run(self) -> _PageSwitchOutcome:
        try:
            recovery = _prepare_outgoing_switch(
                self._service,
                self._scope,
                self._source_id,
                self._target_id,
                retain_state=True,
                failure_context=self._failure_context,
            )
        except _HistoryClearSuperseded:
            return _PageSwitchOutcome.SUPERSEDED
        if not isinstance(recovery, _PageSwitchRecovery) or not self._is_current():
            return (
                _PageSwitchOutcome.REFUSED if self._is_current() else _PageSwitchOutcome.SUPERSEDED
            )
        return self._run_deferred(recovery)

    def _run_deferred(self, recovery: _PageSwitchRecovery) -> _PageSwitchOutcome:
        baseline = self._canvas._defer_recompose_depth
        self._defer_document = getattr(self._canvas, "_doc", None)
        context = _HistoryClearContinuation(self._canvas)
        try:
            self._canvas.defer_recompose()
        except Exception as exc:
            if context.is_current():
                self._resume_owned_level(baseline)
            if not self._is_current():
                return _PageSwitchOutcome.SUPERSEDED
            return self._notify_refused(exc)
        try:
            if not self._is_current():
                return _PageSwitchOutcome.SUPERSEDED
            outcome = self._run_model(recovery)
        finally:
            # Emit only after attach or model recovery. Exceptions/new edits
            # from this notification are outside the old rollback boundary.
            self._resume_owned_level(baseline)
        return outcome if self._is_current() else _PageSwitchOutcome.SUPERSEDED

    def _resume_owned_level(self, baseline: int) -> None:
        canvas = self._canvas
        # Close/image replacement resets the old counter with the old document.
        # A matching number in its replacement can belong to a new lease.
        if getattr(canvas, "_doc", None) is not self._defer_document:
            return
        depth = canvas._defer_recompose_depth
        if depth <= baseline:
            return
        context = _HistoryClearContinuation(canvas)
        pages, active = self._service._pages, self._service._active_page_id
        clock = self._service._history_sequence
        sequence = clock._next
        try:
            canvas.resume_recompose()
        except Exception:
            # Match the existing history restore's single-level fallback. A
            # callback may have consumed our level and opened a new owner's
            # level at the same numeric depth: never consume that replacement.
            if (
                context.is_current()
                and self._service._pages is pages
                and self._service._active_page_id == active
                and self._service._history_sequence is clock
                and clock._next == sequence
                and canvas._defer_recompose_depth == depth
            ):
                canvas._defer_recompose_depth -= 1
            logger.exception("Page switch redraw resume failed after model settlement")

    def _run_model(self, recovery: _PageSwitchRecovery) -> _PageSwitchOutcome:
        try:
            installed = self._install(recovery)
        except _HistoryClearSuperseded:
            return _PageSwitchOutcome.SUPERSEDED
        except Exception as exc:
            if not self._is_current():
                return _PageSwitchOutcome.SUPERSEDED
            recovery.restore()
            self._expected_active = self._source_id
            return self._notify_refused(exc)
        return _PageSwitchOutcome.COMMITTED if installed else _PageSwitchOutcome.SUPERSEDED

    def _install(self, recovery: _PageSwitchRecovery) -> bool:
        service = self._service
        bundle = service._page_history._detach_prepared_active_history(self._source_id, keep=True)
        recovery.take_source(bundle)
        if not self._is_current():
            return False
        # Detach notifications have completed. Convert the latest target once,
        # after CHANGING/close/capture callbacks, before any target pixels move.
        prepared = service._prepare_page_layers(
            self._target.page, service._resolve_canvas_spec(self._target.page)
        )
        if not self._is_current():
            return False
        self._expected_active = self._target_id
        service._active_page_id = self._target_id
        service._apply_page_to_canvas(self._target.page, prepared)
        if not self._is_current():
            return False
        service._page_history.attach_history_for(self._target_id)
        return self._is_current()

    def _notify_refused(self, error: Exception) -> _PageSwitchOutcome:
        # The model is already restored. Redraw/history callbacks may fail or
        # select another document/page; never retry the failed page installer.
        service = self._service
        for notify in (service._canvas._recompose, service._canvas._emit_history_changed):
            try:
                notify()
            except Exception:
                logger.exception("Page switch recovery notification failed")
            if not self._is_current():
                return _PageSwitchOutcome.SUPERSEDED
        logger.exception("Failed to install page %s", self._target_id)
        service._emit_page_op_failed(*self._failure_context, str(error))
        return _PageSwitchOutcome.REFUSED


def _switch_with_recovery(
    service: Any,
    scope: PageMutationPreflight,
    source_id: str,
    target: Any,
    *,
    failure_context: tuple[str, str] | None = None,
) -> _PageSwitchOutcome:
    return _PageSwitchTransaction(service, scope, source_id, target, failure_context).run()
