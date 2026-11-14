"""Commit generated sibling layers before notifying canvas observers."""

from __future__ import annotations

import logging
from contextlib import nullcontext
from typing import Any

from loomstroke.host_api import LayerRef

from .ai_effect_output_layer_command import (
    _CreateAIEffectOutputLayersCommand,
    _standard_output_canvas,
)
from .canvas_layer_model import _Layer
from .document_capture_guard import is_document_capture_blocked
from .events import DocumentChangeEvent, Events
from .imported_3d_layer import _supports_prepared_3d_layer_history
from .layer_command_commit_context import _CommandHistoryReceipt, _LayerCommandContext
from .layer_tree_service import LayerCreationService

logger = logging.getLogger(__name__)


def _notify_output_changes(
    scope: _LayerCommandContext,
    command: _CreateAIEffectOutputLayersCommand,
    *,
    added: bool,
    previous_active: LayerRef | None = None,
) -> None:
    canvas = scope.canvas
    if command._image_payload is not None and scope.current() and command._executed == added:
        source = canvas._find_layer_by_id_tree(command.layer_id)
        if type(source) is _Layer and source.layer_type == "ai_effect":
            canvas._publish_layer_event(
                Events.LAYER_CHANGED, layer=source, change_type="ai_effect_image_changed"
            )
    for layer in command._new_layers():
        if not scope.current() or command._executed != added:
            return
        live = canvas._find_layer_by_id_tree(layer.id)
        model = layer.layer_type == "3d"
        if added and live is layer:
            if model:
                canvas._publish_layer_event(
                    Events.LAYER_ADDED,
                    layer=layer,
                    change_type="added",
                    new_value=LayerCreationService.layer_ref(layer),
                )
            else:
                canvas._publish_layer_event(
                    Events.LAYER_ADDED, layer=layer, change_type="ai_effect_output_created"
                )
        elif not added and live is None:
            canvas._publish_layer_event(
                Events.LAYER_REMOVED,
                layer=layer,
                change_type="removed",
                old_value=(
                    LayerCreationService.layer_ref(layer)
                    if model
                    else LayerRef(id=layer.id, name=layer.name)
                ),
            )
    if previous_active is not None and scope.current() and command._executed == added:
        current = canvas.current_layer()
        if previous_active.id != current.id:
            canvas._emit_layer_selected(previous_active, current)


class _OutputRestoreNotice(_LayerCommandContext):
    def __init__(self, canvas: Any, commands: tuple[Any, ...], inner: Any) -> None:
        super().__init__(canvas)
        self.commands = tuple((command, not command._executed) for command in commands)
        self.inner = inner
        self.previous_active = (
            canvas.current_layer() if any(command._models for command in commands) else None
        )

    def emit_document_change(self) -> None:
        if self.inner is not None:
            self.inner.emit_document_change()
        elif self.current():
            self.canvas._emit_doc_changed(DocumentChangeEvent(change_type="ai_effect_result"))
        for command, added in self.commands:
            _notify_output_changes(self, command, added=added, previous_active=self.previous_active)


def _capture_output_restore_notice(canvas: Any, group: Any, inner: Any) -> Any:
    commands = tuple(
        entry.command
        for entry in getattr(group, "entries", ())
        if type(getattr(entry, "command", None)) is _CreateAIEffectOutputLayersCommand
    )
    return _OutputRestoreNotice(canvas, commands, inner) if commands else inner


def _publish_committed_outputs(
    scope: _LayerCommandContext,
    command: _CreateAIEffectOutputLayersCommand,
    capture: Any,
    previous_active: LayerRef | None,
) -> None:
    canvas = scope.canvas
    try:
        canvas._emit_doc_changed(DocumentChangeEvent(change_type="ai_effect_result"))
        capture.release()
        _notify_output_changes(scope, command, added=True, previous_active=previous_active)
        if scope.current():
            canvas._recompose()
    except Exception:
        logger.exception("AI output notification failed after commitment")


def _prepare_output_submission(canvas: Any, command: _CreateAIEffectOutputLayersCommand) -> Any:
    parent = canvas._find_layer_by_id_tree(command.layer_id)
    if type(parent) is not _Layer or parent.layer_type != "ai_effect" or parent.ai_effect is None:
        return None
    state, scope = parent.ai_effect, _LayerCommandContext(canvas, (parent,))
    last = state.last_result
    generation_index = len(last.output_layer_ids) if last is not None else 0
    try:
        if scope.current() and command._prepare(canvas, parent, scope, generation_index):
            return parent, state, last, scope
    except Exception:
        logger.exception("AI output preparation failed")
    return None


def _submit_ai_effect_output_layers(
    canvas: Any, command: _CreateAIEffectOutputLayersCommand
) -> bool:
    if (
        type(command) is not _CreateAIEffectOutputLayersCommand
        or command._new_layers()
        or not _standard_output_canvas(canvas)
        or is_document_capture_blocked(canvas)
    ):
        return False
    if command._model_outputs and (
        not _supports_prepared_3d_layer_history(canvas)
        or canvas._in_transaction
        or getattr(canvas, "_panel_edit_events", None) is not None
    ):
        return False
    attempt = _prepare_output_submission(canvas, command)
    if attempt is None:
        return False
    parent, state, last, scope = attempt
    previous_active = canvas.current_layer() if command._models else None
    receipt = _CommandHistoryReceipt(canvas, scope)
    notifier = getattr(scope.pages, "_history_state_notifier", None)
    batch = notifier.batch() if notifier is not None else nullcontext()
    capture = canvas._begin_transient_document_edit()
    committed = recovered = False
    try:
        with batch:
            try:
                if not scope.current() or not command.apply(canvas):
                    raise RuntimeError("AI output application was not confirmed")
                if not scope.current() or not command._initial_image_is_current():
                    raise RuntimeError("AI output target changed before commitment")
                canvas._record_history_command("AI Effect", command)
                if (
                    not receipt.recorded(command)
                    or parent.ai_effect is not state
                    or state.last_result is not last
                    or not command._initial_image_is_current()
                    or any(
                        canvas._find_layer_by_id_tree(layer.id) is not layer
                        for layer in command._new_layers()
                    )
                ):
                    raise RuntimeError("AI output history commitment was not confirmed")
                if last is not None:
                    command._remember_last_result(last)
                committed = True
            except Exception:
                if receipt.can_rollback(command) and command._rollback_initial_application(canvas):
                    receipt.restore()
                    recovered = True
                logger.exception("AI output layers failed before history commitment")
                return False

            _publish_committed_outputs(scope, command, capture, previous_active)
        return True
    finally:
        unapplied = not command._executed and not command._initial_image_is_installed()
        command._initial_image = None
        if committed or recovered or unapplied or not scope.current():
            capture.release()
