"""Own one temporary opacity scalar without retaining any pixel snapshots."""

from __future__ import annotations

import logging
from typing import Any

from .commands_layer_opacity import SetLayerOpacityCommand
from .layer_command_commit_context import _LayerCommandContext

logger = logging.getLogger(__name__)


class _OpacityPreviewOwner:
    def __init__(self, canvas: Any, layer: Any, scope: _LayerCommandContext, active: str):
        self.canvas, self.layer, self.scope = canvas, layer, scope
        self.lease: Any = None
        self.document = getattr(canvas, "_doc", None)
        self.active = active
        self.before = self.expected = layer.opacity
        self.phase = "preview"
        self.written = False
        self.command: Any = None
        self.receipt: Any = None
        self.commit_capture: Any = None
        self.closed_callback: Any = None

    @property
    def closed(self) -> bool:
        return self.phase == "closed"

    def replaced_document(self) -> bool:
        if getattr(self.canvas, "_doc", None) is not self.document:
            return True
        if self.commit_capture is not None:
            return not self.commit_capture.is_active
        # During submit the preview lease was deliberately released after
        # restoring the original. Its absence alone then proves no reset.
        return self.phase != "committing" and self.lease is not None and not self.lease.is_active

    def _matches(self, *, active: bool = False) -> bool:
        return (
            self.scope.current()
            and getattr(self.canvas, "_opacity_preview_owner", None) is self
            and self.layer.opacity == self.expected
            and (not active or self.canvas.current_layer().id == self.active)
        )

    def preview(self, opacity: float) -> bool:
        if self.phase != "preview" or not self._matches(active=True):
            self.cancel()
            return False
        if opacity == self.expected:
            return True
        self.layer.opacity = self.expected = opacity
        self.written = True
        try:
            self.canvas._invalidate_layer_cache()
            self.canvas._recompose()
        except Exception:
            logger.exception("Opacity preview display failed")
            self.cancel()
            return False
        return self.phase == "preview" and self._matches(active=True)

    def _close(self) -> bool:
        try:
            if self.lease is not None:
                self.lease.release()
            if self.commit_capture is not None:
                self.commit_capture.release()
        except Exception:
            logger.exception("Opacity capture release failed; retaining its owner")
            return False
        if getattr(self.canvas, "_opacity_preview_owner", None) is self:
            self.canvas._opacity_preview_owner = None
        command, self.command = self.command, None
        if command is not None:
            command._opacity_preview_owner = None
        self.phase = "closed"
        self.receipt = self.commit_capture = None
        callback = self.closed_callback() if self.closed_callback is not None else None
        if callback is not None:
            try:
                callback(self)
            except Exception:
                logger.exception("Opacity owner closed but its control refresh failed")
        return True

    def _restore_original(self) -> bool:
        if self.phase == "rescue" and self.receipt is not None:
            return bool(self.receipt.rollback())
        if not self._matches():
            return False
        self.layer.opacity = self.expected = self.before
        try:
            self.canvas._invalidate_layer_cache()
        except Exception:
            logger.exception("Opacity original restored but invalidation failed")
        return True

    def cancel(self) -> bool:
        if self.closed:
            return True
        if self.replaced_document() or self.phase in {"recovered", "committed"}:
            return self._close()
        if self.phase == "committing":
            return False
        if not self._restore_original():
            self.phase = "rescue"
            return False
        self.phase = "recovered"
        if not self._close():
            return False
        try:
            if self.scope.current():
                self.canvas._recompose()
        except Exception:
            logger.exception("Opacity cancellation display failed")
        return True

    def commit(self) -> bool:
        from .layer_opacity_preview_boundary import _supports_opacity_gesture  # noqa: PLC0415

        if self.closed:
            return False
        if self.phase != "preview" or not self._matches(active=True):
            self.cancel()
            return False
        if not self.written or self.expected == self.before:
            return self.cancel()
        if not _supports_opacity_gesture(self.canvas):
            self.cancel()
            return False
        after = self.expected
        self.layer.opacity = self.expected = self.before
        try:
            self.canvas._invalidate_layer_cache()
            self.lease.release()
        except Exception:
            logger.exception("Opacity gesture could not prepare its original scalar")
            self.cancel()
            return False
        self.phase = "committing"
        self.command = SetLayerOpacityCommand(self.layer.id, after)
        self.command._opacity_preview_owner = self
        try:
            success = bool(self.canvas.submit(self.command))
        except Exception:
            logger.exception("Opacity gesture submission failed")
            success = False
        return self._settle_submission(success)

    def _settle_submission(self, success: bool) -> bool:
        if success and (self.closed or self.phase == "committed"):
            return True
        # A custom lower dispatcher may claim True without the checked
        # history/DOC acknowledgment. Do not infer an atomic commitment.
        if self.closed:
            return False
        if self.replaced_document():
            self._close()
        elif self.receipt is None and self._matches():
            # No checked scalar mutation occurred; the original is still live.
            self._close()
        else:
            self.phase = "rescue"
            if self.commit_capture is None or not self.commit_capture.is_active:
                self.commit_capture = self.canvas._begin_transient_document_edit()
        return False

    def track_commit(self, receipt: Any, capture: Any) -> None:
        self.receipt, self.commit_capture = receipt, capture

    def acknowledge(self) -> None:
        self.phase = "committed"
        self._close()

    def finish_submit(self, *, committed: bool, recovered: bool) -> None:
        if self.closed:
            return
        if committed or recovered or self.replaced_document():
            self.phase = "committed" if committed else "recovered"
            self._close()
        else:
            # Same-document page/owner replacement is not proof that the
            # temporary scalar disappeared. Keep this exact capture token.
            self.phase = "rescue"
