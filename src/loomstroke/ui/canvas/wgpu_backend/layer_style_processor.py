"""GPU layer styles with bounded, submit-committed per-layer caches."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Sequence
from typing import Any

import numpy as np
from PySide6.QtCore import QRect

from loomstroke.core.appearance import AppearanceRegistry
from loomstroke.core.appearance.live_styles import layer_styles, style_padding, style_signature
from loomstroke.core.appearance.relief_style_parameters import IDENTITY_PROFILE

from .layer_style_damage import copy_style_patch, style_work_rect
from .layer_style_evaluator import evaluate_styles
from .relief_style_uniforms import EMPTY_RELIEF
from .style_pipeline_store import StylePipelineStore, style_format
from .types import WgpuRenderTargetHandle


def finish_render_caches(compositor: Any, active_ids: set[str], *, submitted: bool) -> None:
    """Commit dependent GPU caches only after their shared encoder submits."""
    compositor._group_composite_cache.finish(submitted)
    compositor.layer_style_processor.finish_frame(active_ids, submitted=submitted)
    compositor.filter_processor.finish_frame(active_ids, submitted=submitted)


class WgpuLayerStyleProcessor:
    def __init__(self, bundle: Any, *, pipeline_store: Any = None) -> None:
        self.bundle = bundle
        self.pipeline_store = pipeline_store or StylePipelineStore(bundle)
        self.pipelines = self.pipeline_store.pipelines
        self.cache: OrderedDict[str, tuple[Any, WgpuRenderTargetHandle, int]] = OrderedDict()
        self.pending: dict[str, tuple[Any, WgpuRenderTargetHandle, int]] = {}
        self.retired: list[WgpuRenderTargetHandle] = []
        self.registry = AppearanceRegistry(include_adjustments=False)
        self.scratch: list[WgpuRenderTargetHandle] = []
        self.budget_bytes = 256 * 1024 * 1024
        self.cache_hits = 0
        self.capture_id: str | None = None
        self.captured: WgpuRenderTargetHandle | None = None

    def apply(
        self,
        encoder: Any,
        source: Any,
        layer: Any,
        *,
        scale: int = 1,
        premultiplied: bool = False,
        dirty: QRect | None = None,
        reference: tuple[int, int, int, int] | None = None,
        backdrop: Any = None,
    ) -> tuple[Any, int]:
        stack = layer_styles(layer)
        styles = [s for s in stack.render_order() if self.registry.get("style", s.kind)]
        if not styles:
            return source, 0
        precise_relief = any(
            s.kind in {"three_d", "bevel_emboss"}
            and s.params.get("profile", IDENTITY_PROFILE) != IDENTITY_PROFILE
            for s in styles
        )
        padding = style_padding(stack) * scale
        reference = reference or (0, 0, source.width, source.height)
        key = (
            source.texture,
            source.width,
            source.height,
            getattr(source, "texture_version", -1),
            getattr(layer, "texture_version", 0),
            style_signature(layer),
            scale,
            premultiplied,
            reference,
            backdrop.key if backdrop else None,
        )
        identifier = str(layer.id)
        entry = self.pending.get(identifier) or self.cache.get(identifier)
        if entry is not None and entry[0] == key:
            self.cache_hits += 1
            if identifier in self.cache:
                self.cache.move_to_end(identifier)
            self._capture(encoder, identifier, entry[1])
            return entry[1], entry[2]
        temporary: list[WgpuRenderTargetHandle] = []
        free: list[WgpuRenderTargetHandle] = []
        compatible = entry is not None and entry[0][:3] == key[:3] and entry[0][5:] == key[5:]
        work, damage = style_work_rect(
            source, padding, dirty if compatible and not backdrop else None
        )

        def render(
            src: Any,
            op: int,
            radius: int = 0,
            mode: int = 0,
            geometry: tuple[int, ...] = (0, 0, 0, 0),
            color: tuple[float, ...] = (0, 0, 0),
            opacity: float = 0.0,
            **kw: Any,
        ) -> WgpuRenderTargetHandle:
            format = style_format(op, precise_relief)
            document = backdrop is not None and op in {4, 9, 11, 12, 13, 14}
            width, height = (
                (backdrop.texture.width, backdrop.texture.height)
                if document
                else (work.width(), work.height())
            )
            target = next(
                (t for t in free if (t.format, t.width, t.height) == (format, width, height)), None
            )
            if target is not None:
                free.remove(target)
            else:
                target = self._target(width, height, format)
                temporary.append(target)
            self._pass(
                encoder,
                target,
                src,
                op,
                radius,
                mode,
                geometry,
                (*color, opacity),
                premultiplied=premultiplied,
                backdrop=backdrop.texture if document else None,
                underlay=backdrop.underlay if document else None,
                mapping=(
                    (
                        *backdrop.mapping[:4],
                        backdrop.mapping[4] - work.x() - 0.5,
                        backdrop.mapping[5] - work.y() - 0.5,
                    )
                    if document
                    else (1, 0, 0, 1, 0, 0)
                ),
                source_document=op == 4 and mode == 0,
                coordinates=(
                    work.x() + reference[0],
                    work.y() + reference[1],
                    reference[2],
                    reference[3],
                ),
                **kw,
            )
            return target

        try:
            base = render(source, 0, geometry=(-work.x(), -work.y(), 0, 0))
            alpha_base = render(base, 6)
            accumulator = render(base, 13 if backdrop else 0, mode=1)

            def dilate(src: Any, radius: int, *, erosion: bool = False) -> WgpuRenderTargetHandle:
                levels = [src]
                for level in range((2 * radius + 1).bit_length() - 1):
                    levels.append(render(levels[-1], 1, 1 << level, mode=int(erosion)))
                mask = render(base, 2, radius, mode=int(erosion), levels=levels)
                free.extend(levels[1:])
                return mask

            accumulator = evaluate_styles(
                stack,
                self.registry,
                base,
                alpha_base,
                accumulator,
                render=render,
                dilate=dilate,
                release=free.append,
                convert_color=lambda color: tuple(c / 255 for c in color[:3]),
                extract_alpha=lambda src: render(src, 6),
                scale=scale,
                project=(lambda src: render(src, 12)) if backdrop else None,
            )
            if damage is not None and entry is not None:
                target = self._target(
                    source.width + 2 * padding, source.height + 2 * padding, "rgba16float"
                )
                temporary.append(target)
                copy_style_patch(encoder, entry[1], target, accumulator, work, damage, padding)
                accumulator = target
        except Exception:
            for target in temporary:
                target.texture.destroy()
            raise
        return self._stage_result(
            encoder, identifier, key, accumulator, 0 if backdrop else padding, temporary
        )

    def _stage_result(
        self,
        encoder: Any,
        identifier: str,
        key: Any,
        accumulator: WgpuRenderTargetHandle,
        padding: int,
        temporary: list[WgpuRenderTargetHandle],
    ) -> tuple[Any, int]:
        previous = self.pending.get(identifier)
        if previous is not None:
            self.retired.append(previous[1])
        self.pending[identifier] = (key, accumulator, padding)
        self._capture(encoder, identifier, accumulator)
        self.retired.extend(target for target in temporary if target is not accumulator)
        return accumulator, padding

    def _capture(self, encoder: Any, identifier: str, source: WgpuRenderTargetHandle) -> None:
        if identifier != self.capture_id:
            return
        if self.captured is not None:
            self.captured.texture.destroy()
        self.captured = self._target(source.width, source.height, source.format)
        encoder.copy_texture_to_texture(
            {"texture": source.texture},
            {"texture": self.captured.texture},
            (source.width, source.height, 1),
        )

    def _target(self, width: int, height: int, format: str) -> WgpuRenderTargetHandle:
        for target in self.scratch:
            if (target.width, target.height, target.format) == (width, height, format):
                self.scratch.remove(target)
                return target
        wgpu = self.bundle.wgpu
        texture = self.bundle.device.create_texture(
            size=(width, height, 1),
            format=format,
            usage=wgpu.TextureUsage.RENDER_ATTACHMENT
            | wgpu.TextureUsage.TEXTURE_BINDING
            | wgpu.TextureUsage.COPY_SRC
            | wgpu.TextureUsage.COPY_DST,
        )
        return WgpuRenderTargetHandle(texture, texture.create_view(), width, height, format)

    def _pass(
        self,
        encoder: Any,
        target: WgpuRenderTargetHandle,
        source: Any,
        op: int,
        radius: int,
        mode: int,
        geometry: tuple[int, ...],
        color: tuple[float, ...],
        *,
        previous: Any = None,
        mask: Any = None,
        levels: Sequence[Any] = (),
        coverage: Any = None,
        style: tuple[float, ...] = (0, 0, 0, 0),
        secondary: tuple[float, ...] = (0, 0, 0, 0),
        lighting: tuple[float, ...] = (0, 0, 0, 0),
        material: tuple[float, ...] = (0, 0, 0, 0),
        coordinates: tuple[float, ...] = (0, 0, 1, 1),
        relief: tuple[float, ...] = EMPTY_RELIEF,
        blend: int = 0,
        premultiplied: bool = False,
        backdrop: Any = None,
        underlay: Any = None,
        mapping: tuple[float, ...] = (1, 0, 0, 1, 0, 0),
        source_document: bool = False,
    ) -> None:
        device = self.bundle.device
        pipeline = self.pipeline_store.compile((target.format, op, backdrop is not None))
        params = np.array(
            [
                op,
                radius,
                mode,
                int(premultiplied),
                *geometry,
                *color,
                *style,
                *secondary,
                *lighting,
                *material,
                *coordinates,
                blend,
                int(backdrop is not None),
                int(source_document),
                0,
                *relief,
                mapping[0],
                mapping[1],
                mapping[4],
                0,
                mapping[2],
                mapping[3],
                mapping[5],
                0,
                target.width,
                target.height,
                int(backdrop is not None),
                2 if underlay is not None else 0,
            ],
            dtype=np.float32,
        )
        uniform = device.create_buffer_with_data(
            data=params, usage=self.bundle.wgpu.BufferUsage.UNIFORM
        )
        textures = [source, previous or source, mask or source]
        textures.extend([levels[i] if i < len(levels) else source for i in range(8)])
        bindings = [{"binding": i, "resource": texture.view} for i, texture in enumerate(textures)]
        bindings.append({"binding": 11, "resource": {"buffer": uniform}})
        bindings.append({"binding": 12, "resource": (coverage or source).view})
        bindings.append({"binding": 13, "resource": (backdrop or source).view})
        bindings.append({"binding": 14, "resource": (underlay or source).view})
        bind_group = device.create_bind_group(
            layout=pipeline.get_bind_group_layout(0), entries=bindings
        )
        render_pass = encoder.begin_render_pass(
            color_attachments=[
                {
                    "view": target.view,
                    "resolve_target": None,
                    "clear_value": (0, 0, 0, 0),
                    "load_op": "clear",
                    "store_op": "store",
                }
            ]
        )
        render_pass.set_pipeline(pipeline)
        render_pass.set_bind_group(0, bind_group)
        render_pass.draw(3)
        render_pass.end()

    def finish_frame(self, active_ids: set[str], *, submitted: bool) -> None:
        pending, self.pending = self.pending, {}
        if submitted:
            for identifier, entry in pending.items():
                old = self.cache.pop(identifier, None)
                if old is not None:
                    self.retired.append(old[1])
                self.cache[identifier] = entry
        else:
            self.retired.extend(entry[1] for entry in pending.values())
        for identifier in list(self.cache):
            if identifier not in active_ids:
                self.retired.append(self.cache.pop(identifier)[1])
        size = sum(self._bytes(entry[1]) for entry in self.cache.values())
        while self.cache and (size > self.budget_bytes or len(self.cache) > 32):
            _, entry = self.cache.popitem(last=False)
            size -= self._bytes(entry[1])
            self.retired.append(entry[1])
        size = sum(self._bytes(t) for t in self.scratch)
        for target in self.retired:
            if submitted and size + self._bytes(target) <= self.budget_bytes:
                self.scratch.append(target)
                size += self._bytes(target)
            else:
                target.texture.destroy()
        self.retired.clear()

    def release(self) -> None:
        if self.captured is not None:
            self.captured.texture.destroy()
            self.captured = None
        self.finish_frame(set(), submitted=False)
        for target in self.scratch:
            target.texture.destroy()
        self.scratch.clear()

    @staticmethod
    def _bytes(target: WgpuRenderTargetHandle) -> int:
        channels = {"r16float": 2, "r32float": 4, "rgba16float": 8, "rgba32float": 16}
        return target.width * target.height * channels[target.format]
