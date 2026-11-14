"""Prepare an existing RGB layer's import before committing its image history."""

from __future__ import annotations

import logging
from contextlib import nullcontext

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter

from .commands_layer_image_replace import ReplaceLayerImageCommand
from .commands_layer_lookup import find_layer
from .document_capture_guard import is_document_capture_blocked
from .layer_command_commit_context import _CommandHistoryReceipt, _LayerCommandContext

logger = logging.getLogger(__name__)


class ImportIntoLayerCommand(ReplaceLayerImageCommand):
    def __init__(self, layer_id: str, image: QImage) -> None:
        super().__init__(layer_id, image)
        self._submitting = False

    @property
    def retained_bytes(self) -> int:
        return sum(
            image.sizeInBytes() for image in (self._image, self._previous) if image is not None
        )

    def _bump(self, canvas, layer) -> None:
        if not self._submitting:
            super()._bump(canvas, layer)


def import_rgb_into_layer(canvas, pixels) -> str | None:
    layer = canvas._get_active_layer()
    if layer is None or layer.image is None:
        return None
    incoming = canvas._numpy_to_qimage(pixels, 1.0)
    if incoming is None or incoming.isNull():
        return None
    width = max(layer.image.width(), incoming.width())
    height = max(layer.image.height(), incoming.height())
    if width > layer.image.width() or height > layer.image.height():
        output = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        if output.isNull():
            return None
        output.fill(Qt.GlobalColor.transparent)
    else:
        output = layer.image.copy()
    painter = QPainter(output)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
    painter.drawImage(0, 0, incoming)
    painter.end()
    command = ImportIntoLayerCommand(layer.id, output)
    return layer.id if canvas.submit(command) else None


def submit_rgb_layer_import(canvas, command: ImportIntoLayerCommand) -> bool:
    layer = find_layer(canvas, command.layer_id)
    if layer is None or is_document_capture_blocked(canvas):
        return False
    scope = _LayerCommandContext(canvas, (layer,))
    history = _CommandHistoryReceipt(canvas, scope)
    previous, version = layer.image, layer.texture_version
    notifier = getattr(scope.pages, "_history_state_notifier", None)
    batch = notifier.batch() if notifier is not None else nullcontext()
    capture = canvas._begin_transient_document_edit()
    committed, recovered = False, False
    command._submitting = True
    try:
        with batch:
            try:
                if not command.apply(canvas):
                    recovered = True
                    return False
                if not scope.current():
                    return False
                canvas._record_history_command("Import", command)
                if not history.recorded(command):
                    raise RuntimeError("Imported image history was not recorded")
                committed = True
            except Exception:
                if history.can_rollback(command) and layer.image is command._image:
                    layer.image, layer.texture_version = previous, version
                    command._executed = False
                    history.restore()
                    recovered = True
                logger.exception("Existing layer import failed before commitment")
                return False
            try:
                ReplaceLayerImageCommand._bump(canvas, layer)
            except Exception:
                logger.exception("Existing layer import notification failed after commitment")
        return True
    finally:
        command._submitting = False
        if committed or recovered or not scope.current():
            capture.release()
