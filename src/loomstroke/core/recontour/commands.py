"""Command boundary for optional capture and atomic raster corrections."""

from __future__ import annotations

from typing import Any

from ..commands import Command
from .raster import submit_live_stroke
from .session import RasterStrokeSession


class RecontourCommand(Command):
    def __init__(
        self,
        session: RasterStrokeSession,
        action: str,
        *,
        stroke: Any = None,
        lane: int = 0,
        edited: Any = None,
    ) -> None:
        super().__init__(getattr(stroke, "merge_key", None))
        self.session = session
        self.action = action
        self.stroke = stroke
        self.lane = lane
        self.edited = edited

    def apply(self, canvas: Any) -> bool:
        record = self.session
        if self.action == "capture":
            try:
                record.capture_before(canvas, self.stroke)
            except Exception:
                record.fail()
            # Capture failures must never suppress ordinary drawing.
            applied = submit_live_stroke(canvas, self.stroke)
            record.capture_after(canvas, self.lane, self.stroke, applied)
            return applied
        try:
            if self.action == "seal":
                return record.seal(canvas)
            return record.commit(canvas, self.edited, cancel=self.action == "cancel")
        except Exception:
            record.fail()
            return False

    def revert(self, canvas: Any) -> bool:
        # Capture is not a history entry; commit records native raster history patches.
        return False
