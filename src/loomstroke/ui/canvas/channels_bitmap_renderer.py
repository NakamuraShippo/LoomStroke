"""GPU channel display for the bitmap presenter; document export stays separate."""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any

from .channels_surface_view import ChannelsSurfaceView
from .wgpu_backend.readback import WgpuReadback
from .wgpu_backend.types import WgpuRenderResult, WgpuRenderTargetHandle

_SHADER = """
struct VertexOut { @builtin(position) position: vec4<f32>, @location(0) uv: vec2<f32> };
@group(0) @binding(0) var tex_sampler: sampler;
@group(0) @binding(1) var canvas_tex: texture_2d<f32>;
@vertex fn vs_main(@builtin(vertex_index) index: u32) -> VertexOut {
    let positions = array<vec2<f32>, 6>(vec2(-1.0, 1.0), vec2(1.0, 1.0), vec2(-1.0, -1.0),
        vec2(-1.0, -1.0), vec2(1.0, 1.0), vec2(1.0, -1.0));
    let p = positions[index];
    var out: VertexOut;
    out.position = vec4(p, 0.0, 1.0);
    out.uv = vec2((p.x + 1.0) * 0.5, (1.0 - p.y) * 0.5);
    return out;
}
@fragment fn fs_main(in: VertexOut) -> @location(0) vec4<f32> {
    let src = textureSample(canvas_tex, tex_sampler, in.uv);
    let alpha = clamp(src.a, 0.0, 1.0);
    let checker = vec3<f32>(1.0);
    let rgb_srgb = proof_rgb(src.rgb + checker * (1.0 - alpha));
    return vec4(rgb_srgb, 1.0);
}
"""


class ChannelsBitmapRenderer:
    def __init__(self, renderer: Any) -> None:
        self.renderer = renderer
        self.bundle = renderer._bundle
        self._pipelines: dict[int, Any] = {}
        self._sampler = self.bundle.device.create_sampler(
            mag_filter="nearest", min_filter="nearest"
        )
        self._target: WgpuRenderTargetHandle | None = None

    def render(self, scene: Any, invalidation: Any) -> WgpuRenderResult:
        start = time.perf_counter()
        display, index = ChannelsSurfaceView.prepare(self, scene)
        if index < 0:
            return self.renderer.render_scene(scene, invalidation)
        bundle = self.bundle
        source, warnings, metrics = self.renderer._compositor.render(
            display, invalidation=invalidation
        )
        try:
            output = self._output(scene.width, scene.height)
            pipeline = self._pipeline(index)
            bind = bundle.device.create_bind_group(
                layout=pipeline.get_bind_group_layout(0),
                entries=[
                    {"binding": 0, "resource": self._sampler},
                    {"binding": 1, "resource": source.view},
                ],
            )
            encoder = bundle.device.create_command_encoder()
            render_pass = encoder.begin_render_pass(
                color_attachments=[
                    {
                        "view": output.view,
                        "resolve_target": None,
                        "clear_value": (0, 0, 0, 1),
                        "load_op": "clear",
                        "store_op": "store",
                    }
                ]
            )
            render_pass.set_pipeline(pipeline)
            render_pass.set_bind_group(0, bind)
            render_pass.draw(6)
            render_pass.end()
            bundle.queue.submit([encoder.finish()])
            render_ms = (time.perf_counter() - start) * 1000
            read_start = time.perf_counter()
            pixels = WgpuReadback(bundle).read_rgba(output)
            metrics = replace(
                metrics,
                render_frame_ms=render_ms,
                readback_ms=(time.perf_counter() - read_start) * 1000,
            )
            return WgpuRenderResult(
                width=scene.width,
                height=scene.height,
                rgba=pixels,
                backend_name="wgpu-channel-display",
                warnings=warnings,
                metrics=metrics,
            )
        finally:
            source.texture.destroy()

    def _pipeline(self, index: int) -> Any:
        if index not in self._pipelines:
            module = self.bundle.device.create_shader_module(
                code=ChannelsSurfaceView.shader(_SHADER, index)
            )
            self._pipelines[index] = self.bundle.device.create_render_pipeline(
                layout="auto",
                vertex={"module": module, "entry_point": "vs_main", "buffers": []},
                fragment={
                    "module": module,
                    "entry_point": "fs_main",
                    "targets": [{"format": "rgba8unorm"}],
                },
                primitive={"topology": "triangle-list"},
            )
        return self._pipelines[index]

    def _output(self, width: int, height: int) -> WgpuRenderTargetHandle:
        if self._target is None or (self._target.width, self._target.height) != (width, height):
            if self._target is not None:
                self._target.texture.destroy()
            wgpu = self.bundle.wgpu
            texture = self.bundle.device.create_texture(
                size=(width, height, 1),
                format="rgba8unorm",
                usage=wgpu.TextureUsage.RENDER_ATTACHMENT | wgpu.TextureUsage.COPY_SRC,
            )
            self._target = WgpuRenderTargetHandle(texture, texture.create_view(), width, height)
        return self._target

    def release(self) -> None:
        cache = getattr(self, "_native_channel_view_cache", None)
        if cache is not None:
            cache.release()
            self._native_channel_view_cache = None
        if self._target is not None:
            self._target.texture.destroy()
            self._target = None
        self._pipelines.clear()
