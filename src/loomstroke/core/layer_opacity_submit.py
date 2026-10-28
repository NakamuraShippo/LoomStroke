"""Commit one opacity scalar before publishing history-backed model changes."""

from __future__ import annotations

import logging
from contextlib import nullcontext
from inspect import getattr_static
from typing import Any, cast

from .commands_layer_lookup import find_layer
from .commands_layer_opacity import SetLayerOpacityCommand, _is_standard_opacity_command
from .document_capture_guard import is_document_capture_blocked
from .events import DocumentChangeEvent
from .layer_command_commit_context import _CommandHistoryReceipt, _LayerCommandContext
from .layer_opacity_preview_boundary import _opacity_command_owner

logger = logging.getLogger(__name__)


def _standard_opacity_route(canvas: Any, command: Any) -> bool:
    from .canvas_service_simple_layer_api import _DEFAULT_OPACITY_LAYER_HOOKS  # noqa: PLC0415

    return _is_standard_opacity_command(command) and all(
        getattr_static(canvas, name, None) is original
        for name, original in _DEFAULT_OPACITY_LAYER_HOOKS.items()
    )


class _OpacityScope(_LayerCommandContext):
    def emit_document_change(self) -> None:
        if self.current():
            self.canvas._emit_doc_changed(
                DocumentChangeEvent(change_type="layer_opacity", source="canvas")
            )


def _capture_opacity_restore_notice(canvas: Any, group: Any) -> _OpacityScope | None:
    if group is not None and any(
        _is_standard_opacity_command(getattr(entry, "command", None))
        and entry.command._checked_values is not None
        for entry in group.entries
    ):
        return _OpacityScope(canvas)
    return None


class _OpacityReceipt:
    """Keep only one before/after scalar and existing history custody."""

    def __init__(
        self, canvas: Any, command: SetLayerOpacityCommand, scope: _OpacityScope, layer: Any
    ):
        self.canvas, self.command, self.layer = canvas, command, layer
        self.scope = scope
        self.before, self.after = layer.opacity, command._new
        self.command_state = command._old, command._executed, command._checked_values
        self.history = _CommandHistoryReceipt(canvas, self.scope)

    def rollback(self) -> bool:
        if self.layer.opacity != self.after or not self.history.can_rollback(self.command):
            return False
        self.layer.opacity = self.before
        self.command._old, self.command._executed, self.command._checked_values = self.command_state
        self.history.restore()
        try:
            self.canvas._invalidate_layer_cache()
            self.canvas._recompose()
        except Exception:
            logger.exception("Opacity recovered but its redraw failed")
        return True


def _prepare_opacity(canvas: Any, command: SetLayerOpacityCommand) -> _OpacityReceipt | bool:
    layer = find_layer(canvas, command.layer_id)
    if layer is None or is_document_capture_blocked(canvas):
        return False
    scope = _OpacityScope(canvas, (layer,))
    if not scope.current():
        return False
    if layer.opacity == command._new:
        return True
    return _OpacityReceipt(canvas, command, scope, layer)


def _notify_opacity(receipt: _OpacityReceipt, capture: Any, owner: Any) -> None:
    canvas, scope, command = receipt.canvas, receipt.scope, receipt.command
    try:
        scope.emit_document_change()
    except Exception:
        logger.exception("Opacity document notification failed after commitment")
    finally:
        try:
            capture.release()
        except Exception:
            logger.exception("Opacity committed but its capture release failed")
        finally:
            if owner is not None:
                owner.acknowledge()
    try:
        if scope.current():
            command._submitting = False
            command._notify(canvas, receipt.layer)
        if scope.current():
            canvas._recompose()
    except Exception:
        logger.exception("Opacity notification failed after commitment")


def _finish_opacity_submit(
    owner: Any, capture: Any, scope: _OpacityScope, committed: bool, recovered: bool
) -> None:
    if owner is not None:
        owner.finish_submit(committed=committed, recovered=recovered)
    elif committed or recovered or not scope.current():
        capture.release()


def _submit_opacity_command(  # noqa: PLR0911 - preserve preparation/owner/commit refusal boundaries
    canvas: Any, command: SetLayerOpacityCommand
) -> bool:
    if not _standard_opacity_route(canvas, command):
        return cast(bool, canvas._simple_layer_command_submit().submit(command, "Layer Opacity"))
    owner = _opacity_command_owner(canvas, command)
    try:
        receipt = _prepare_opacity(canvas, command)
    except Exception:
        logger.exception("Opacity preparation failed")
        return False
    if isinstance(receipt, bool):
        return receipt
    scope = receipt.scope
    notifier = getattr(scope.pages, "_history_state_notifier", None)
    batch = notifier.batch() if notifier is not None else nullcontext()
    try:
        capture = canvas._begin_transient_document_edit()
    except Exception:
        logger.exception("Opacity capture preparation failed")
        return False
    if owner is not None:
        owner.track_commit(receipt, capture)
    if not scope.current():
        capture.release()
        if owner is not None:
            owner.finish_submit(committed=False, recovered=True)
        return False
    command._checked_values = (receipt.before, receipt.after)
    command._submitting = True
    committed = recovered = False
    try:
        with batch:
            try:
                if not command.apply(canvas):
                    recovered = True
                    return False
                if not scope.current() or receipt.layer.opacity != receipt.after:
                    return False
                canvas._record_history_command("Layer Opacity", command)
                if not receipt.history.recorded(command):
                    raise RuntimeError("Opacity history commitment was not confirmed")
                committed = True
            except Exception:
                recovered = receipt.rollback()
                logger.exception("Opacity failed before history commitment")
                return False
            _notify_opacity(receipt, capture, owner)
        return True
    finally:
        command._submitting = False
        _finish_opacity_submit(owner, capture, scope, committed, recovered)
