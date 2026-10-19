"""Tiled CMYK compositing on wgpu with independent alpha and bounded buffers."""

from __future__ import annotations

import struct
from collections.abc import Iterable
from copy import copy
from typing import Any

import numpy as np
from PySide6.QtCore import QRect

from loomstroke.core.appearance.live_styles import layer_styles
from loomstroke.core.mask_coverage import mask_coverage_alpha8
from loomstroke.core.native_cmyk.affine import placement, sample
from loomstroke.core.native_cmyk.pixels import NativeCmykPixels

from .device_manager import WgpuDeviceManager
from .native_cmyk_batch import NativeCmykBatch
from .native_cmyk_shader import NativeCmykShader
from .style_backdrop import needs_backdrop


class NativeCmykCompositor:
    TILE = 256
    MAX_DEPTH = 16
    _rect: QRect
    _count: int
    _allocated_bytes: int
    _encoder: Any

    def __init__(self, device_manager=None):
        self.manager = device_manager or WgpuDeviceManager()
        self._owns_manager = device_manager is None
        self.bundle = self.manager.get_bundle()
        shader = self.bundle.device.create_shader_module(code=NativeCmykShader.code())
        self.pipeline = self.bundle.device.create_compute_pipeline(
            layout="auto", compute={"module": shader, "entry_point": "main"}
        )
        self._bind_layout = self.pipeline.get_bind_group_layout(0)
        self._batch = NativeCmykBatch(self.bundle)
        self._buffers = []
        self._encoder = None
        self._style_processor = None
        self.capture_id: str | None = None
        self._captured = None

    def tile(self, scene, rect: QRect) -> NativeCmykPixels:
        return self.tiles(scene, (rect,))[0]

    def tiles(self, scene: Any, rects: Iterable[QRect]) -> list[NativeCmykPixels]:
        return self._batch.render(self, scene, rects)

    def _stack(self, nodes, destination, depth, inherited_opacity=1.0):
        if depth > self.MAX_DEPTH:
            raise ValueError("CMYK group depth exceeds the GPU limit")
        i = 0
        while i < len(nodes):
            node = nodes[i]
            i += 1
            clipped = []
            while i < len(nodes) and nodes[i].clipping:
                clipped.append(nodes[i])
                i += 1
            if not node.visible or node.layer_type == "mask" or node.clipping:
                continue
            mode = getattr(node.blend_mode, "value", node.blend_mode)
            if mode not in NativeCmykShader.MODES:
                raise ValueError(f"CMYK does not support blend mode {mode}")
            if (
                node.layer_type == "group"
                and mode == "pass_through"
                and (node.layer_mask is None or not node.mask_enabled)
                and not clipped
                and not layer_styles(node).enabled()
            ):
                destination = self._stack(
                    node.children, destination, depth + 1, inherited_opacity * node.opacity
                )
                continue
            source = self._source(node, depth, destination)
            if source is None:
                continue
            for clip in clipped:
                if clip.visible:
                    background = source
                    if needs_backdrop(clip):
                        carrier = copy(node)
                        carrier.blend_mode = "normal"
                        carrier.layer_mask = None
                        background = self._blend(source, destination, carrier, 1.0)
                    clip_source = self._source(clip, depth, background)
                    if clip_source is not None:
                        source = self._blend(clip_source, source, clip, clip.opacity, atop=True)
            destination = self._blend(source, destination, node, node.opacity * inherited_opacity)
        return destination

    def _source(self, node: Any, depth: int, backdrop: Any = None) -> Any:
        if layer_styles(node).enabled():
            from .native_layer_style_processor import (  # noqa: PLC0415
                NativeLayerStyleProcessor,
            )

            if self._style_processor is None:
                self._style_processor = NativeLayerStyleProcessor(self.bundle)
            source = self._style_processor.render(self, node, depth, backdrop)
        else:
            source = self._unstyled_source(node, depth)
        if source is not None and node.id == self.capture_id:
            isolated = copy(node)
            isolated.blend_mode = "normal"
            zero = self._buffer(np.zeros((self._count, 5), np.float32))
            captured = self._blend(source, zero, isolated, node.opacity)
            self._captured = (captured, QRect(self._rect))
        return source

    def _unstyled_source(self, node: Any, depth: int) -> Any:
        if node.layer_type == "group":
            child_depth = (
                depth if getattr(node, "vector_kind", "") == "native-cmyk-paints" else depth + 1
            )
            return self._stack(
                node.children, self._buffer(np.zeros((self._count, 5), np.float32)), child_depth
            )
        native = node.native_cmyk
        if native is None:
            raise ValueError("A CMYK composite contains a non-native colour layer")
        affine = placement(node)
        if (affine.a, affine.b, affine.c, affine.d) != (1, 0, 0, 1) or (
            affine.tx != round(affine.tx) or affine.ty != round(affine.ty)
        ):
            pixels = sample(native, self._rect, affine)
            if (
                not np.any(pixels.alpha)
                and getattr(node.blend_mode, "value", node.blend_mode) == "normal"
            ):
                return None
            maximum = np.iinfo(native.inks.dtype).max
            return self._buffer(
                np.concatenate(
                    (pixels.inks / maximum, pixels.alpha[:, :, None] / maximum), axis=2
                ).astype(np.float32)
            )
        packed = None
        offset_x, offset_y = round(affine.tx), round(affine.ty)
        bounds = QRect(offset_x, offset_y, native.inks.shape[1], native.inks.shape[0])
        intersection = self._rect.intersected(bounds)
        if not intersection.isEmpty():
            sx, sy = intersection.x() - offset_x, intersection.y() - offset_y
            dx, dy = intersection.x() - self._rect.x(), intersection.y() - self._rect.y()
            width, height = intersection.width(), intersection.height()
            maximum = np.iinfo(native.inks.dtype).max
            if hasattr(native, "region"):
                pixels = native.region((sx, sy, width, height))
                inks, alpha = pixels.inks, pixels.alpha
            else:
                inks = native.inks[sy : sy + height, sx : sx + width]
                alpha = native.alpha[sy : sy + height, sx : sx + width]
            mode = getattr(node.blend_mode, "value", node.blend_mode)
            if mode == "normal" and not np.any(alpha):
                return None
            packed = np.zeros((self._rect.height(), self._rect.width(), 5), np.float32)
            packed[dy : dy + height, dx : dx + width, :4] = inks / maximum
            packed[dy : dy + height, dx : dx + width, 4] = alpha / maximum
        if packed is None:
            if getattr(node.blend_mode, "value", node.blend_mode) == "normal":
                return None
            packed = np.zeros((self._rect.height(), self._rect.width(), 5), np.float32)
        return self._buffer(packed)

    def _blend(self, source, destination, node, opacity, *, atop=False):
        mode = getattr(node.blend_mode, "value", node.blend_mode)
        if mode not in NativeCmykShader.MODES:
            raise ValueError(f"CMYK does not support blend mode {mode}")
        mask = np.ones((self._rect.height(), self._rect.width()), np.float32)
        if node.layer_mask is not None and node.mask_enabled:
            if isinstance(node.layer_mask, np.ndarray):
                x, y = self._rect.x(), self._rect.y()
                coverage = node.layer_mask[y : y + self._rect.height(), x : x + self._rect.width()]
                mask = np.zeros_like(mask)
                mask[: coverage.shape[0], : coverage.shape[1]] = coverage.astype(np.float32) / 255
            else:
                image = mask_coverage_alpha8(node.layer_mask.copy(self._rect))
                rows = np.frombuffer(image.constBits(), np.uint8).reshape(
                    image.height(), image.bytesPerLine()
                )
                mask = rows[:, : image.width()].astype(np.float32) / 255.0
        mask_buffer = self._buffer(mask)
        output = self._buffer(np.zeros((self._count, 5), np.float32))
        parameters = struct.pack(
            "IIfIIIII",
            self._count,
            NativeCmykShader.MODES[mode],
            opacity,
            int(atop),
            self._rect.width(),
            self._rect.x(),
            self._rect.y(),
            0,
        )
        uniform = self._batch.upload(
            np.frombuffer(parameters, np.uint8), self.bundle.wgpu.BufferUsage.UNIFORM
        )
        self._buffers.append(uniform)
        bindings = [source, destination, output, mask_buffer, uniform]
        group = self.bundle.device.create_bind_group(
            layout=self._bind_layout,
            entries=[
                {"binding": i, "resource": {"buffer": buffer}} for i, buffer in enumerate(bindings)
            ],
        )
        compute = self._encoder.begin_compute_pass()
        compute.set_pipeline(self.pipeline)
        compute.set_bind_group(0, group)
        compute.dispatch_workgroups((self._count + 63) // 64)
        compute.end()
        return output

    def _buffer(self, data):
        self._allocated_bytes += data.nbytes
        if self._allocated_bytes > 128 * 1024 * 1024:
            raise ValueError("CMYK tile exceeds the GPU working-memory budget")
        flags = self.bundle.wgpu.BufferUsage
        buffer = self._batch.upload(data, flags.STORAGE | flags.COPY_SRC)
        self._buffers.append(buffer)
        return buffer

    def release(self):
        self._batch.release()
        self._style_processor = None
        self._bind_layout = None
        self.pipeline = None
        if self._owns_manager:
            self.manager.release()
