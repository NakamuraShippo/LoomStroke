"""Command-only boundary for live pen capture and the final tapered tail."""

from __future__ import annotations

import logging
from typing import Any

from .commands import Command, StampBrushCommand

logger = logging.getLogger(__name__)


class PenTaperCommand(Command):
    def __init__(
        self, session: Any, *, stroke: Any = None, lane: int = 0, recontour: Any = None
    ) -> None:
        super().__init__(getattr(stroke, "merge_key", None))
        self.session = session
        self.stroke = stroke
        self.lane = lane
        self.recontour = recontour

    def apply(self, canvas: Any) -> bool:
        if self.stroke is None:
            return bool(self.session.finish(canvas, self.recontour))
        cmd = self.session.prepare(self.stroke, self.lane)
        try:
            self.session.capture_before(canvas, cmd)
        except Exception:
            logger.warning("Pen taper capture failed; preserving the live stroke", exc_info=True)
            self.session.error = "Pen taper could not be completed. The stroke was preserved."
            self.session.close()
        record = self.recontour
        if record is not None:
            try:
                record.capture_before(canvas, cmd)
            except Exception:
                record.fail()
        submit = (
            canvas._submit_stamp_brush
            if isinstance(cmd, StampBrushCommand)
            else canvas._submit_paint
        )
        applied = bool(submit(cmd))
        self.session.capture_after(canvas, self.lane, cmd, applied)
        if record is not None:
            record.capture_after(canvas, self.lane, cmd, applied)
        return applied

    def revert(self, canvas: Any) -> bool:
        # Raster transaction patches own history; capture itself adds no history entry.
        return False
