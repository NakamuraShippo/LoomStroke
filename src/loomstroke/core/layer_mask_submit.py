"""Commit one standard mask Command before exposing its document change."""

from __future__ import annotations

import logging
from contextlib import nullcontext
from inspect import getattr_static
from typing import Any, cast

from PySide6.QtGui import QImage

from .canvas_history_clear_continuation import _stored_state
from .canvas_raster_preimage_capture_service import CanvasRasterPreimageCaptureService
from .commands_layer_lookup import find_layer
from .commands_layer_tree import (
    AddLayerMaskCommand,
    RemoveLayerMaskCommand,
    SetLayerMaskCommand,
    _is_standard_mask_command,
)
from .document_capture_guard import is_document_capture_blocked
from .events import DocumentChangeEvent
from .layer_command_commit_context import _CommandHistoryReceipt, _LayerCommandContext, _same_items
from .layer_mask_command_state import _prepare_mask_state

logger = logging.getLogger(__name__)
_KINDS: dict[type[Any], str] = {
    AddLayerMaskCommand: "add",
    RemoveLayerMaskCommand: "remove",
    SetLayerMaskCommand: "set",
}
_CAPTURE_FIELDS = frozenset(
    (
        "_mode",
        "_layer_id",
        "_img_key",
        "_offset",
        "_mask_edit",
        "_dirty",
        "_source",
        "_dense_before",
        "_fragments",
        "_captured_logical_bytes",
        "_dense_snapshot_count",
    )
)


def _standard_mask_route(canvas: Any, command: Any) -> bool:
    from .canvas_service_simple_layer_api import _DEFAULT_MASK_LAYER_HOOKS  # noqa: PLC0415

    return _is_standard_mask_command(command) and all(
        getattr_static(canvas, name, None) is original
        for name, original in _DEFAULT_MASK_LAYER_HOOKS.items()
    )


class _MaskScope(_LayerCommandContext):
    def emit_document_change(self) -> None:
        if self.current():
            self.canvas._emit_doc_changed(
                DocumentChangeEvent(change_type="layer_mask", source="canvas")
            )


def _capture_mask_restore_notice(canvas: Any, group: Any) -> _MaskScope | None:
    if group is not None and any(
        _is_standard_mask_command(getattr(entry, "command", None))
        and entry.command._mask_state is not None
        for entry in group.entries
    ):
        return _MaskScope(canvas)
    return None


class _MaskReceipt:
    """Mask references and the existing history receipt, never a canvas snapshot."""

    def __init__(self, canvas: Any, command: Any, layer: Any, scope: _MaskScope, state: Any):
        self.canvas, self.command, self.layer, self.scope = canvas, command, layer, scope
        self.state = state
        self.before_mask, self.before_enabled = layer.layer_mask, layer.mask_enabled
        self.command_state = command._mask_state, command._executed
        self.history = _CommandHistoryReceipt(canvas, scope)

    def cancel_unmodified(self) -> bool:
        if (
            self.state.applied_once
            or self.layer.layer_mask is not self.before_mask
            or self.layer.mask_enabled != self.before_enabled
            or not self.history.can_rollback(self.command)
        ):
            return False
        self.command._mask_state, self.command._executed = self.command_state
        self.history.restore()
        return True

    def rollback(self) -> bool:
        if not self.history.can_rollback(self.command) or not self.state.after.matches(self.layer):
            return False
        self.layer.layer_mask, self.layer.mask_enabled = self.before_mask, self.before_enabled
        self.command._mask_state, self.command._executed = self.command_state
        self.history.restore()
        try:
            self.canvas._invalidate_layer_cache()
            self.canvas._recompose()
        except Exception:
            logger.exception("Mask recovered but its redraw failed")
        return True


