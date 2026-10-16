"""Tiled CMYK compositing on wgpu with independent alpha and bounded buffers."""

from __future__ import annotations

import struct

import numpy as np
from PySide6.QtCore import QRect

from loomstroke.core.mask_coverage import mask_coverage_alpha8
from loomstroke.core.native_cmyk.pixels import NativeCmykPixels

from .device_manager import WgpuDeviceManager
from .native_cmyk_shader import NativeCmykShader


class NativeCmykCompositor:
    TILE = 256
    MAX_DEPTH = 16

    def __init__(self, device_manager=None):
        self.manager = device_manager or WgpuDeviceManager()
        self._owns_manager = device_manager is None
        self.bundle = self.manager.get_bundle()
        shader = self.bundle.device.create_shader_module(code=NativeCmykShader.code())
        self.pipeline = self.bundle.device.create_compute_pipeline(
            layout="auto", compute={"module": shader, "entry_point": "main"}
        )

    def tile(self, scene, rect: QRect) -> NativeCmykPixels:
        rect = rect.intersected(QRect(0, 0, scene.width, scene.height))
        if rect.isEmpty() or rect.width() > self.TILE or rect.height() > self.TILE:
            raise ValueError("Native composite requires a bounded canvas tile")
        native = next(
            (layer.native_cmyk for layer in scene.flat_layers if layer.native_cmyk is not None),
            None,
        )
        if native is None:
            raise ValueError("No native CMYK layers")
        dtype = native.inks.dtype
        self._buffers = []
        self._allocated_bytes = 0
        self._rect = rect
        self._count = rect.width() * rect.height()
        self._encoder = self.bundle.device.create_command_encoder()
        try:
            zero = self._buffer(np.zeros((self._count, 5), np.float32))
            result = self._stack(scene.layers, zero, 0)
            self.bundle.queue.submit([self._encoder.finish()])
            data = np.frombuffer(self.bundle.queue.read_buffer(result), np.float32).reshape(
                rect.height(), rect.width(), 5
            )
            maximum = np.iinfo(dtype).max
            values = np.rint(np.clip(data, 0, 1) * maximum).astype(dtype)
            return NativeCmykPixels(values[:, :, :4], values[:, :, 4], native.profile)
        finally:
            for buffer in self._buffers:
                buffer.destroy()
            self._buffers = []
            self._encoder = None

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
            ):
                destination = self._stack(
                    node.children, destination, depth + 1, inherited_opacity * node.opacity
                )
                continue
            source = self._source(node, depth)
            if source is None:
                continue
            for clip in clipped:
                if clip.visible:
                    clip_source = self._source(clip, depth)
                    if clip_source is not None:
                        source = self._blend(clip_source, source, clip, clip.opacity, atop=True)
            destination = self._blend(source, destination, node, node.opacity * inherited_opacity)
        return destination

    def _source(self, node, depth):
        if node.layer_type == "group":
            return self._stack(
                node.children, self._buffer(np.zeros((self._count, 5), np.float32)), depth + 1
            )
        native = node.native_cmyk
        if native is None:
            raise ValueError("A CMYK composite contains a non-native colour layer")
        transform = node.transform
        if transform is not None and (
            transform.scale_x != 1
            or transform.scale_y != 1
            or transform.rotation != 0
            or transform.tx != 0
            or transform.ty != 0
        ):
            raise ValueError("CMYK transforms must be rasterized")
        packed = np.zeros((self._rect.height(), self._rect.width(), 5), np.float32)
        bounds = QRect(node.offset_x, node.offset_y, native.inks.shape[1], native.inks.shape[0])
        intersection = self._rect.intersected(bounds)
        if not intersection.isEmpty():
            sx, sy = intersection.x() - node.offset_x, intersection.y() - node.offset_y
            dx, dy = intersection.x() - self._rect.x(), intersection.y() - self._rect.y()
            width, height = intersection.width(), intersection.height()
            maximum = np.iinfo(native.inks.dtype).max
            if hasattr(native, "region"):
                pixels = native.region((sx, sy, width, height))
                inks, alpha = pixels.inks, pixels.alpha
            else:
                inks = native.inks[sy : sy + height, sx : sx + width]
                alpha = native.alpha[sy : sy + height, sx : sx + width]
            packed[dy : dy + height, dx : dx + width, :4] = inks / maximum
            packed[dy : dy + height, dx : dx + width, 4] = alpha / maximum
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
        uniform = self.bundle.device.create_buffer_with_data(
            data=parameters, usage=self.bundle.wgpu.BufferUsage.UNIFORM
        )
        self._buffers.append(uniform)
        bindings = [source, destination, output, mask_buffer, uniform]
        group = self.bundle.device.create_bind_group(
            layout=self.pipeline.get_bind_group_layout(0),
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
        buffer = self.bundle.device.create_buffer_with_data(
            data=np.ascontiguousarray(data), usage=flags.STORAGE | flags.COPY_SRC
        )
        self._buffers.append(buffer)
        return buffer

    def release(self):
        self.pipeline = None
        if self._owns_manager:
            self.manager.release()
