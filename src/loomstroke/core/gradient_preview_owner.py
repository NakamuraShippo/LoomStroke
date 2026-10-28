"""Own one temporary Gradient source without depending on a tool or UI."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import numpy as np
from PySide6.QtGui import QImage

from .commands_layer_lookup import find_layer
from .layer_command_commit_context import _LayerCommandContext

logger = logging.getLogger(__name__)


class GradientPreviewOwner:
    def __init__(
        self,
        canvas: Any,
        layer: Any,
        target: np.ndarray,
        scope: _LayerCommandContext,
        lease: Any,
        release: Callable[[Any], bool],
        closed: Callable[[GradientPreviewOwner], None],
    ) -> None:
        self.canvas, self.layer, self.target = canvas, layer, target
        self.layer_id, self.scope = layer.id, scope
        self.document = getattr(canvas, "_doc", None)
        self.backup = target.copy()
        self.lease, self._release, self._closed_callback = lease, release, closed
        self.source = getattr(layer, "image", None)
        if not isinstance(self.source, QImage):
            self.source = getattr(layer, "buffer", None)
        self.native: Any = getattr(layer, "native_cmyk", None)
        self.native_version: Any = getattr(self.native, "version", None)
        self.native_original: Any | None = None
        self.expected_version: Any = getattr(layer, "texture_version", None)
        self.expected_key = self._image_key()
        self.conditions = self._conditions()
        self.command: Any = None
        self.phase = "preview"

    @classmethod
    def capture(
        cls,
        canvas: Any,
        layer_id: str | None,
        resolve: Callable[[], tuple[Any, np.ndarray | None]],
        begin: Callable[[Any, Any], Any],
        release: Callable[[Any], bool],
        previous: Any,
        acquired: Callable[[Any], None],
        closed: Callable[[GradientPreviewOwner], None],
    ) -> GradientPreviewOwner | None:
        layer = find_layer(canvas, layer_id) if layer_id is not None else None
        if layer is None:
            return None
        current = getattr(canvas, "_gradient_preview_owner", None)
        if isinstance(current, cls) and not current.is_closed:
            return None
        scope = _LayerCommandContext(canvas, (layer,))
        lease = begin(canvas, previous)
        acquired(lease)
        try:
            resolved, target = resolve()
            if not scope.current() or resolved is not layer or target is None:
                if release(lease):
                    acquired(None)
                return None
            owner = cls(canvas, layer, target, scope, lease, release, closed)
            if not scope.current():
                if release(lease):
                    acquired(None)
                return None
            canvas._gradient_preview_owner = owner
            return owner
        except Exception:
            # This factory has not written live pixels. A failed snapshot is
            # safe to release; the caller retains the lease if release fails.
            if release(lease):
                acquired(None)
            raise

    @property
    def is_closed(self) -> bool:
        return self.phase == "closed"

    def _image_key(self) -> int | None:
        return self.source.cacheKey() if isinstance(self.source, QImage) else None

    def _conditions(self) -> tuple[Any, ...]:
        layer = self.layer
        selection = getattr(self.canvas, "_selection_mask", None)
        channels = getattr(self.canvas, "_channels_service", None)
        return (
            bool(getattr(layer, "locked", False)),
            bool(getattr(layer, "alpha_lock", False)),
            bool(getattr(layer, "mask_edit_mode", False)),
            getattr(layer, "offset_x", 0),
            getattr(layer, "offset_y", 0),
            repr(getattr(layer, "transform", None)),
            getattr(layer, "parent_id", None),
            selection.cacheKey() if isinstance(selection, QImage) else None,
            repr(getattr(self.canvas, "_selection_bounds", None)),
            tuple(channels.selected) if channels is not None else (0, 1, 2),
        )

    def source_matches(self, canvas: Any = None, *, attempt: bool = False) -> bool:
        if self.is_closed or (canvas is not None and canvas is not self.canvas):
            return False
        if not self.scope.current() or find_layer(self.canvas, self.layer_id) is not self.layer:
            return False
        source = getattr(self.layer, "image", None)
        if not isinstance(source, QImage):
            source = getattr(self.layer, "buffer", None)
        if source is not self.source or self.target.shape != self.backup.shape:
            return False
        if getattr(self.layer, "native_cmyk", None) is not self.native:
            return False
        version = getattr(self.layer, "texture_version", None)
        native_version = getattr(self.native, "version", None)
        if attempt:
            return (
                self.phase == "committing"
                and (
                    version is None or self.expected_version <= version <= self.expected_version + 2
                )
                and (
                    native_version is None
                    or self.native_version <= native_version <= self.native_version + 1
                )
            )
        return (
            version == self.expected_version
            and self._image_key() == self.expected_key
            and native_version == self.native_version
        )

    def can_edit(self, canvas: Any) -> bool:
        if not self.source_matches(canvas) or self._conditions() != self.conditions:
            return False
        getter = getattr(canvas, "current_layer", None)
        active = getter() if callable(getter) else getattr(canvas, "active_layer", None)
        return active is not None and getattr(active, "id", None) == self.layer_id

    def foreign_document(self) -> bool:
        return getattr(self.canvas, "_doc", None) is not self.document or (
            self.scope.page_set is not None
            and getattr(self.scope.pages, "_pages", None) is not self.scope.page_set
        )

    def pixels_written(self) -> None:
        self.expected_version = getattr(self.layer, "texture_version", None)
        self.expected_key = self._image_key()

    def resolve(self, canvas: Any) -> tuple[Any, np.ndarray | None]:
        return (self.layer, self.target) if self.source_matches(canvas) else (None, None)

    def _write_original(self) -> None:
        self.target[:] = self.backup
        version = getattr(self.layer, "texture_version", None)
        if version is not None:
            self.layer.texture_version = version + 1
        self.pixels_written()
        mark_dirty = getattr(self.layer, "mark_dirty", None)
        if callable(mark_dirty):
            try:
                height, width = self.target.shape[:2]
                mark_dirty(0, 0, width, height)
            except Exception:
                logger.exception("Gradient original recovered but dirty marking failed")

    def bind(self, command: Any) -> None:
        self.command, self.phase = command, "queued"

    def prepare(self, canvas: Any, command: Any) -> bool:
        if self.command is not command or self.phase != "queued" or not self.can_edit(canvas):
            return False
        if self.native is not None:
            self.native_original = self.native.snapshot()
            if not self.can_edit(canvas):
                return False
        self.phase = "committing"
        self._write_original()
        command._executed = False
        return True

    def restore(self, *, attempt: bool = False) -> bool:
        if not self.source_matches(attempt=attempt):
            return False
        if attempt and self.native_original is not None:
            height, width = self.native_original.alpha.shape
            self.native.patch((0, 0, width, height), self.native_original)
            self.native_version = self.native.version
        self._write_original()
        return True

    def close(self, *, committed: bool = False) -> bool:
        if not self._release(self.lease):
            return False
        self.phase = "closed"
        if getattr(self.canvas, "_gradient_preview_owner", None) is self:
            self.canvas._gradient_preview_owner = None
        command, self.command = self.command, None
        self.native_original = None
        if command is not None:
            command._gradient_preview_owner = None
            command._gradient_preview_aborted = not committed
            command._document_capture_lease = None
        try:
            self._closed_callback(self)
        except Exception:
            logger.exception("Gradient preview owner cleanup failed")
        return True

    def cancel(self) -> bool:
        if self.is_closed:
            return True
        if self.foreign_document():
            # A completed document replacement invalidates the old preview,
            # but never authorizes copying its pixels into the new document.
            return self.close()
        if self.phase in {"recovered", "committed"}:
            return self.close(committed=self.phase == "committed")
        if not self.restore() or not self.close():
            return False
        try:
            self.canvas._recompose()
        except Exception:
            logger.exception("Gradient preview display refresh failed after original recovery")
        return True

    def acknowledge(self) -> bool:
        # Document replacement can invalidate the old token before this late
        # acknowledgment. Releasing it cannot affect a newer owner's lease.
        self.phase = "committed"
        return self.close(committed=True)
