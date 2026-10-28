"""Commit one checked layer blend scalar before notifying document observers."""

from __future__ import annotations

import logging
from contextlib import nullcontext
from inspect import getattr_static
from typing import Any, cast

from .commands_layer_blend_mode import SetLayerBlendModeCommand, _is_standard_blend_command
from .commands_layer_lookup import find_layer
from .document_capture_guard import is_document_capture_blocked
from .events import DocumentChangeEvent
from .layer_command_commit_context import _CommandHistoryReceipt, _LayerCommandContext
from .native_cmyk.capabilities import NativeCmykCapabilities

logger = logging.getLogger(__name__)


def _standard_blend_route(canvas: Any, command: Any) -> bool:
    # Resolve these completed definitions only when dispatching, avoiding the
    # Canvas history/mixin import cycle. The markers are fixed at definition time.
    from .canvas_service_simple_layer_api import _DEFAULT_BLEND_LAYER_HOOKS  # noqa: PLC0415

    return _is_standard_blend_command(command) and all(
        getattr_static(canvas, name, None) is original
        for name, original in _DEFAULT_BLEND_LAYER_HOOKS.items()
    )


class _BlendScope(_LayerCommandContext):
    def emit_document_change(self) -> None:
        if self.current():
            self.canvas._emit_doc_changed(
                DocumentChangeEvent(change_type="layer_blend_mode", source="canvas")
            )


def _capture_blend_restore_notice(canvas: Any, group: Any) -> _BlendScope | None:
    if group is not None and any(
        _is_standard_blend_command(getattr(entry, "command", None))
        and entry.command._checked_values is not None
        for entry in group.entries
    ):
        return _BlendScope(canvas)
    return None


class _BlendReceipt:
    """Keep only this scalar and the shared history custody references."""

    def __init__(
        self,
        canvas: Any,
        command: SetLayerBlendModeCommand,
        scope: _BlendScope,
        layer: Any,
        after: Any,
    ):
        self.canvas, self.command, self.scope = canvas, command, scope
        self.layer, self.before, self.after = layer, layer.blend_mode, after
        self.command_state = command._old, command._executed, command._checked_values
        self.history = _CommandHistoryReceipt(canvas, scope)

    def rollback(self) -> bool:
        if self.layer.blend_mode is not self.after or not self.history.can_rollback(self.command):
            return False
        self.layer.blend_mode = self.before
        self.command._old, self.command._executed, self.command._checked_values = self.command_state
        self.history.restore()
        try:
            self.canvas._invalidate_layer_cache()
            self.canvas._recompose()
        except Exception:
            logger.exception("Layer blend recovery redraw failed")
        return True


def _prepare_blend(canvas: Any, command: SetLayerBlendModeCommand) -> _BlendReceipt | bool:
    layer = find_layer(canvas, command.layer_id)
    if layer is None or is_document_capture_blocked(canvas):
        return False
    scope = _BlendScope(canvas, (layer,))
    if not scope.current():
        return False
    after = canvas._normalize_blend_mode(command._new)
    if (
        any(item.native_cmyk is not None for item in canvas._flatten_layers())
        and after.value not in NativeCmykCapabilities.BLEND_MODES
    ):
        return False
    if not scope.current():
        return False
    before = layer.blend_mode
    if after is before:
        return True
    return _BlendReceipt(canvas, command, scope, layer, after)


def _notify_blend(receipt: _BlendReceipt, capture: Any) -> None:
    canvas, scope, command = receipt.canvas, receipt.scope, receipt.command
    try:
        scope.emit_document_change()
        capture.release()
        if scope.current():
            command._submitting = False
            command._notify(canvas, receipt.layer)
        if scope.current():
            canvas._recompose()
    except Exception:
        logger.exception("Layer blend notification failed after commitment")


def _submit_blend_command(  # noqa: PLR0911 - keep explicit preparation/owner/commit refusal boundaries
    canvas: Any, command: SetLayerBlendModeCommand
) -> bool:
    if not _standard_blend_route(canvas, command):
        return cast(bool, canvas._simple_layer_command_submit().submit(command, "Blend Mode"))
    try:
        receipt = _prepare_blend(canvas, command)
    except Exception:
        logger.exception("Layer blend preparation failed")
        return False
    if isinstance(receipt, bool):
        return receipt
    scope = receipt.scope
    notifier = getattr(scope.pages, "_history_state_notifier", None)
    batch = notifier.batch() if notifier is not None else nullcontext()
    try:
        capture = canvas._begin_transient_document_edit()
    except Exception:
        logger.exception("Layer blend capture preparation failed")
        return False
    if not scope.current():
        capture.release()
        return False
    command._checked_values = (receipt.before.value, receipt.after.value)
    command._submitting = True
    committed = recovered = False
    try:
        with batch:
            try:
                if not command.apply(canvas):
                    recovered = True
                    return False
                if not scope.current() or receipt.layer.blend_mode is not receipt.after:
                    return False
                canvas._record_history_command("Blend Mode", command)
                if not receipt.history.recorded(command):
                    raise RuntimeError("Blend history commitment was not confirmed")
                committed = True
            except Exception:
                recovered = receipt.rollback()
                logger.exception("Layer blend failed before history commitment")
                return False
            _notify_blend(receipt, capture)
        return True
    finally:
        command._submitting = False
        if committed or recovered or not scope.current():
            capture.release()
