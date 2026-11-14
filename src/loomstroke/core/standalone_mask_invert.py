"""Commit an alpha-channel layer's inversion as one image-only history edit."""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from contextlib import nullcontext
from inspect import getattr_static
from typing import Any

import numpy as np
from PySide6.QtGui import QImage

from .command_types import Command
from .commands_layer_lookup import find_layer
from .document_capture_guard import is_document_capture_blocked
from .events import DocumentChangeEvent, Events
from .layer_command_commit_context import _CommandHistoryReceipt, _LayerCommandContext
from .layer_mask_submit import _close_pending_mask_paint

logger = logging.getLogger(__name__)
_WORD_FORMATS = (QImage.Format.Format_ARGB32, QImage.Format.Format_ARGB32_Premultiplied)
_BYTE_FORMATS = (QImage.Format.Format_RGBA8888, QImage.Format.Format_RGBA8888_Premultiplied)


def _inverted_image(source: QImage) -> QImage | None:
    image_format = source.format()
    if image_format not in (*_WORD_FORMATS, *_BYTE_FORMATS) or source.isNull():
        return None
    result = QImage(source)
    pixels = result.bits()  # Detach only the inverted image, never the live source.
    if pixels is None:
        return None
    rows = np.frombuffer(pixels, dtype=np.uint8).reshape(result.height(), result.bytesPerLine())
    channel = 0 if image_format in _WORD_FORMATS and sys.byteorder == "big" else 3
    alpha = rows[:, channel : result.width() * 4 : 4]
    np.subtract(255, alpha, out=alpha)
    return result


def _refresh_mask(canvas: Any, layer: Any) -> None:
    layer.texture_version += 1
    canvas._invalidate_layer_cache()
    canvas._publish_layer_event(Events.LAYER_CHANGED, layer=layer, change_type="mask_inverted")


class _StandaloneMaskScope(_LayerCommandContext):
    def emit_document_change(self) -> None:
        if self.current():
            self.canvas._emit_doc_changed(
                DocumentChangeEvent(change_type="invert_mask_layer", source="canvas")
            )


class _InvertStandaloneMaskCommand(Command):
    def __init__(self, layer_id: str) -> None:
        super().__init__()
        self.layer_id = layer_id
        self.attempt: tuple[_StandaloneMaskScope, Callable[[], bool]] | None = None
        self.before: QImage | None = None
        self.after: QImage | None = None

    @property
    def retained_bytes(self) -> int:
        return sum(image.sizeInBytes() for image in (self.before, self.after) if image is not None)

    def _install(self, canvas: Any, image: QImage | None, *, use_after: bool) -> bool:
        layer = find_layer(canvas, self.layer_id)
        if layer is None or layer.layer_type != "mask" or image is None:
            return False
        layer.image = QImage(image)
        self._executed = use_after
        if self.attempt is None:
            try:
                _refresh_mask(canvas, layer)
            except Exception:
                logger.exception("Standalone mask history image restored but refresh failed")
        return True

    def apply(self, canvas: Any) -> bool:
        return self._install(canvas, self.after, use_after=True)

    def revert(self, canvas: Any) -> bool:
        return self._executed and self._install(canvas, self.before, use_after=False)


def _supports_standalone_mask_invert(canvas: Any) -> bool:
    from .canvas_service_simple import SimpleCanvasService  # noqa: PLC0415
    from .canvas_service_simple_command_api import _DEFAULT_RESOURCE_METADATA_HOOKS  # noqa: PLC0415
    from .canvas_service_simple_layer_api import _DEFAULT_INVERT_MASK_LAYER  # noqa: PLC0415

    return (
        isinstance(canvas, SimpleCanvasService)
        and getattr_static(canvas, "submit", None) is _DEFAULT_RESOURCE_METADATA_HOOKS["submit"]
        and getattr_static(canvas, "invert_mask_layer", None) is _DEFAULT_INVERT_MASK_LAYER
    )


