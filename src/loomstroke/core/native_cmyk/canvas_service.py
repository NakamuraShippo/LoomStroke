"""Native edit targets and command-backed tile commits, separate from UI."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QRect

from loomstroke.core.fill.flood_fill import _apply_gap_close, _bounds_from_spans, _scanline_fill

from .buffer import NativeCmykBuffer
from .color_transform import CmykColorTransform
from .convert_command import ConvertNativeCmykCommand
from .patch_command import NativeCmykPatchCommand
from .pixel_operations import NativeCmykPixelOperations
from .pixels import NativeCmykPixels
from .stroke_coverage import NativeCmykStrokeCoverage


class CanvasNativeCmykService:
    def __init__(self, canvas) -> None:
        self.canvas = canvas
        self._foreground: tuple[int, int, int, int] | None = None
        self.selected = (0, 1, 2, 3)

    def profile(self) -> bytes | None:
        for layer in self.canvas._layer_tree().flatten_layers():
            if layer.native_cmyk is not None:
                return layer.native_cmyk.profile
        return None

    def convert(self, profile: bytes, *, source_profile: bytes | None = None) -> bool:
        filter_pen = getattr(self.canvas, "_filter_pen_service", None)
        if filter_pen is not None:
            filter_pen.finalize()
        return self.canvas._simple_layer_command_submit().submit(
            ConvertNativeCmykCommand(profile, source_profile=source_profile), "Convert to CMYK"
        )

    @property
    def foreground_percentages(self):
        return (
            tuple(value * 100 / 65535 for value in self._foreground)
            if self._foreground is not None
            else None
        )

    def set_foreground(self, percentages: tuple[float, float, float, float] | None) -> None:
        if percentages is None:
            self._foreground = None
            return
        values = np.asarray(percentages, dtype=float)
        if (
            values.shape != (4,)
            or not np.isfinite(values).all()
            or np.any((values < 0) | (values > 100))
        ):
            raise ValueError("CMYK percentages must be between 0 and 100")
        self._foreground = tuple(int(value) for value in np.rint(values * 65535 / 100))

    def convert_document(self, pages, profile, *, bits=8, source_profile=None):
        from .document_convert_command import DocumentCmykConvertCommand  # noqa: PLC0415

        command = DocumentCmykConvertCommand(profile, bits=bits, source_profile=source_profile)
        return pages._book_history.execute(command)

    def clear(self, layer_id=None):
        layer = self._target(layer_id)
        if layer is None:
            return False
        if layer.alpha_lock:
            return False
        height, width = layer.native_cmyk.alpha.shape
        before = layer.native_cmyk.region((0, 0, width, height))
        after = NativeCmykPixels(before.inks, np.zeros_like(before.alpha), before.profile)
        return self._submit(layer, (0, 0, width, height), before, after, "Clear CMYK Layer")

    def color(self, rgb, layer, *, use_foreground=True) -> tuple[int, int, int, int]:
        if use_foreground and self._foreground is not None:
            maximum = np.iinfo(layer.native_cmyk.inks.dtype).max
            return tuple((v * maximum + 32767) // 65535 for v in self._foreground)
        values = np.zeros((1, 1, 4), np.uint8)
        values[0, 0] = (*np.rint(tuple(rgb)[:3]).astype(np.uint8), 255)
        inks, _ = CmykColorTransform.from_rgb(values, layer.native_cmyk.profile)
        scale = 257 if layer.native_cmyk.inks.dtype == np.uint16 else 1
        return tuple(int(v) * scale for v in inks[0, 0])

    def initialize_layer(self, layer) -> None:
        profile = self.profile()
        if profile is None or layer.layer_type != "raster" or layer.native_cmyk is not None:
            return
        height, width = layer.image.height(), layer.image.width()
        dtype = next(
            candidate.native_cmyk.inks.dtype
            for candidate in self.canvas._layer_tree().flatten_layers()
            if candidate.native_cmyk is not None
        )
        layer.native_cmyk = NativeCmykBuffer(
            NativeCmykPixels(
                np.zeros((height, width, 4), dtype), np.zeros((height, width), dtype), profile
            )
        )

    def paint(self, command, *, erase=False) -> bool:
        layer = self._target(getattr(command, "layer_id", None))
        if layer is None:
            return False
        if getattr(command.brush.blend_mode, "value", command.brush.blend_mode) != "normal":
            return False
        if getattr(command.brush, "color_mixing", 0.0) != 0:
            return False
        rendered = NativeCmykStrokeCoverage.render(self.canvas, command, layer, erase=erase)
        if rendered is None:
            return False
        bounds, coverage, strength = rendered
        base = np.asarray(self.color(command.brush.color, layer), dtype=np.uint32)
        color = ((base * strength[:, :, None] + 127) // 255).astype(layer.native_cmyk.inks.dtype)
        return self._commit(layer, bounds, color, coverage, erase=erase, label="CMYK Stroke")

    def fill(self, command) -> bool:
        layer = self._target()
        if (
            layer is None
            or command.reference_layer_id not in (None, layer.id)
            or command.connectivity != 8
        ):
            return False
        native = layer.native_cmyk
        x, y = command.seed_x - layer.offset_x, command.seed_y - layer.offset_y
        height, width = native.alpha.shape
        if not (0 <= x < width and 0 <= y < height):
            return False
        scale = 257 if native.inks.dtype == np.uint16 else 1
        tolerance = command.color_tolerance * scale
        difference = np.max(
            np.abs(native.inks.astype(np.int32) - native.inks[y, x].astype(np.int32)), axis=2
        )
        alpha_difference = np.abs(native.alpha.astype(np.int32) - int(native.alpha[y, x]))
        similar = (difference <= tolerance) & (alpha_difference <= tolerance)
        if native.alpha[y, x] == 0:
            similar = alpha_difference <= tolerance
        if command.gap_close_px:
            similar = _apply_gap_close(similar, command.gap_close_px)
        spans = _scanline_fill(similar, x, y)
        rect = _bounds_from_spans(spans)
        if rect is None:
            return False
        rx, ry, rw, rh = rect
        coverage = np.zeros((rh, rw), np.uint8)
        for sy, sx0, sx1 in spans:
            coverage[sy - ry, sx0 - rx : sx1 - rx + 1] = command.fill_rgba[3]
        bounds = QRect(rx + layer.offset_x, ry + layer.offset_y, rw, rh)
        coverage = self._selection_coverage(bounds, coverage)
        return self._commit(
            layer, bounds, self.color(command.fill_rgba, layer), coverage, label="CMYK Fill"
        )

    def gradient(self, command) -> bool:
        layer = self._target(command._layer_id)
        if layer is None:
            return False
        height, width = layer.native_cmyk.alpha.shape
        yy, xx = np.mgrid[:height, :width]
        dx, dy = command._x1 - command._x0, command._y1 - command._y0
        px, py = xx + layer.offset_x - command._x0, yy + layer.offset_y - command._y0
        if command._gradient_type == "radial":
            amount = np.hypot(px, py) / max(np.hypot(dx, dy), 1e-10)
        elif command._gradient_type == "linear":
            amount = (px * dx + py * dy) / max(dx * dx + dy * dy, 1e-10)
        else:
            return False
        amount = np.clip(amount, 0, 1)
        if command._dither:
            amount = np.clip(
                amount + np.random.default_rng(42).uniform(-0.5, 0.5, amount.shape) / 255.0, 0, 1
            )
        stops = [(0.0, command._color_a), *(command._stops or []), (1.0, command._color_b)]
        stops.sort(key=lambda stop: stop[0])
        positions = np.asarray([s[0] for s in stops])
        samples = np.asarray(
            [
                (*self.color(rgba, layer, use_foreground=index == 0), rgba[3])
                for index, (_position, rgba) in enumerate(stops)
            ]
        )
        if np.any(np.diff(positions) <= 0) or positions[0] != 0 or positions[-1] != 1:
            raise ValueError("CMYK gradient stops must have distinct positions between 0 and 1")
        color = np.stack([np.interp(amount, positions, samples[:, c]) for c in range(4)], axis=2)
        color = np.rint(color).astype(layer.native_cmyk.inks.dtype)
        coverage = np.rint(np.interp(amount, positions, samples[:, 4])).astype(np.uint8)
        bounds = QRect(layer.offset_x, layer.offset_y, width, height)
        return self._commit(
            layer, bounds, color, self._selection_coverage(bounds, coverage), label="CMYK Gradient"
        )

    def invert(self) -> bool:
        layer = self._target()
        if layer is None:
            return False
        height, width = layer.native_cmyk.alpha.shape
        before = layer.native_cmyk.region((0, 0, width, height))
        inks = before.inks.copy()
        maximum = np.iinfo(inks.dtype).max
        coverage = self._selection_coverage(
            QRect(layer.offset_x, layer.offset_y, width, height),
            np.full((height, width), 255, np.uint8),
        ).astype(np.uint64)
        for channel in self.selected:
            original = before.inks[:, :, channel].astype(np.uint64)
            inks[:, :, channel] = (
                (original * (255 - coverage) + (maximum - original) * coverage + 127) // 255
            ).astype(inks.dtype)
        after = NativeCmykPixels(inks, before.alpha, before.profile)
        return self._submit(layer, (0, 0, width, height), before, after, "Invert CMYK")

    def _target(self, layer_id=None):
        layer = (
            self.canvas._get_layer_by_id(layer_id) if layer_id else self.canvas._get_active_layer()
        )
        if (
            layer is None
            or layer.native_cmyk is None
            or layer.locked
            or self.canvas._mask_edit_flag
        ):
            return None
        if (
            layer.transform.scale_x != 1
            or layer.transform.scale_y != 1
            or layer.transform.rotation != 0
            or layer.transform.tx != 0
            or layer.transform.ty != 0
        ):
            return None
        return layer

    def _selection_coverage(self, bounds, coverage):
        selection, _ = self.canvas._selection_clip_state()
        if selection is None:
            return coverage
        patch = selection.copy(bounds).convertToFormat(selection.Format.Format_Alpha8)
        rows = np.frombuffer(patch.constBits(), np.uint8).reshape(
            patch.height(), patch.bytesPerLine()
        )
        return ((coverage.astype(np.uint16) * rows[:, : bounds.width()] + 127) // 255).astype(
            np.uint8
        )

    def _commit(self, layer, bounds, color, coverage, *, erase=False, label):
        if not np.any(coverage):
            return False
        rect = (
            bounds.x() - layer.offset_x,
            bounds.y() - layer.offset_y,
            bounds.width(),
            bounds.height(),
        )
        before = layer.native_cmyk.region(rect)
        after = NativeCmykPixelOperations.paint(
            before, color, coverage, self.selected, erase=erase, alpha_lock=layer.alpha_lock
        )
        if np.array_equal(before.inks, after.inks) and np.array_equal(before.alpha, after.alpha):
            return False
        return self._submit(layer, rect, before, after, label)

    def _submit(self, layer, rect, before, after, label):
        command = NativeCmykPatchCommand(layer.id, rect, before, after)
        if not command.apply(self.canvas):
            return False
        self.canvas._record_history_command(label, command)
        return True
