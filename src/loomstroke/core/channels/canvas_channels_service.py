"""Canvas channel targets and command-backed component/alpha operations."""

from __future__ import annotations

from typing import Any

import numpy as np
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage, QPainter

from loomstroke.core.commands import (
    ApplySelectionMaskCommand,
    DeleteLayerCommand,
    DuplicateLayerCommand,
)
from loomstroke.core.commands_layer_image_replace import ReplaceLayerImageCommand
from loomstroke.core.commands_layer_rename import RenameLayerCommand
from loomstroke.core.mask_coverage import mask_coverage_alpha8
from loomstroke.core.native_cmyk.channel_image import NativeCmykChannelImage

from .channel_pixel_policy import ChannelPixelPolicy
from .create_alpha_channel_command import CreateAlphaChannelCommand

ALPHA_METADATA_KEY = "loomstroke.channels.alpha"


class CanvasChannelsService:
    """Use existing hidden mask layers for lossless, backward-compatible alpha storage."""

    def __init__(self, canvas: Any) -> None:
        self._canvas = canvas
        self._selected = (0, 1, 2)
        self._layer_id: str | None = None
        self._color_layer_id: str | None = None
        self._view: tuple[str, str] | None = None

    @property
    def view(self) -> tuple[str, str] | None:
        if self._view is None:
            return None
        layer = self._canvas._get_active_layer()
        return self._view if layer is not None and layer.id == self._view[0] else None

    def set_view(self, channel: str | None) -> None:
        layer = self._canvas._get_active_layer()
        if channel is not None and channel not in (*self.color_keys, "transparency"):
            raise ValueError("Unknown channel")
        self._view = (layer.id, channel) if channel is not None and layer is not None else None
        self._canvas._recompose()

    @staticmethod
    def is_alpha(layer: Any) -> bool:
        return getattr(layer, "layer_type", None) == "mask" and (
            getattr(layer, "metadata", None) or {}
        ).get(ALPHA_METADATA_KEY) == {"version": 1}

    def reset(self) -> None:
        """A loaded document or page starts with the composite RGB edit target."""
        self._selected = (0, 1, 2)
        self._layer_id = None
        self._color_layer_id = None
        self._view = None
        native = getattr(self._canvas, "_native_cmyk_service", None)
        if native is not None:
            native.selected = (0, 1, 2, 3)
            native._foreground = None

    @property
    def color_keys(self) -> tuple[str, ...]:
        layers = self._canvas._layer_tree().flatten_layers()
        return (
            ("c", "m", "y", "k")
            if any(getattr(layer, "native_cmyk", None) is not None for layer in layers)
            else ("r", "g", "b")
        )

    @property
    def all_colors(self) -> tuple[int, ...]:
        return tuple(range(len(self.color_keys)))

    def alpha_layers(self) -> tuple[Any, ...]:
        return tuple(
            layer for layer in self._canvas._layer_tree().flatten_layers() if self.is_alpha(layer)
        )

    @property
    def selected(self) -> tuple[int, ...]:
        layer = self._canvas._get_active_layer()
        if layer is None or layer.id != self._layer_id:
            self._selected = (
                self.all_colors if getattr(layer, "native_cmyk", None) is not None else (0, 1, 2)
            )
            self._layer_id = getattr(layer, "id", None)
            if getattr(layer, "native_cmyk", None) is not None:
                self._canvas.native_cmyk().selected = self._selected
        return self._selected

    def select_color(self, channels: tuple[int, ...]) -> None:
        if self._canvas._in_transaction:
            raise ValueError("Finish the current stroke before changing channels.")
        if not channels or any(c not in self.all_colors for c in channels):
            raise ValueError("Select at least one RGB component.")
        layer = self._canvas._get_active_layer()
        if layer is not None and self.is_alpha(layer):
            target = (
                self._canvas._get_layer_by_id(self._color_layer_id)
                if self._color_layer_id
                else None
            )
            if target is None:
                target = next(
                    (
                        candidate
                        for candidate in reversed(self._canvas._layer_tree().flatten_layers())
                        if candidate.layer_type == "raster"
                    ),
                    None,
                )
            if target is not None:
                self._canvas.set_active_layer(target.id)
                self._color_layer_id = target.id
            layer = self._canvas._get_active_layer()
        self._selected = tuple(sorted(set(channels)))
        self._layer_id = getattr(layer, "id", None)
        if getattr(layer, "native_cmyk", None) is not None:
            self._canvas.native_cmyk().selected = self._selected
        self.set_view(self.color_keys[self._selected[0]] if len(self._selected) == 1 else None)

    def select_alpha(self, layer_id: str) -> None:
        if self._canvas._in_transaction:
            raise ValueError("Finish the current stroke before changing channels.")
        layer = self._canvas._get_layer_by_id(layer_id)
        if layer is None or not self.is_alpha(layer):
            raise ValueError("The alpha channel no longer exists.")
        current = self._canvas._get_active_layer()
        if current is not None and not self.is_alpha(current):
            self._color_layer_id = current.id
        self._canvas.set_active_layer(layer_id)
        self._selected = (0, 1, 2)
        self._layer_id = layer_id
        self.set_view("transparency")

    def enabled(self, layer: Any) -> bool:
        if getattr(layer, "native_cmyk", None) is not None:
            return False
        return not getattr(self._canvas, "_mask_edit_flag", False) and (
            self.is_alpha(layer) or (layer.id == self._layer_id and self.selected != (0, 1, 2))
        )

    def capture(self, layer: Any, rect: QRect) -> QImage | None:
        if layer.image is None or not self.enabled(layer):
            return None
        return layer.image.copy(rect)

    def finish(
        self, layer: Any, rect: QRect, before: QImage | None, *, erase: bool = False
    ) -> None:
        if before is None or layer.image is None or not self.enabled(layer):
            return
        after = layer.image.copy(rect)
        if self.is_alpha(layer):
            coverage = mask_coverage_alpha8(after)
            rows = np.frombuffer(coverage.constBits(), np.uint8).reshape(
                coverage.height(), coverage.bytesPerLine()
            )
            patch = self.alpha_image(rows[:, : coverage.width()])
        else:
            patch = ChannelPixelPolicy.merge(before, after, self.selected, erase=erase)
        painter = QPainter(layer.image)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.drawImage(rect.topLeft(), patch)
        painter.end()

    @staticmethod
    def alpha_image(coverage: np.ndarray) -> QImage:
        height, width = coverage.shape
        pixels = np.full((height, width, 4), 255, np.uint8)
        pixels[:, :, 3] = coverage
        return ChannelPixelPolicy.image(pixels, straight=True).convertToFormat(
            QImage.Format.Format_ARGB32_Premultiplied
        )

    def channel_image(self, channel: str, *, max_extent: int | None = None) -> QImage:
        layer = self._canvas._get_layer_by_id(channel)
        if layer is not None and self.is_alpha(layer) and layer.image is not None:
            index = 3
        else:
            layer = self._canvas._get_active_layer()
            if layer is None or layer.image is None:
                return QImage()
            native = getattr(layer, "native_cmyk", None)
            if native is not None:
                if channel == "cmyk":
                    image = layer.image
                    return (
                        image.scaled(max_extent, max_extent, Qt.AspectRatioMode.KeepAspectRatio)
                        if max_extent
                        else image.copy()
                    )
                if channel in (*self.color_keys, "transparency"):
                    return NativeCmykChannelImage.image(native, channel, max_extent)
            index = {"r": 0, "g": 1, "b": 2, "transparency": 3}.get(channel)
            if index is None and channel != "rgb":
                raise ValueError("Unknown channel")
        image = layer.image
        if max_extent is not None:
            image = image.scaled(
                max_extent,
                max_extent,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        if channel == "rgb":
            return image.copy()
        values = ChannelPixelPolicy.pixels(image, straight=True)[:, :, index]
        rgba = np.full((*values.shape, 4), 255, np.uint8)
        rgba[:, :, :3] = values[:, :, None]
        return ChannelPixelPolicy.image(rgba, straight=True)

    def create_alpha(self, name: str, *, from_selection: bool = False) -> str:
        width, height = self._canvas._width, self._canvas._height
        coverage = np.zeros((height, width), np.uint8)
        if from_selection:
            selection = self._canvas._selection_state().selection_clip_state()[0]
            if selection is None:
                raise ValueError("There is no selection to save.")
            selection = self._canvas._selection_state().selection_snapshot_image(selection)
            rows = np.frombuffer(selection.constBits(), np.uint8).reshape(
                selection.height(), selection.bytesPerLine()
            )
            coverage[:] = rows[:, :width]
        command = CreateAlphaChannelCommand(name, self.alpha_image(coverage))
        if not self._canvas._simple_layer_command_submit().submit(command, "Create Alpha Channel"):
            raise ValueError("The alpha channel could not be created.")
        assert command.layer_id is not None
        return command.layer_id

    def rename_alpha(self, layer_id: str, name: str) -> bool:
        self._require_alpha(layer_id)
        return self._canvas._simple_layer_command_submit().submit(
            RenameLayerCommand(layer_id, name), "Rename Alpha Channel"
        )

    def delete_alpha(self, layer_id: str) -> bool:
        self._require_alpha(layer_id)
        return self._canvas.submit(DeleteLayerCommand(layer_id))

    def duplicate_alpha(self, layer_id: str) -> bool:
        self._require_alpha(layer_id)
        return self._canvas.submit(DuplicateLayerCommand(layer_id))

    def load_selection(self, layer_id: str, operation: str = "replace") -> bool:
        layer = self._require_alpha(layer_id)
        coverage = np.ascontiguousarray(
            ChannelPixelPolicy.pixels(layer.image, straight=True)[:, :, 3]
        )
        height, width = coverage.shape
        mask = QImage(coverage.data, width, height, width, QImage.Format.Format_Alpha8).copy()
        return self._canvas.submit(
            ApplySelectionMaskCommand(mask, operation, bounds=(0, 0, width, height))
        )

    def invert(self) -> bool:
        layer = self._canvas._get_active_layer()
        if getattr(layer, "native_cmyk", None) is not None:
            return self._canvas.native_cmyk().invert()
        if (
            layer is None
            or layer.image is None
            or layer.locked
            or layer.layer_type not in ("raster", "mask")
        ):
            return False
        before = layer.image.copy()
        pixels = ChannelPixelPolicy.pixels(before, straight=True)
        if self.is_alpha(layer):
            after = self.alpha_image(255 - pixels[:, :, 3])
        else:
            pixels[:, :, :3] = 255 - pixels[:, :, :3]
            candidate = ChannelPixelPolicy.image(pixels, straight=True)
            after = ChannelPixelPolicy.merge(before, candidate, self.selected)
        return self._canvas.submit(ReplaceLayerImageCommand(layer.id, after))

    def _require_alpha(self, layer_id: str) -> Any:
        layer = self._canvas._get_layer_by_id(layer_id)
        if layer is None or not self.is_alpha(layer) or layer.image is None:
            raise ValueError("The alpha channel no longer exists.")
        return layer
