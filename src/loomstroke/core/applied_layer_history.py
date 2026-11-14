"""Recover a confirmed layer edit if registering its history raises."""

from __future__ import annotations

import logging

from .canvas_command_commit_guard import CanvasCommandCommitGuard
from .layer_command_commit_context import _LayerCommandContext

logger = logging.getLogger(__name__)


class AppliedLayerHistoryAttempt:
    def __init__(self, canvas) -> None:
        from .canvas_service_simple import SimpleCanvasService  # noqa: PLC0415

        self.canvas = canvas
        self.guard = self.scope = None
        if isinstance(canvas, SimpleCanvasService):
            self.guard = CanvasCommandCommitGuard(canvas)
            self.scope = _LayerCommandContext(canvas)

    def record(self, command, label: str, *, confirmed: bool) -> bool:
        scope = self.scope
        if scope is not None and not scope.current():
            return False
        try:
            self.canvas._record_history_command(label, command)
        except Exception:
            if confirmed and scope is not None and scope.current():
                try:
                    self.guard.rollback(command)
                except Exception as error:
                    logger.warning("Applied layer recovery failed (%s)", type(error).__name__)
            # Preserve the existing caller's exception, including its identity.
            raise
        return True
