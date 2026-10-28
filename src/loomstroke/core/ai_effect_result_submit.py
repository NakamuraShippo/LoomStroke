"""Commit a prepared existing-layer RGB result before publishing its change."""

from __future__ import annotations

import logging
from contextlib import nullcontext
from typing import Any

from .ai_effect_result_command import _ApplyAIEffectResultCommand, _is_standard_result_command
from .ai_effect_result_payload import _prepare_result, _standard_result_canvas
from .canvas_layer_model import _Layer
from .commands_layer_lookup import find_layer
from .document_capture_guard import is_document_capture_blocked
from .events import DocumentChangeEvent, Events
from .layer_command_commit_context import _CommandHistoryReceipt, _LayerCommandContext

logger = logging.getLogger(__name__)


class _ResultScope(_LayerCommandContext):
    def emit_document_change(self) -> None:
        if self.current():
            self.canvas._emit_doc_changed(
                DocumentChangeEvent(change_type="ai_effect_result", source="canvas")
            )


def _notify_layer(canvas: Any, layer: _Layer) -> None:
    canvas._publish_layer_event(
        Events.LAYER_CHANGED, layer=layer, change_type="ai_effect_image_changed"
    )


class _ResultRestoreNotice(_ResultScope):
    def __init__(self, canvas: Any, identifiers: tuple[str, ...], inner: Any) -> None:
        super().__init__(canvas)
        self.identifiers, self.inner = identifiers, inner

    def emit_document_change(self) -> None:
        if self.inner is not None:
            self.inner.emit_document_change()
        else:
            super().emit_document_change()
        for identifier in self.identifiers:
            if not self.current():
                return
            layer = find_layer(self.canvas, identifier)
            if type(layer) is _Layer and layer.layer_type == "ai_effect":
                _notify_layer(self.canvas, layer)


def _capture_result_restore_notice(canvas: Any, group: Any, inner: Any) -> Any:
    identifiers = tuple(
        dict.fromkeys(
            entry.command.layer_id
            for entry in getattr(group, "entries", ())
            if _is_standard_result_command(getattr(entry, "command", None))
            and entry.command._payload is not None
        )
    )
    return _ResultRestoreNotice(canvas, identifiers, inner) if identifiers else inner


def _prepare_submission(canvas: Any, command: _ApplyAIEffectResultCommand) -> Any:
    if any(layer.native_cmyk is not None for layer in canvas._flatten_layers()):
        return None
    layer = find_layer(canvas, command.layer_id)
    if type(layer) is not _Layer or layer.layer_type != "ai_effect" or layer.ai_effect is None:
        return None
    scope = _ResultScope(canvas, (layer,))
    if not scope.current():
        return None
    payload, prepared = _prepare_result(canvas, layer, scope, command.png)
    history = _CommandHistoryReceipt(canvas, scope)
    capture = canvas._begin_transient_document_edit()
    return scope, payload, prepared, history, capture


def _rollback_submission(canvas: Any, command: Any, prepared: Any, history: Any) -> bool:
    if not history.can_rollback(command) or not prepared.rollback():
        return False
    history.restore()
    command._payload, command._executed = None, False
    try:
        canvas._invalidate_layer_cache()
        canvas._recompose()
    except Exception:
        logger.exception("AI result rollback presentation failed")
    return True


def _notify_result(scope: _ResultScope, capture: Any) -> None:
    try:
        scope.emit_document_change()
        capture.release()
        if scope.current():
            _notify_layer(scope.canvas, scope.layers[0])
        if scope.current():
            scope.canvas._recompose()
    except Exception:
        logger.exception("AI result notification failed after commitment")


def _submit_ai_effect_result(canvas: Any, command: _ApplyAIEffectResultCommand) -> bool:
    if (
        not _is_standard_result_command(command)
        or not _standard_result_canvas(canvas)
        or command._payload is not None
        or is_document_capture_blocked(canvas)
    ):
        return False
    try:
        attempt = _prepare_submission(canvas, command)
    except Exception:
        logger.exception("AI result preparation failed")
        return False
    if attempt is None:
        return False
    scope, payload, prepared, history, capture = attempt
    notifier = getattr(scope.pages, "_history_state_notifier", None)
    batch = notifier.batch() if notifier is not None else nullcontext()
    committed = recovered = False
    command._payload, command._prepared = payload, prepared
    try:
        with batch:
            try:
                if not command.apply(canvas):
                    raise RuntimeError("AI result application was not confirmed")
                if (
                    not scope.current()
                    or prepared.installed is None
                    or not prepared.installed.current()
                ):
                    raise RuntimeError("AI result target changed before commitment")
                canvas._record_history_command("AI Effect", command)
                if not history.recorded(command):
                    raise RuntimeError("AI result history commitment was not confirmed")
                committed = True
            except Exception:
                recovered = _rollback_submission(canvas, command, prepared, history)
                logger.exception("AI result failed before history commitment")
                return False
            _notify_result(scope, capture)
        return True
    finally:
        command._prepared = None
        if not committed and prepared.installed is None:
            command._payload, command._executed = None, False
        if committed or recovered or prepared.installed is None or not scope.current():
            capture.release()
