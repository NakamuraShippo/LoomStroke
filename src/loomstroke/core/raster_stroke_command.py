"""One sparse command retains source tiles across a placed raster stroke."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRect
from PySide6.QtGui import QImage, QPainter

from .command_layer_access import _layer_transform, _resolve_layer_by_id
from .command_types import Command, DirtyRegion
from .raster_edit_geometry import RasterEditGeometry


@dataclass
class _StrokeTile:
    rect: QRect
    before: QImage
    after: QImage


class RasterStrokeCommand(Command):
    raster_stroke_command = True

    def __init__(self, layer, *, erase=False):
        super().__init__()
        self.layer_id, self.size = layer.id, layer.image.size()
        self.transform = _layer_transform(layer)
        self.geometry = RasterEditGeometry.capture(layer)
        self.kind = "erase" if erase else "paint"
        self.tiles = {}
        self.local_dirty = QRect()
        self.version = layer.texture_version

    @property
    def retained_bytes(self):
        return sum(
            tile.before.sizeInBytes() + tile.after.sizeInBytes() for tile in self.tiles.values()
        )

    def valid(self, layer):
        return (
            layer is not None
            and layer.image is not None
            and layer.image.size() == self.size
            and _layer_transform(layer) == self.transform
        )

    def prepare(self, layer, rect, after):
        changes = {}
        for y in range(rect.top() // 128, rect.bottom() // 128 + 1):
            for x in range(rect.left() // 128, rect.right() // 128 + 1):
                tile_rect = QRect(x * 128, y * 128, 128, 128).intersected(layer.image.rect())
                current = layer.image.copy(tile_rect)
                result = current.copy()
                painter = QPainter(result)
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                try:
                    painter.drawImage(rect.topLeft() - tile_rect.topLeft(), after)
                finally:
                    painter.end()
                if current == result:
                    continue
                previous = self.tiles.get((x, y))
                changes[(x, y)] = _StrokeTile(
                    tile_rect, previous.before if previous else current, result
                )
        return changes

    def _write(self, canvas, tiles, *, before, dirty, refresh=True):
        layer = _resolve_layer_by_id(canvas, self.layer_id)
        if not self.valid(layer):
            return False
        painter = QPainter(layer.image)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        try:
            for tile in tiles.values():
                painter.drawImage(tile.rect.topLeft(), tile.before if before else tile.after)
        finally:
            painter.end()
        layer.texture_version += 1
        self.version = layer.texture_version
        document = self.geometry.document_rect(dirty)
        if refresh:
            canvas._recompose_region(document)
        canvas._emit_layer_content_changed(layer, document, {"command": self.kind})
        return True

    def update(self, canvas, layer, rect, after):
        if not self._executed or not self.valid(layer) or layer.texture_version != self.version:
            return False
        changes = self.prepare(layer, rect, after)
        if not changes:
            return False
        previous = {
            key: _StrokeTile(tile.rect, layer.image.copy(tile.rect), layer.image.copy(tile.rect))
            for key, tile in changes.items()
        }
        try:
            if not self._write(canvas, changes, before=False, dirty=rect, refresh=False):
                return False
        except Exception:
            self._write(canvas, previous, before=True, dirty=rect)
            raise
        self.tiles.update(changes)
        self.local_dirty = self.local_dirty.united(rect)
        self._set_dirty()
        return True

    def _set_dirty(self):
        rect = self.geometry.document_rect(self.local_dirty)
        self._dirty_region = DirtyRegion(rect.x(), rect.y(), rect.width(), rect.height())

    def apply(self, canvas, *, refresh=True):
        if not self.tiles or not self._write(
            canvas, self.tiles, before=False, dirty=self.local_dirty, refresh=refresh
        ):
            return False
        self._executed = True
        self._set_dirty()
        return True

    def revert(self, canvas):
        if not self._executed or not self._write(
            canvas, self.tiles, before=True, dirty=self.local_dirty
        ):
            return False
        self._executed = False
        return True


def current_placed_stroke(canvas, layer, *, erase):
    group = canvas._active_history_group if canvas._in_transaction else None
    command = getattr(group.entries[-1], "command", None) if group and group.entries else None
    kind = "erase" if erase else "paint"
    return (
        command
        if isinstance(command, RasterStrokeCommand)
        and command.layer_id == layer.id
        and command.kind == kind
        else None
    )


def placed_stroke_history_dirty(canvas):
    group = canvas._active_history_group
    if not group or not group.entries:
        return None
    commands = [getattr(entry, "command", None) for entry in group.entries]
    if not all(isinstance(command, RasterStrokeCommand) for command in commands):
        return None
    layer_id = commands[0].layer_id
    if any(command.layer_id != layer_id for command in commands):
        return None
    dirty = QRect()
    for command in commands:
        region = command.dirty_region
        dirty = dirty.united(QRect(region.x, region.y, region.width, region.height))
    return layer_id, dirty
