"""One prepared RGB image and resource record, owned across Place Undo/Redo."""

from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter

from .command_types import Command
from .commands_layer_lookup import find_layer
from .events import DocumentChangeEvent
from .layer_creation_history import CreatedLayerHistory
from .linked_assets.resource_layer_metadata_codec import ResourceLayerMetadataCodec


class PlaceResourceCommand(Command):
    """Keep prepared pixels; never decode the source again during history."""

    def __init__(self, image: QImage, record, *, name: str = "Imported Layer") -> None:
        super().__init__()
        self._image = image.copy()
        self._record = deepcopy(record)
        self._name = name
        self._created = CreatedLayerHistory()
        self._submitting = False

    @property
    def layer_id(self) -> str | None:
        return self._created.layer.id if self._created.layer is not None else None

    @property
    def retained_bytes(self) -> int:
        return self._created.retained_bytes

    def _prepare(self, canvas) -> None:
        layer = canvas._layer_tree()._prepare_layer_for_history(self._name, layer_type="raster")
        width = max(layer.image.width(), self._image.width())
        height = max(layer.image.height(), self._image.height())
        image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        if image.isNull():
            raise MemoryError("The placed image buffer could not be allocated.")
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.drawImage(0, 0, self._image)
        painter.end()
        layer.image = image
        layer.metadata = ResourceLayerMetadataCodec().write(layer.metadata, self._record)
        self._created.layer = layer
        self._created.active_before = canvas.current_layer().id if canvas._layers else None
        # Match RGB import's root insertion immediately above the old active
        # entry, retaining its background/root-zero protection.
        end = len(canvas._layers)
        active = canvas._active_index
        self._created.index = max(min(1, end), min(active + 1, end)) if active >= 0 else end
        # Redo owns the complete placed layer; discard the second source buffer.
        self._image = QImage()

    def apply(self, canvas) -> bool:
        if any(layer.native_cmyk is not None for layer in canvas._layer_tree().flatten_layers()):
            return False
        if self._created.layer is None:
            if self._image.isNull():
                return False
            self._prepare(canvas)
        succeeded = self._created.restore(canvas)
        if succeeded:
            self._executed = True
            self._notify(canvas)
        return succeeded

    def revert(self, canvas) -> bool:
        if not self._executed or not self._created.remove(canvas):
            return False
        self._executed = False
        self._notify(canvas)
        return True

    def _rollback(self, canvas) -> bool:
        """Also accept a preparation failure that never attached its layer."""
        live = find_layer(canvas, self.layer_id) if self.layer_id else None
        if live is None:
            return True
        if live is not self._created.layer:
            return False
        succeeded = self._created.remove(canvas)
        if succeeded:
            self._executed = False
        return succeeded

    def _notify(self, canvas) -> None:
        if not self._submitting:
            canvas._emit_doc_changed(DocumentChangeEvent(change_type="resource.place"))
