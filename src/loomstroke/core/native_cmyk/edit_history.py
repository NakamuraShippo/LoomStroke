"""Record a native raster edit before notifying the document of a change."""

from __future__ import annotations

from typing import Any

from ..canvas_history_clear_continuation import _HistoryClearContinuation
from ..command_types import Command
from ..events import DocumentChangeEvent
from ..raster_history_commit import record_raster_history


def commit_native_raster_command(canvas: Any, command: Command, label: str) -> bool:
    if getattr(canvas, "_pending_native_raster_doc_events", None) is not None:
        return False
    owner = _HistoryClearContinuation(canvas)
    pending: list[DocumentChangeEvent] = []
    canvas._pending_native_raster_doc_events = (owner, pending)

    def restore() -> None:
        # Revert the original ink/selection command. Pixel writes advance the
        # native tile revision again so caches discard any tentative result.
        if command._executed and not command.revert(canvas):
            raise RuntimeError("Could not restore rejected Native CMYK edit")

    try:
        success = record_raster_history(
            canvas,
            lambda: canvas._record_history_command(label, command),
            restore,
            apply=lambda: bool(command.apply(canvas)),
        )
    finally:
        canvas._pending_native_raster_doc_events = None
    if success:
        for event in pending:
            if not owner.is_current():
                break
            canvas._emit_doc_changed(event)
    return success
