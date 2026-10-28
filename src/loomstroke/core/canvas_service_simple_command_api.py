"""Canvas service CommandApi mixin."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeAlias, cast

from PySide6.QtCore import QRect

from loomstroke.core.canvas_layer_model import _Layer
from loomstroke.core.canvas_vector_command_submit_service import (
    CanvasVectorCommandSubmitService,
)
from loomstroke.core.commands import (
    ApplySelectionMaskCommand,
    Command,
    CutSelectionCommand,
    EraseCommand,
    LayerVisibilityCommand,
    ModifySelectionMaskCommand,
    MoveLayerCommand,
    PaintCommand,
    PasteSelectionCommand,
    SetActiveColorCommand,
    StampBrushCommand,
    TransformCommand,
    TransformLayerCommand,
    TransformSelectionCommand,
    UpdateSelectionMaskCommand,
)
from loomstroke.core.document_capture_guard import notify_document_capture_committed
from loomstroke.core.fill.commands_fill import (
    FloodFillCommand,
    FloodFillToNewLayerCommand,
    PatternFillCommand,
)
from loomstroke.host_api import LayerRef
from loomstroke.plugins.basic_gradient.gradient_command import GradientCommand

if TYPE_CHECKING:
    from loomstroke.core.canvas import Canvas
    from loomstroke.core.canvas_service_simple_accessors import CanvasServiceAccessorsMixin

    class _CanvasServiceCommandApiBase(CanvasServiceAccessorsMixin):
        _command_dispatcher: Any
        _in_transaction: bool
        _txn_preimage_capture: Any

else:

    class _CanvasServiceCommandApiBase:
        pass


logger = logging.getLogger(__name__)
_PEN_LAG_DIAG = os.environ.get("LOOMSTROKE_PEN_LAG_DIAG", "").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
LayerValue: TypeAlias = LayerRef | Mapping[str, int] | bool | str | int | None


class CanvasServiceCommandApiMixin(_CanvasServiceCommandApiBase):
    def submit(self, cmd: Command, *, merge_key: str | None = None) -> bool:
        """Submit a command through the command dispatcher boundary."""

        success = cast(bool, self._command_dispatcher.submit(cmd, merge_key=merge_key))
        if success:
            notify_document_capture_committed(cmd)
        return success

    def _submit_command_direct(self, cmd: Command, *, merge_key: str | None = None) -> bool:
        """Legacy command dispatch table retained behind the adapter."""
        return self._command_routing().submit(cmd, merge_key=merge_key)

    def _submit_tree_command(self, cmd: Command) -> bool:
        """Execute a tree-structure command (clip/effect/mask) with undo support."""
        return self._tree_command_submit().submit(cmd)

    @staticmethod
    def _is_vector_command(cmd: Command) -> bool:
        return CanvasVectorCommandSubmitService.is_vector_command(cmd)

    def _submit_set_active_color(self, cmd: SetActiveColorCommand) -> bool:
        return bool(cmd.apply(cast("Canvas", self)))

    def _submit_paint(self, cmd: PaintCommand) -> bool:
        return self._stroke_submit().submit_paint(cmd)

    def _submit_stamp_brush(self, cmd: StampBrushCommand) -> bool:
        return self._stroke_submit().submit_stamp_brush(cmd)

    def _submit_erase(self, cmd: EraseCommand) -> bool:
        return self._stroke_submit().submit_erase(cmd)

    def _submit_flood_fill(
        self, cmd: FloodFillCommand | FloodFillToNewLayerCommand | PatternFillCommand
    ) -> bool:
        return self._fill_gradient_submit().submit_flood_fill(cmd)

    def _submit_gradient(self, cmd: GradientCommand) -> bool:
        return self._fill_gradient_submit().submit_gradient(cmd)

    def _submit_transform(self, cmd: TransformCommand) -> bool:
        """Handle raster layer transforms (rotate, scale, flip).

        TransformCommand was designed for numpy-buffer layers, but
        SimpleCanvasService uses QImage.  We translate the command
        into QImage operations here.
        """
        return self._transform_submit().submit(cmd)

    def _submit_update_selection_mask(self, cmd: UpdateSelectionMaskCommand) -> bool:
        return self._selection_mask_submit().submit_update(cmd)

    def _submit_apply_selection_mask(self, cmd: ApplySelectionMaskCommand) -> bool:
        return self._selection_mask_submit().submit_apply(cmd)

    def _submit_magic_wand(self, cmd: Command) -> bool:
        return self._selection_change_submit().submit(cmd, "MagicWand")

    def _submit_vector_fill_to_selection(self, cmd: Command) -> bool:
        return self._selection_change_submit().submit(cmd, "VectorFillToSelection")

    def _submit_modify_selection_mask(self, cmd: ModifySelectionMaskCommand) -> bool:
        return self._selection_mask_submit().submit_modify(cmd)

    def _submit_cut_selection(self, cmd: CutSelectionCommand) -> bool:
        return self._selection_content_submit().submit(cmd, "Cut Selection")

    def _submit_paste_selection(self, cmd: PasteSelectionCommand) -> bool:
        return self._selection_content_submit().submit(cmd, "Paste Selection")

    def _submit_transform_selection(self, cmd: TransformSelectionCommand) -> bool:
        return self._selection_content_submit().submit(cmd, "Transform Selection")

    def _submit_move_layer(self, cmd: MoveLayerCommand) -> bool:
        return self._simple_layer_command_submit().submit(cmd, "Move Layer")

    def _submit_layer_visibility(self, cmd: LayerVisibilityCommand) -> bool:
        return self._layer_visibility_submit().submit(cmd)

    def _submit_transform_layer(self, cmd: TransformLayerCommand) -> bool:
        return self._simple_layer_command_submit().submit(cmd, "Transform Layer")

    def _apply_vector_command(self, cmd: Command) -> bool:
        """Handle lightweight vector commands by storing vector objects."""
        return self._vector_command_submit().submit(cmd)

    def begin_transaction(self, label: str) -> None:
        """Begin a command transaction.

        Args:
            label: Transaction label for history
        """
        self._history().begin_transaction(label)

    def end_transaction(self) -> None:
        """End the current transaction and trigger recomposition."""
        self._history().end_transaction()

    def _txn_flush_deferred_paint(self) -> None:
        """Flush deferred paint history accumulated during a transaction.

        During an active stroke transaction, per-segment ``QImage.copy`` and
        ``zlib.compress`` are skipped.  Instead a single before-snapshot is
        captured on the first paint and the dirty rect is accumulated.  This
        method creates the final history patch from the snapshot and the
        current layer state, then resets the deferred state.
        """
        self._history().flush_deferred_paint()

    def _txn_try_defer_paint(
        self, layer: _Layer, bounds: QRect, offset_x: int, offset_y: int
    ) -> bool:
        """Try to defer paint history recording during an active transaction.

        Returns True if the paint was deferred (caller should skip history).
        """
        if not self._in_transaction:
            return False
        if layer.image is None:
            return False
        capture = self._txn_preimage_capture
        mask_edit = bool(getattr(self, "_mask_edit_flag", False))
        if capture.requires_flush(
            layer_id=layer.id,
            image=layer.image,
            offset=(offset_x, offset_y),
            mask_edit=mask_edit,
        ):
            self._txn_flush_deferred_paint()
        capture.capture_segment(
            layer_id=layer.id,
            image=layer.image,
            bounds=bounds,
            offset=(offset_x, offset_y),
            mask_edit=mask_edit,
        )
        return True

    def is_busy(self) -> bool:
        """Check if service is busy (always False for simple implementation)."""
        return False


_DEFAULT_RESOURCE_METADATA_HOOKS = {
    name: getattr(CanvasServiceCommandApiMixin, name)
    for name in ("submit", "begin_transaction", "end_transaction")
}
