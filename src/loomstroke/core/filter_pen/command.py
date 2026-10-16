"""One sparse, mutable command owns all patches of a live filter stroke."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from weakref import ref

import numpy as np
from PySide6.QtCore import QRect
from PySide6.QtGui import QImage, QPainter

from loomstroke.core.canvas_command_commit_guard import CanvasCommandCommitGuard
from loomstroke.core.channels.channel_pixel_policy import ChannelPixelPolicy
from loomstroke.core.command_types import Command, DirtyRegion
from loomstroke.core.commands_layer_lookup import find_layer
from loomstroke.core.filter_pen.compatibility import bind_legacy_names as _bind_legacy_names

from .pixels import mix_patch
from .snapshot import FilterPenSnapshot

TILE_SIZE = 128


@dataclass
class FilterPenTile:
    rect: QRect
    before: QImage
    after: QImage


class FilterPenStrokeCommand(Command):
    """Keep only touched tiles in history, while a stroke may extend its coverage."""

    filter_pen_command = True
    effect_pen_command = True  # Compatibility marker for older canvas routing.

    def __init__(self, snapshot: FilterPenSnapshot) -> None:
        super().__init__()
        self.layer_id = snapshot.layer.id
        self.origin = snapshot.origin
        self.size = snapshot.image.size()
        self.geometry = snapshot.geometry
        self.transform = snapshot.transform
        self.tiles: dict[tuple[int, int], FilterPenTile] = {}
        self._expected_version = snapshot.texture_version
        self._initial_layer = ref(snapshot.layer)
        self._ever_applied = False

    @property
    def expected_version(self) -> int:
        return self._expected_version

    @property
    def retained_bytes(self) -> int:
        """Report tile payloads without retaining the original full layer image."""
        seen: set[int] = set()
        total = 0
        for tile in self.tiles.values():
            for image in (tile.before, tile.after):
                if id(image) not in seen:
                    seen.add(id(image))
                    total += image.sizeInBytes()
        return total

    def prepare(
        self,
        snapshot: FilterPenSnapshot,
        filtered: QImage,
        coverage: Any,
        dirty: QRect,
        strength: float,
        keys: set[tuple[int, int]] | None = None,
    ) -> dict[tuple[int, int], FilterPenTile]:
        dirty = dirty.intersected(snapshot.image.rect())
        changes: dict[tuple[int, int], FilterPenTile] = {}
        if dirty.isEmpty():
            return changes
        if keys is None:
            keys = {
                (x, y)
                for y in range(dirty.top() // TILE_SIZE, dirty.bottom() // TILE_SIZE + 1)
                for x in range(dirty.left() // TILE_SIZE, dirty.right() // TILE_SIZE + 1)
            }
        for key in keys:
            x, y = key
            rect = QRect(x * TILE_SIZE, y * TILE_SIZE, TILE_SIZE, TILE_SIZE)
            rect = rect.intersected(snapshot.image.rect())
            mask = coverage.coverage(rect)
            original = self.tiles.get(key)
            if not np.any(mask) and original is None:
                continue
            after = mix_patch(snapshot, filtered, rect, mask, strength)
            if snapshot.selected_channels != (0, 1, 2):
                after = ChannelPixelPolicy.merge(
                    snapshot.image.copy(rect), after, snapshot.selected_channels
                )
            previous = snapshot.layer.image.copy(rect)
            if previous == after:
                continue
            before = original.before if original else snapshot.image.copy(rect)
            changes[key] = FilterPenTile(rect, before, after)
        return changes

    def apply(self, canvas: Any) -> bool:
        layer = find_layer(canvas, self.layer_id)
        if not self._target_valid(layer) or not self.tiles:
            return False
        if not self._ever_applied and (
            layer is not self._initial_layer() or layer.texture_version != self._expected_version
        ):
            return False
        self._executed = True
        self._write(canvas, layer, self.tiles, before=False)
        self._ever_applied = True
        return True

    def update(self, canvas: Any, changes: dict[tuple[int, int], FilterPenTile]) -> bool:
        if not self._executed or not changes:
            return False
        layer = find_layer(canvas, self.layer_id)
        if (
            layer is not self._initial_layer()
            or not self._target_valid(layer)
            or layer.texture_version != self._expected_version
        ):
            return False
        previous_tiles = {key: self.tiles.get(key) for key in changes}
        previous_pixels = {key: layer.image.copy(tile.rect) for key, tile in changes.items()}
        try:
            self._write(canvas, layer, changes, before=False)
        except Exception:
            painter = QPainter(layer.image)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            for key, image in previous_pixels.items():
                rect = changes[key].rect
                painter.drawImage(rect.x(), rect.y(), image)
            painter.end()
            layer.texture_version += 1
            self._expected_version = layer.texture_version
            canvas._recompose_region(self._canvas_rect(changes))
            raise
        for key, tile in changes.items():
            previous = previous_tiles[key]
            if previous is not None:
                tile.before = previous.before
            self.tiles[key] = tile
        return True

    def revert(self, canvas: Any) -> bool:
        layer = find_layer(canvas, self.layer_id)
        if not self._executed or not self._target_valid(layer):
            return False
        self._write(canvas, layer, self.tiles, before=True)
        self._executed = False
        return True

    def _target_valid(self, layer: Any) -> bool:
        return bool(
            layer is not None
            and layer.layer_type == "raster"
            and layer.image is not None
            and layer.image.size() == self.size
            and (layer.offset_x, layer.offset_y) == self.origin
            and (self.geometry is None or layer.transform == self.transform)
        )

    def _canvas_rect(self, tiles: dict[tuple[int, int], FilterPenTile]) -> QRect:
        rect = QRect()
        for tile in tiles.values():
            rect = rect.united(tile.rect)
        return self.geometry.document_rect(rect) if self.geometry else rect.translated(*self.origin)

    def _write(
        self, canvas: Any, layer: Any, tiles: dict[tuple[int, int], FilterPenTile], *, before: bool
    ) -> None:
        painter = QPainter(layer.image)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        try:
            for tile in tiles.values():
                painter.drawImage(
                    tile.rect.x(), tile.rect.y(), tile.before if before else tile.after
                )
        finally:
            painter.end()
        layer.texture_version += 1
        self._expected_version = layer.texture_version
        rect = self._canvas_rect(tiles)
        self._dirty_region = DirtyRegion(rect.x(), rect.y(), rect.width(), rect.height())
        canvas._recompose_region(rect)
        canvas._emit_layer_content_changed(layer, rect, {"command": "effect_pen"})


def submit_filter_pen_command(canvas: Any, command: FilterPenStrokeCommand) -> bool:
    guard = CanvasCommandCommitGuard(canvas)
    try:
        if not command.apply(canvas):
            return False
        canvas._record_history_command("Filter Pen Stroke", command)
    except Exception:
        if command._executed:
            guard.rollback(command)
        raise
    return True


# Compatibility aliases for existing imports.
EffectPenTile = FilterPenTile
EffectPenStrokeCommand = FilterPenStrokeCommand
submit_effect_pen_command = submit_filter_pen_command


_bind_legacy_names(
    __name__,
    {
        "EffectPenSnapshot": "FilterPenSnapshot",
        "EffectPenTile": "FilterPenTile",
        "EffectPenStrokeCommand": "FilterPenStrokeCommand",
        "submit_effect_pen_command": "submit_filter_pen_command",
    },
)
