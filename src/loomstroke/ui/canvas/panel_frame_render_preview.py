"""Project detached frame repairs into the live WGPU scene only."""

import uuid
from dataclasses import replace

from loomstroke.core.canvas_layer_model import _Layer
from loomstroke.core.comic_panels.membership import PART_KEY
from loomstroke.core.snapshot_vector_rasterizer import SnapshotVectorRasterizer


class PanelFrameRenderPreview:
    def __init__(self, repair, canvas):
        self.repair = repair
        self.canvas = canvas
        self.token = uuid.uuid4().hex
        self._version_base = int(self.token, 16) << 64
        self.geometry = None
        self.geometry_key = None
        self.rasters = {}
        self.placements = {}
        self.content_key = None
        self.content = {}
        self.revision = 0
        self.rasterizer = SnapshotVectorRasterizer(
            width=repair.page_size[0], height=repair.page_size[1]
        )

    def apply_to_layers(self, layers, *, scale=1):
        from loomstroke.plugins.comic_tools.panel_render_preview_plan import (  # noqa: PLC0415
            content_preview_parts,
            geometry_preview_parts,
        )

        from .render_scene import RenderLayerSnapshot  # noqa: PLC0415

        selection = getattr(self.canvas, "_vector_selection", None)
        self.rasterizer._vector_selection = selection
        key = (
            tuple(sorted(self.repair.edge_policies.items())),
            tuple(sorted(self.repair.regenerated_clips)),
            repr(selection),
        )
        if key != self.geometry_key:
            self.geometry = geometry_preview_parts(self.repair)
            self.geometry_key = key
            self.rasters.clear()
        updates, insertions = self.geometry
        content_key = (
            tuple(sorted(self.repair.moves.items())),
            tuple(sorted(self.repair.image_repairs.items())),
            tuple(sorted(self.repair.clip_moves.items())),
            tuple(sorted(self.repair.regenerated_clips)),
        )
        if content_key != self.content_key:
            self.content = content_preview_parts(self.repair)
            self.content_key = content_key
        content = self.content

        def project(layer):
            changes = {**updates.get(layer.id, {}), **content.get(layer.id, {})}
            children = [project(child) for child in layer.children]
            for incoming in insertions:
                if incoming.parent_id == layer.id:
                    children.append(project(RenderLayerSnapshot.from_layer(incoming)))
            if not changes:
                return replace(layer, children=children)
            changed_pixels = (
                "vector_objects" in changes and layer.layer_type == "vector" and layer.visible
            )
            if changed_pixels:
                layer = self._raster_layer(layer, changes, insertions, scale)
            allowed = {
                k: v
                for k, v in changes.items()
                if k
                in {
                    "image",
                    "transform",
                    "offset_x",
                    "offset_y",
                    "layer_mask",
                    "effect_mask",
                    "visible",
                    "opacity",
                    "mask_enabled",
                }
            }
            if {"layer_mask", "effect_mask"} & changes.keys():
                # Mask identity follows the immutable candidate image, not document versions.
                # Local revision numbers restart in each draft and can select
                # an earlier draft's coverage texture for the same layer ID.
                allowed["effect_mask_texture_version"] = 0
            if "metadata" in changes:
                allowed["mask_nearest"] = bool(
                    (changes["metadata"] or {}).get(PART_KEY, {}).get("nearest_mask", False)
                )
            # Presenter reuse and affine caches key on versions, not raw transforms.
            signature = tuple(
                (
                    k,
                    (
                        v.cacheKey()
                        if k in {"layer_mask", "effect_mask"} and v is not None
                        else repr(v)
                    ),
                )
                for k, v in sorted(allowed.items())
            )
            previous = self.placements.get(layer.id)
            if previous is None or previous[0] != signature:
                self.revision += 1
                self.placements[layer.id] = signature, self.revision
            version = self.placements[layer.id][1]
            # Include draft identity when a cancel/start is coalesced into one
            # paint. Negative view-only versions cannot alias document counters.
            allowed["transform_version"] = -(self._version_base + version)
            return replace(layer, **allowed, children=children)

        return tuple(project(layer) for layer in layers)

    def _raster_layer(self, layer, changes, insertions, scale):
        from .render_scene import RenderLayerSnapshot  # noqa: PLC0415

        objects = changes["vector_objects"]
        cache_key = layer.id, scale
        cached = self.rasters.get(cache_key)
        if cached is None or cached[0] != objects:
            values = (
                self.repair.state.panel_parts[layer.id].values
                if layer.id in self.repair.state.panel_parts
                else vars(next(p for p in insertions if p.id == layer.id))
            )
            self.revision += 1
            part = _Layer(
                id=layer.id,
                name=layer.name,
                **{
                    k: v
                    for k, v in {**values, **changes}.items()
                    if k not in {"name", "id", "parent_id", "children"}
                },
            )
            part.texture_version = self.revision
            prepared = (
                getattr(self.repair, "prepared_vector_rasters", {})
                if scale == 1
                and not getattr(self.canvas, "_vector_selection", None)
                and self.repair.geometry_command()
                is getattr(self.repair, "prepared_vector_plan", None)
                else {}
            )
            result = (
                prepared[layer.id]
                if layer.id in prepared
                else self.rasterizer.rasterize_for_preview(part, scale=scale)
            )
            raster = RenderLayerSnapshot.from_layer(
                part, rasterize_vector=lambda _: result, copy_rasterized_vector=False
            )
            self.rasters[cache_key] = objects, raster
        else:
            raster = cached[1]
        return replace(raster, id=f"{layer.id}:frame-preview:{self.token}")
