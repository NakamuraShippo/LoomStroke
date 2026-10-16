"""Command dispatch and history adapters for ``SimpleCanvasService``.

The adapters keep the public facade stable while giving future core splits a
single place to replace command routing or history storage.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QRect
from PySide6.QtGui import QPainter

from loomstroke.core.channels.channel_command_policy import ChannelCommandPolicy
from loomstroke.core.native_cmyk.command_dispatcher import NativeCmykCommandDispatcher

logger = logging.getLogger(__name__)


class CanvasCommandDispatcher:
    """Thin command submission boundary around the legacy dispatch table."""

    def __init__(self, canvas: Any) -> None:
        self._canvas = canvas

    def submit(self, cmd: Any, *, merge_key: str | None = None) -> bool:
        native_result = NativeCmykCommandDispatcher.submit(self._canvas, cmd)
        if native_result is not None:
            return native_result
        if not ChannelCommandPolicy.allows(self._canvas, cmd):
            logger.warning(
                "Return to RGB before applying %s to selection pixels", type(cmd).__name__
            )
            return False
        return bool(self._canvas._submit_command_direct(cmd, merge_key=merge_key))


class HistoryAdapter:
    """Undo/redo boundary around the current history implementation."""

    def __init__(self, canvas: Any) -> None:
        self._canvas = canvas

    def can_undo(self) -> bool:
        return bool(self._canvas._undo_stack)

    def can_redo(self) -> bool:
        return bool(self._canvas._redo_stack)

    def undo(self) -> bool:
        filter_pen = getattr(
            self._canvas, "_filter_pen_service", getattr(self._canvas, "_effect_pen_service", None)
        )
        if filter_pen is not None:
            filter_pen.finalize()
        return bool(self._canvas._history_undo_direct())

    def redo(self) -> bool:
        filter_pen = getattr(
            self._canvas, "_filter_pen_service", getattr(self._canvas, "_effect_pen_service", None)
        )
        if filter_pen is not None:
            filter_pen.finalize()
        return bool(self._canvas._history_redo_direct())

    def undo_count(self) -> int:
        return len(self._canvas._undo_stack)

    def redo_count(self) -> int:
        return len(self._canvas._redo_stack)

    def clear(self) -> None:
        filter_pen = getattr(
            self._canvas, "_filter_pen_service", getattr(self._canvas, "_effect_pen_service", None)
        )
        if filter_pen is not None:
            filter_pen.finalize()
        self._canvas._history_clear_direct()


class CanvasHistoryBookkeepingService:
    """Own history stack bookkeeping and changed-event emission."""

    def __init__(
        self,
        canvas: Any,
        *,
        history_group_type: Callable[..., Any],
        history_command_type: type[Any],
        history_changed_event_type: Callable[..., Any],
        events: Any,
    ) -> None:
        self._canvas = canvas
        self._history_group_type = history_group_type
        self._history_command_type = history_command_type
        self._history_changed_event_type = history_changed_event_type
        self._events = events

    def snapshot(self) -> tuple[bool, bool, int, int]:
        undo_count = len(self._canvas._undo_stack)
        redo_count = len(self._canvas._redo_stack)
        length = undo_count + redo_count
        cursor = undo_count
        return bool(undo_count), bool(redo_count), length, cursor

    def emit_changed(self) -> None:
        # CT-05 H5b: with the unified-history callback connected
        # (production wiring through PageServiceImpl), canvas history
        # changes are routed to the document-history notifier instead of
        # publishing a raw content-only HISTORY_CHANGED — the unified
        # service owns the public event pair and a raw payload must not
        # override it.  Standalone/legacy canvases (no callback) keep the
        # direct EventBus emission unchanged.
        callback = getattr(self._canvas, "_history_changed_callback", None)
        if callable(callback):
            callback()
            return
        if not self._canvas._event_bus:
            return
        can_undo, can_redo, length, cursor = self.snapshot()
        payload = self._history_changed_event_type(
            can_undo=can_undo,
            can_redo=can_redo,
            length=length,
            cursor=cursor,
        )
        publish = getattr(self._canvas._event_bus, "publish", None)
        if callable(publish):
            publish(self._events.HISTORY_CHANGED, payload)
            return
        emit = getattr(self._canvas._event_bus, "emit", None)
        if callable(emit):
            emit(self._events.HISTORY_CHANGED, payload)

    def clear_direct(self) -> None:
        self._canvas._undo_stack.clear()
        self._canvas._redo_stack.clear()
        self._canvas._active_history_group = None
        self._canvas._emit_history_changed()

    def record_patch(self, label: str, patch: Any) -> None:
        if patch.rect.isEmpty():
            return

        if self._canvas._in_transaction:
            if self._canvas._active_history_group is None:
                title = self._canvas._transaction_label or label
                self._canvas._active_history_group = self._history_group_type(label=title)
            self._canvas._active_history_group.entries.append(patch)
        else:
            group = self._history_group_type(label=label, entries=[patch])
            self._canvas._push_history_group(group)

    def record_command(self, label: str, command: Any) -> None:
        entry = self._history_command_type(command=command)
        if self._canvas._in_transaction:
            if self._canvas._active_history_group is None:
                title = self._canvas._transaction_label or label
                self._canvas._active_history_group = self._history_group_type(label=title)
            self._canvas._active_history_group.entries.append(entry)
        else:
            group = self._history_group_type(label=label, entries=[entry])
            self._canvas._push_history_group(group)

    def push_group(self, group: Any) -> None:
        if not group.entries:
            return

        # CT-05 H5a: assign the document-local sequence exactly when a
        # non-empty group actually enters the undo stack.  Every record
        # path (patch, command, selection, transaction commit, pending
        # close, vector, resize/merge/flatten) funnels through here, so
        # no per-tool wiring exists.  Groups that already carry a
        # sequence (bundle re-attach flows) are never re-claimed, and a
        # canvas without an injected source (standalone/legacy use)
        # keeps pushing unsequenced groups unchanged.
        if getattr(group, "sequence", 0) <= 0:
            source = getattr(self._canvas, "_history_sequence_source", None)
            if callable(source):
                try:
                    group.sequence = int(source())
                except Exception:
                    logger.exception(
                        "History sequence source failed; group %r stays unsequenced",
                        group.label,
                    )

        self._canvas._undo_stack.append(group)
        self._canvas._redo_stack.clear()
        # CT-05 H6 (single document timeline): a brand-new content
        # operation invalidates every redo branch in the document, not
        # just this canvas'.  The callback (wired by PageServiceImpl)
        # drops the book redo and all inactive pages' redo branches; it
        # never touches this canvas' stacks, so no recursion.
        new_operation = getattr(self._canvas, "_history_new_operation_callback", None)
        if callable(new_operation):
            new_operation()
        self._canvas._emit_history_changed()


class CanvasHistoryTransactionService:
    """Own transaction lifecycle and deferred paint history flushing."""

    def __init__(
        self,
        canvas: Any,
        *,
        history_patch_type: Any,
        bookkeeping: CanvasHistoryBookkeepingService,
    ) -> None:
        self._canvas = canvas
        self._history_patch_type = history_patch_type
        self._bookkeeping = bookkeeping

    def begin_transaction(self, label: str) -> None:
        if self._canvas._in_transaction:
            logger.warning(
                "begin_transaction(%r) called while already in transaction %r; "
                "flushing previous transaction first",
                label,
                self._canvas._transaction_label,
            )
            self.flush_deferred_paint()
            if self._canvas._active_history_group and self._canvas._active_history_group.entries:
                self._bookkeeping.push_group(self._canvas._active_history_group)
            self._canvas._active_history_group = None
            self._canvas._in_transaction = False
            self._canvas._stroke_painting_active = False
        self._canvas._in_transaction = True
        self._canvas._transaction_label = label
        self._canvas._active_history_group = None
        logger.debug("Transaction started: %s", label)

    def end_transaction(self) -> None:
        if not self._canvas._in_transaction:
            logger.debug("end_transaction called outside transaction; ignoring")
            return
        capture = self._canvas._txn_preimage_capture
        deferred_layer_id = capture.pending_layer_id
        deferred_dirty = capture.pending_dirty_rect()
        if deferred_dirty is None:
            from .raster_stroke_command import placed_stroke_history_dirty  # noqa: PLC0415

            placed = placed_stroke_history_dirty(self._canvas)
            if placed is not None:
                deferred_layer_id, deferred_dirty = placed
        self.flush_deferred_paint()
        from .comic_panels.stroke_upload import panel_paint_history_only  # noqa: PLC0415

        panel_paint = deferred_dirty is not None and panel_paint_history_only(
            self._canvas, deferred_layer_id, self._history_patch_type
        )
        if self._canvas._active_history_group and self._canvas._active_history_group.entries:
            self._bookkeeping.push_group(self._canvas._active_history_group)
        self._canvas._active_history_group = None
        self._canvas._in_transaction = False
        self._canvas._stroke_painting_active = False
        if panel_paint:
            self._canvas._recompose_region(deferred_dirty)
        else:
            self._canvas._recompose()
        if deferred_layer_id and deferred_dirty:
            layer = self._canvas._get_layer_by_id(deferred_layer_id)
            if layer is not None:
                self._canvas._emit_layer_content_changed(
                    layer,
                    deferred_dirty,
                    {"command": "paint"},
                )
        logger.debug("Transaction ended: %s", self._canvas._transaction_label)
        self._canvas._transaction_label = ""

    def flush_deferred_paint(self) -> None:
        result = self._canvas._txn_preimage_capture.take()
        if result is None:
            return

        # The layer lookup only checks that the layer still exists and can
        # record history; the pixels below come from the capture itself.
        layer = self._canvas._get_layer_by_id(result.layer_id)
        if layer is None or layer.image is None:
            return

        offset_x, offset_y = result.offset
        local_dirty = QRect(result.dirty)
        local_dirty.translate(-offset_x, -offset_y)

        img_rect = QRect(0, 0, result.img_key[0], result.img_key[1])
        local_dirty = local_dirty.intersected(img_rect)
        if local_dirty.isEmpty():
            return

        before_patch = result.materialize_before(local_dirty)

        # The capture's source/offset are authoritative for the after crop
        # too: segments mutate the captured QImage object in place, so
        # ``result.source`` holds this capture's post-mutation pixels even
        # when the layer has already moved on to the next source or offset
        # (mask swap, image replacement, offset change) by flush time.
        # Cropping the same ``local_dirty`` also guarantees the before and
        # after payloads share width/height/format, which
        # ``_HistoryPatch.from_images`` requires.
        after_patch = result.source.copy(local_dirty)
        meta: dict[str, Any] = {"command": "paint"}
        if result.mask_edit:
            meta["mask_edit"] = True
        self._canvas._record_history_patch(
            "Paint Stroke",
            self._history_patch_type.from_images(
                result.layer_id,
                QRect(result.dirty),
                before_patch,
                after_patch,
                meta,
            ),
        )


class CanvasHistoryFloodFillRestoreHelper:
    """Preserve alpha-lock side effects when replaying flood/pattern fills."""

    def __init__(
        self,
        canvas: Any,
        *,
        flood_fill_alpha_command_types: tuple[Any, ...],
        flood_fill_refresh_command_types: tuple[Any, ...],
        pattern_fill_command_type: Any,
    ) -> None:
        self._canvas = canvas
        self._flood_fill_alpha_command_types = flood_fill_alpha_command_types
        self._flood_fill_refresh_command_types = flood_fill_refresh_command_types
        self._pattern_fill_command_type = pattern_fill_command_type

    def save_alpha_for_redo(self, command: Any) -> tuple[Any | None, Any | None]:
        if not isinstance(command, self._flood_fill_alpha_command_types):
            return None, None
        layer_id = getattr(command, "target_layer_id", None) or getattr(
            command,
            "reference_layer_id",
            None,
        )
        layer = (
            self._canvas._get_layer_by_id(layer_id)
            if layer_id
            else self._canvas._get_active_layer()
        )
        if layer is None or layer.image is None:
            return None, None
        bounds = QRect(0, 0, layer.image.width(), layer.image.height())
        saved_alpha = self._canvas._save_alpha(layer, bounds)
        if saved_alpha is None:
            return None, None
        return saved_alpha, layer

    def restore_saved_alpha(self, saved_alpha: Any | None, alpha_layer: Any | None) -> None:
        if saved_alpha is None or alpha_layer is None or alpha_layer.image is None:
            return
        restore_bounds = QRect(0, 0, alpha_layer.image.width(), alpha_layer.image.height())
        self._canvas._restore_alpha(alpha_layer, restore_bounds, saved_alpha)

    def refresh_history_restore(self, command: Any) -> None:
        if not isinstance(command, self._flood_fill_refresh_command_types):
            return
        dirty = command.dirty_region
        target_layer_id = getattr(command, "target_layer_id", None)
        if dirty is None or not target_layer_id:
            return
        layer = self._canvas._get_layer_by_id(target_layer_id)
        if layer is None:
            return
        layer.texture_version += 1
        rect = QRect(dirty.x, dirty.y, dirty.width, dirty.height)
        self._canvas._emit_layer_content_changed(
            layer,
            rect,
            {
                "command": (
                    "pattern_fill"
                    if isinstance(command, self._pattern_fill_command_type)
                    else "bucket_fill"
                )
            },
        )


class CanvasHistoryRestoreService:
    """Own history entry dispatch and patch/command restoration."""

    def __init__(
        self,
        canvas: Any,
        *,
        history_command_type: type[Any],
        history_patch_type: Any,
        resize_entry_type: Any,
        merge_entry_type: Any,
        merge_visible_entry_type: Any,
        flatten_all_entry_type: Any,
        layer_visibility_command_type: Any,
        flood_fill_alpha_command_types: tuple[Any, ...],
        flood_fill_refresh_command_types: tuple[Any, ...],
        pattern_fill_command_type: Any,
        events: Any,
    ) -> None:
        self._canvas = canvas
        self._history_command_type = history_command_type
        self._history_patch_type = history_patch_type
        self._resize_entry_type = resize_entry_type
        self._merge_entry_type = merge_entry_type
        self._merge_visible_entry_type = merge_visible_entry_type
        self._flatten_all_entry_type = flatten_all_entry_type
        self._layer_visibility_command_type = layer_visibility_command_type
        self._events = events
        self._flood_fill_restore = CanvasHistoryFloodFillRestoreHelper(
            canvas,
            flood_fill_alpha_command_types=flood_fill_alpha_command_types,
            flood_fill_refresh_command_types=flood_fill_refresh_command_types,
            pattern_fill_command_type=pattern_fill_command_type,
        )

    def restore_entry(self, entry: Any, *, use_after: bool) -> None:
        if isinstance(entry, self._history_patch_type):
            self.restore_patch(entry, use_after=use_after)
            return
        if isinstance(entry, self._resize_entry_type):
            snapshot = entry.after if use_after else entry.before
            self._canvas._restore_resize_snapshot(snapshot)
            return
        if isinstance(entry, self._merge_entry_type):
            self._canvas._restore_merge(entry, use_after=use_after)
            return
        if isinstance(entry, self._merge_visible_entry_type):
            self._canvas._restore_merge_visible(entry, use_after=use_after)
            return
        if isinstance(entry, self._flatten_all_entry_type):
            if use_after:
                self._canvas._do_flatten_all_forward(entry)
            else:
                self._canvas._do_flatten_all_undo(entry)
            return
        if isinstance(entry, self._history_command_type):
            self.restore_command(entry.command, use_after=use_after)

    def restore_command(self, command: Any, *, use_after: bool) -> None:
        saved_alpha: Any | None = None
        alpha_layer: Any | None = None
        if use_after:
            saved_alpha, alpha_layer = self._flood_fill_restore.save_alpha_for_redo(command)

        success = self._run_history_command(command, use_after=use_after)
        if not success:
            return
        self._flood_fill_restore.restore_saved_alpha(saved_alpha, alpha_layer)
        self._flood_fill_restore.refresh_history_restore(command)
        self._bump_vector_command_texture_versions(command)
        self._publish_visibility_restore(command)

    def _run_history_command(self, command: Any, *, use_after: bool) -> bool:
        try:
            if use_after:
                return bool(command.apply(self._canvas))
            return bool(command.revert(self._canvas))
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("History command failed: %s", exc)
            return False

    def _bump_vector_command_texture_versions(self, command: Any) -> None:
        if self._canvas._is_vector_command(command):
            target_layer_id = getattr(command, "layer_id", None) or getattr(
                command,
                "_layer_id",
                None,
            )
            if target_layer_id:
                vector_layer = self._canvas._get_layer_by_id(target_layer_id)
                if vector_layer is not None:
                    vector_layer.texture_version += 1
            else:
                for layer in self._canvas._layers:
                    if layer.layer_type == "vector":
                        layer.texture_version += 1

    def _publish_visibility_restore(self, command: Any) -> None:
        if isinstance(command, self._layer_visibility_command_type):
            visibility_layer = self._canvas._get_layer_by_id(command.layer_id)
            if visibility_layer is not None:
                self._canvas._publish_layer_event(
                    self._events.LAYER_CHANGED,
                    layer=visibility_layer,
                    change_type="visibility",
                )

    def save_flood_fill_alpha_for_history_redo(self, command: Any) -> tuple[Any | None, Any | None]:
        return self._flood_fill_restore.save_alpha_for_redo(command)

    def refresh_flood_fill_history_restore(self, command: Any) -> None:
        self._flood_fill_restore.refresh_history_restore(command)

    def restore_patch(self, patch: Any, *, use_after: bool) -> None:
        if patch.layer_id == self._canvas._selection_id:
            replacement = patch.after if use_after else patch.before
            bounds = self._canvas._selection_bounds_from_mask(replacement)
            self._canvas.set_selection_mask(replacement, bounds)
            return
        layer = self._canvas._get_layer_by_id(patch.layer_id)
        if layer is None or layer.image is None:
            return

        is_mask_patch = bool(patch.metadata.get("mask_edit"))
        if use_after and not is_mask_patch:
            from .comic_panels.raster_history import prepare_edge_patch_restore  # noqa: PLC0415

            prepare_edge_patch_restore(self._canvas, layer, patch.rect)
        target = layer.layer_mask if is_mask_patch and layer.layer_mask is not None else layer.image

        replacement = patch.after if use_after else patch.before
        offset_x, offset_y = self._canvas._layer_offset(layer)
        local_rect = QRect(patch.rect)
        local_rect.translate(-offset_x, -offset_y)
        painter = QPainter(target)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.drawImage(local_rect.topLeft(), replacement)
        painter.end()
        layer.texture_version += 1
        self._canvas._emit_layer_content_changed(layer, patch.rect, patch.metadata)


class CanvasHistoryService:
    """Facade for history helpers retained behind the canvas facade."""

    def __init__(
        self,
        canvas: Any,
        *,
        history_group_type: Callable[..., Any],
        history_command_type: type[Any],
        history_patch_type: Any,
        resize_entry_type: Any,
        merge_entry_type: Any,
        merge_visible_entry_type: Any,
        flatten_all_entry_type: Any,
        layer_visibility_command_type: Any,
        flood_fill_alpha_command_types: tuple[Any, ...],
        flood_fill_refresh_command_types: tuple[Any, ...],
        pattern_fill_command_type: Any,
        history_changed_event_type: Callable[..., Any],
        events: Any,
    ) -> None:
        self._canvas = canvas
        self._bookkeeping = CanvasHistoryBookkeepingService(
            canvas,
            history_group_type=history_group_type,
            history_command_type=history_command_type,
            history_changed_event_type=history_changed_event_type,
            events=events,
        )
        self._transaction = CanvasHistoryTransactionService(
            canvas,
            history_patch_type=history_patch_type,
            bookkeeping=self._bookkeeping,
        )
        self._restore = CanvasHistoryRestoreService(
            canvas,
            history_command_type=history_command_type,
            history_patch_type=history_patch_type,
            resize_entry_type=resize_entry_type,
            merge_entry_type=merge_entry_type,
            merge_visible_entry_type=merge_visible_entry_type,
            flatten_all_entry_type=flatten_all_entry_type,
            layer_visibility_command_type=layer_visibility_command_type,
            flood_fill_alpha_command_types=flood_fill_alpha_command_types,
            flood_fill_refresh_command_types=flood_fill_refresh_command_types,
            pattern_fill_command_type=pattern_fill_command_type,
            events=events,
        )

    def snapshot(self) -> tuple[bool, bool, int, int]:
        return self._bookkeeping.snapshot()

    def emit_changed(self) -> None:
        self._bookkeeping.emit_changed()

    def clear_direct(self) -> None:
        self._bookkeeping.clear_direct()

    def _undo_direct(self) -> bool:
        canvas = self._canvas
        if canvas._active_history_group and canvas._active_history_group.entries:
            canvas._push_history_group(canvas._active_history_group)
            canvas._active_history_group = None

        if not canvas._undo_stack:
            return False

        group = canvas._undo_stack.pop()
        canvas.defer_recompose()
        try:
            for entry in reversed(group.entries):
                canvas._restore_entry(entry, use_after=False)
        finally:
            canvas.resume_recompose()
        canvas._redo_stack.append(group)
        canvas._invalidate_layer_cache()
        canvas._recompose()
        canvas._emit_history_changed()
        return True

    def _redo_direct(self) -> bool:
        canvas = self._canvas
        if not canvas._redo_stack:
            return False

        group = canvas._redo_stack.pop()
        canvas.defer_recompose()
        try:
            for entry in group.entries:
                canvas._restore_entry(entry, use_after=True)
        finally:
            canvas.resume_recompose()
        canvas._undo_stack.append(group)
        canvas._invalidate_layer_cache()
        canvas._recompose()
        canvas._emit_history_changed()
        return True

    def record_patch(self, label: str, patch: Any) -> None:
        self._bookkeeping.record_patch(label, patch)

    def record_command(self, label: str, command: Any) -> None:
        self._bookkeeping.record_command(label, command)

    def push_group(self, group: Any) -> None:
        self._bookkeeping.push_group(group)

    def begin_transaction(self, label: str) -> None:
        self._transaction.begin_transaction(label)

    def end_transaction(self) -> None:
        self._transaction.end_transaction()

    def flush_deferred_paint(self) -> None:
        self._transaction.flush_deferred_paint()

    def restore_entry(self, entry: Any, *, use_after: bool) -> None:
        self._restore.restore_entry(entry, use_after=use_after)

    def restore_command(self, command: Any, *, use_after: bool) -> None:
        self._restore.restore_command(command, use_after=use_after)

    def save_flood_fill_alpha_for_history_redo(self, command: Any) -> tuple[Any | None, Any | None]:
        return self._restore.save_flood_fill_alpha_for_history_redo(command)

    def refresh_flood_fill_history_restore(self, command: Any) -> None:
        self._restore.refresh_flood_fill_history_restore(command)

    def restore_patch(self, patch: Any, *, use_after: bool) -> None:
        self._restore.restore_patch(patch, use_after=use_after)