class _PendingPaintReferences:
    """Return an unconsumed capture after a failed flush, using references only."""

    def __init__(
        self, canvas: Any, scope: _MaskScope, capture: CanvasRasterPreimageCaptureService
    ) -> None:
        self.canvas, self.scope, self.capture = canvas, scope, capture
        self.fields = dict(_stored_state(capture))
        self.source = cast(QImage, self.fields.get("_source"))
        self.layer_id = cast(str, self.fields.get("_layer_id"))
        self.source_layer = find_layer(canvas, self.layer_id)
        owner = getattr(
            self.source_layer, "layer_mask" if self.fields.get("_mask_edit") else "image", None
        )
        if (
            set(self.fields) != _CAPTURE_FIELDS
            or not isinstance(self.source, QImage)
            or owner is not self.source
        ):
            raise ValueError("Pending paint ownership is not recognized")
        self.source_key = self.source.cacheKey()
        self.undo, self.redo = tuple(canvas._undo_stack), tuple(canvas._redo_stack)
        self.group = canvas._active_history_group
        self.entries = tuple(self.group.entries) if self.group is not None else ()

    def restore_if_unconsumed(self) -> None:
        current, group = _stored_state(self.capture), self.canvas._active_history_group
        layer = find_layer(self.canvas, self.layer_id)
        source = getattr(layer, "layer_mask" if self.fields.get("_mask_edit") else "image", None)
        same_group = group is self.group or (
            self.group is None and group is not None and not group.entries
        )
        if (
            not self.scope.current()
            or _stored_state(self.canvas).get("_txn_preimage_capture") is not self.capture
            or layer is not self.source_layer
            or source is not self.source
            or self.source.cacheKey() != self.source_key
            or self.capture.is_active
            or set(current) != set(self.fields)
            or any(
                current.get(name) is not None
                for name in ("_mode", "_layer_id", "_source", "_dirty", "_dense_before")
            )
            or current.get("_fragments")
            or current.get("_mask_edit") is not False
            or current.get("_captured_logical_bytes") != 0
            or current.get("_dense_snapshot_count") != 0
            or current.get("_img_key") != (0, 0)
            or current.get("_offset") != (0, 0)
            or not _same_items(self.canvas._undo_stack, self.undo)
            or not _same_items(self.canvas._redo_stack, self.redo)
            or not same_group
            or not _same_items(group.entries if group is not None else (), self.entries)
        ):
            return
        current.update(self.fields)


def _close_pending_mask_paint(canvas: Any, scope: _MaskScope) -> bool:
    state = _stored_state(canvas)
    capture = state.get("_txn_preimage_capture")
    if capture is not None and type(capture) is not CanvasRasterPreimageCaptureService:
        return False
    if state.get("_mask_paint_dirty") is True:
        timer = state.get("_mask_commit_timer")
        if timer is None:
            return False
        timer.stop()
        canvas._commit_mask_paint()
        if not scope.current() or _stored_state(canvas).get("_txn_preimage_capture") is not capture:
            return False
    if capture is None:
        return scope.current()
    if capture.is_active:
        references = _PendingPaintReferences(canvas, scope, capture)
        try:
            canvas._history().flush_deferred_paint()
        except Exception:
            references.restore_if_unconsumed()
            raise
        if not scope.current() or canvas._txn_preimage_capture is not capture:
            return False
    return not capture.is_active and scope.current()


def _prepare_mask(canvas: Any, command: Any) -> _MaskReceipt | None:
    if is_document_capture_blocked(canvas):
        return None
    layer = find_layer(canvas, command._layer_id)
    if layer is None:
        return None
    scope = _MaskScope(canvas, (layer,))
    if (
        not scope.current()
        or not _close_pending_mask_paint(canvas, scope)
        or is_document_capture_blocked(canvas)
    ):
        return None
    state = _prepare_mask_state(canvas, command, layer, _KINDS[type(command)])
    if state is None or not scope.current():
        return None
    return _MaskReceipt(canvas, command, layer, scope, state)


def _notify_mask(receipt: _MaskReceipt, capture: Any) -> None:
    try:
        receipt.scope.emit_document_change()
    except Exception:
        logger.exception("Mask document notification failed after commitment")
    finally:
        try:
            capture.release()
        except Exception:
            # The outer finally retries this idempotent, own-token release.
            logger.exception("Mask committed but its capture release failed")
    try:
        if receipt.scope.current():
            receipt.state.notify(receipt.canvas, receipt.layer, use_after=True)
        if receipt.scope.current():
            receipt.canvas._recompose()
    except Exception:
        logger.exception("Mask layer notification failed after commitment")


def _submit_mask_command(  # noqa: PLR0911 - keep preparation/commit/foreign refusal boundaries explicit
    canvas: Any, command: Any
) -> bool | None:
    if not _standard_mask_route(canvas, command):
        return None
    try:
        receipt = _prepare_mask(canvas, command)
    except Exception:
        logger.exception("Mask preparation failed")
        return False
    if receipt is None:
        return False
    scope = receipt.scope
    notifier = getattr(scope.pages, "_history_state_notifier", None)
    batch = notifier.batch() if notifier is not None else nullcontext()
    try:
        capture = canvas._begin_transient_document_edit()
    except Exception:
        logger.exception("Mask capture preparation failed")
        return False
    if not scope.current():
        capture.release()
        return False
    command._mask_state, command._mask_submitting = receipt.state, True
    committed = recovered = False
    try:
        with batch:
            try:
                if not command.apply(canvas):
                    recovered = receipt.cancel_unmodified()
                    return False
                if not scope.current() or not receipt.state.after.matches(receipt.layer):
                    return False
                canvas._record_history_command(
                    type(command).__name__.replace("Command", ""), command
                )
                if not receipt.history.recorded(command):
                    raise RuntimeError("Mask history commitment was not confirmed")
                committed = True
            except Exception:
                recovered = receipt.rollback()
                logger.exception("Mask failed before history commitment")
                return False
            _notify_mask(receipt, capture)
        return True
    finally:
        command._mask_submitting = False
        if committed or recovered or not scope.current():
            capture.release()
