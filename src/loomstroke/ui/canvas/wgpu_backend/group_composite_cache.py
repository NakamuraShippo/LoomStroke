"""Bounded, submit-committed results for unchanged comic group subtrees."""

from collections import OrderedDict
from dataclasses import dataclass
from types import SimpleNamespace

from loomstroke.core.appearance.live_styles import style_signature

from .types import WgpuRenderTargetHandle


@dataclass(frozen=True)
class CachedGroup:
    target: object
    current_ids: frozenset
    layers_drawn: int

    def result(self):
        return SimpleNamespace(
            current_target=self.target,
            owned_targets=[],
            current_ids=set(self.current_ids),
            reports=[],
            warnings=[],
            layers_drawn=self.layers_drawn,
        )


class GroupCompositeCache:
    def __init__(self, budget_bytes=128 * 1024 * 1024):
        self.budget_bytes = budget_bytes
        self.entries = OrderedDict()
        self.pending = {}
        self.bytes = 0

    def lookup(self, key):
        item = self.pending.get(key) or self.entries.get(key)
        if key in self.entries:
            self.entries.move_to_end(key)
        return item.result() if item is not None else None

    def stage(self, key, result, compositor, encoder):
        pending_bytes = sum(
            item.target.width * item.target.height * 4 for item in self.pending.values()
        )
        if (
            result.warnings
            or self.bytes
            + pending_bytes
            + result.current_target.width * result.current_target.height * 4
            > self.budget_bytes
        ):
            return
        source = result.current_target
        texture = compositor._device.create_texture(
            size=(source.width, source.height, 1),
            format=source.format,
            usage=compositor._wgpu.TextureUsage.COPY_DST
            | compositor._wgpu.TextureUsage.TEXTURE_BINDING,
        )
        try:
            target = WgpuRenderTargetHandle(
                texture, texture.create_view(), source.width, source.height, source.format
            )
            encoder.copy_texture_to_texture(
                {"texture": source.texture}, {"texture": texture}, (source.width, source.height, 1)
            )
        except Exception:
            texture.destroy()
            raise
        # Cache copies never enter either scratch allocator. Encoder ordering
        # captures the completed contents before their working target is reused.
        self.pending[key] = CachedGroup(target, frozenset(result.current_ids), result.layers_drawn)

    def finish(self, submitted):
        pending, self.pending = self.pending, {}
        if not submitted:
            for item in pending.values():
                item.target.texture.destroy()
            return
        for key, item in pending.items():
            previous = self.entries.pop(key, None)
            if previous is not None:
                self.bytes -= previous.target.width * previous.target.height * 4
                if previous.target is not item.target:
                    previous.target.texture.destroy()
            self.entries[key] = item
            self.bytes += item.target.width * item.target.height * 4
        while self.bytes > self.budget_bytes or len(self.entries) > 64:
            _, item = self.entries.popitem(last=False)
            self.bytes -= item.target.width * item.target.height * 4
            item.target.texture.destroy()

    def release(self):
        self.finish(False)
        for item in self.entries.values():
            item.target.texture.destroy()
        self.entries.clear()
        self.bytes = 0


def group_composite_key(compositor, layer, scene, region, *, depth=0, mask_upload_policy=None):
    if not getattr(layer, "mask_region_eligible", False):
        return None

    def signature(node, depth):
        if depth >= compositor.MAX_WGPU_GROUP_DEPTH:
            return None
        kind = getattr(node, "layer_type", "raster")
        if (
            kind not in {"group", "raster", "vector", "text", "ai_effect"}
            or getattr(node, "filter_type", getattr(node, "effect_type", None))
            or getattr(node, "vector_direct_ready", False)
            or getattr(node, "id", None) == compositor._screen_space_vector_layer_id
        ):
            return None
        children = [signature(child, depth + 1) for child in getattr(node, "children", ())]
        if any(child is None for child in children):
            return None
        image = getattr(node, "image", None)
        if image is None and any(obj.paths for obj in getattr(node, "vector_objects", ())):
            return None
        mask = getattr(node, "layer_mask", None)
        return (
            node.id,
            style_signature(node),
            getattr(node, "texture_version", 0),
            bool(getattr(node, "visible", True)),
            float(getattr(node, "opacity", 1)),
            str(getattr(node, "blend_mode", "normal")),
            bool(getattr(node, "clipping", False)),
            image.cacheKey() if image is not None else None,
            mask.cacheKey() if mask is not None else None,
            bool(getattr(node, "mask_enabled", True)),
            bool(getattr(node, "mask_nearest", False)),
            getattr(node, "offset_x", 0),
            getattr(node, "offset_y", 0),
            repr(getattr(node, "transform", None)),
            repr(getattr(node, "_preview_transform", None)),
            getattr(node, "vector_raster_scale", 1),
            tuple(getattr(node, "vector_raster_origin", (0, 0))),
            tuple(children),
        )

    values = tuple(signature(child, depth + 1) for child in layer.children)
    if any(value is None for value in values):
        return None
    return (
        layer.id,
        mask_upload_policy,
        depth,
        scene.width,
        scene.height,
        compositor._view.affine if compositor._view is not None else None,
        region,
        values,
    )