def _standalone_mask_invert_action(
    canvas: Any, layer: Any, caller_current: Callable[[], bool]
) -> bool:
    scope = _StandaloneMaskScope(canvas, (layer,))

    def current() -> bool:
        return (
            scope.current()
            and caller_current()
            and scope.current()
            and _supports_standalone_mask_invert(canvas)
        )

    command = _InvertStandaloneMaskCommand(layer.id)
    command.attempt = (scope, current)
    try:
        # The standard dispatcher closes owned Gradient and Opacity previews.
        # Preparation reads the restored image only after those boundaries.
        return current() and bool(canvas.submit(command)) and current()
    finally:
        command.attempt = None


def _capture_standalone_mask_restore_notice(canvas: Any, group: Any) -> _StandaloneMaskScope | None:
    if any(
        type(getattr(entry, "command", None)) is _InvertStandaloneMaskCommand
        for entry in getattr(group, "entries", ())
    ):
        return _StandaloneMaskScope(canvas)
    return None


def _prepare_inversion(
    scope: _StandaloneMaskScope, current: Callable[[], bool]
) -> tuple[QImage, QImage] | None:
    canvas = scope.canvas
    layer = scope.layers[0]
    if not current() or is_document_capture_blocked(canvas):
        return None
    if not _close_pending_mask_paint(canvas, scope) or not current():
        return None
    source = layer.image
    if layer.layer_type != "mask" or not isinstance(source, QImage):
        return None
    source_key = source.cacheKey()
    after = _inverted_image(source)
    if after is None or is_document_capture_blocked(canvas):
        return None
    if not current() or layer.image is not source or source.cacheKey() != source_key:
        return None
    return source, after


def _notify_inversion(scope: _StandaloneMaskScope, capture: Any) -> None:
    canvas = scope.canvas
    try:
        scope.emit_document_change()
    except Exception:
        logger.exception("Standalone mask committed but document notification failed")
    capture.release()
    try:
        if scope.current():
            _refresh_mask(canvas, scope.layers[0])
        if scope.current():
            canvas._recompose()
    except Exception:
        logger.exception("Standalone mask committed but refresh failed")


def _submit_standalone_mask_invert(canvas: Any, command: _InvertStandaloneMaskCommand) -> bool:
    if type(command) is not _InvertStandaloneMaskCommand or command.attempt is None:
        return False
    scope, current = command.attempt
    prepared = _prepare_inversion(scope, current)
    if prepared is None:
        return False
    source, after = prepared
    source_key = source.cacheKey()
    layer = scope.layers[0]

    command.before, command.after = QImage(source), after
    history = _CommandHistoryReceipt(canvas, scope)
    notifier = getattr(scope.pages, "_history_state_notifier", None)
    batch = notifier.batch() if notifier is not None else nullcontext()
    capture = canvas._begin_transient_document_edit()
    installed = None
    committed = recovered = False
    try:
        with batch:
            try:
                if not current() or layer.image is not source or source.cacheKey() != source_key:
                    return False
                if not command.apply(canvas):
                    raise RuntimeError("Standalone mask application was not confirmed")
                installed = layer.image
                if not scope.current():
                    return False
                canvas._record_history_command("Invert Transparency", command)
                if (
                    not history.recorded(command)
                    or layer.image is not installed
                    or installed.cacheKey() != after.cacheKey()
                ):
                    raise RuntimeError("Standalone mask history was not recorded")
                committed = True
            except Exception:
                if (
                    installed is not None
                    and layer.image is installed
                    and installed.cacheKey() == after.cacheKey()
                    and history.can_rollback(command)
                ):
                    layer.image = source
                    command._executed = False
                    history.restore()
                    recovered = True
                logger.exception("Standalone mask failed before commitment")
                return False
            _notify_inversion(scope, capture)
        return True
    finally:
        if committed or recovered or installed is None or not scope.current():
            capture.release()
