"""Undo payload of an Image Size operation (CT-13 spec §11).

Extends the canvas-resize snapshot with the state a resample also changes:
DPI, layer / effect masks, layer transforms and the selection.  Because it
*is* a ``_ResizeSnapshot`` it rides the existing ``_ResizeEntry`` history
plumbing; the canvas facade routes restores by type.  Runtime-only, never
persisted to ``.lstroke``.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QImage

from loomstroke.core.canvas_history_entries import _ResizeSnapshot

#: ``(layer_id, compressed_bytes, width, height, QImage.Format)``
MaskSnapshot = tuple[str, bytes, int, int, QImage.Format]
#: ``(compressed_bytes, width, height, QImage.Format, (x, y, w, h))``
SelectionSnapshot = tuple[bytes, int, int, QImage.Format, tuple[int, int, int, int]]


@dataclass(slots=True)
class ResampleSnapshot(_ResizeSnapshot):
    """Canvas state captured before / after ``resample_canvas``."""

    dpi: int = 96
    selection: SelectionSnapshot | None = None

    @property
    def filter_masks(self) -> list[MaskSnapshot]:
        return self.effect_masks

    @filter_masks.setter
    def filter_masks(self, value: list[MaskSnapshot]) -> None:
        self.effect_masks = value
