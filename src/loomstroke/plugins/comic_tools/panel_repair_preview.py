"""Detached boundary preview and content/edge repairs in one atomic edit."""

import copy
import math

from loomstroke.core.comic_panels.content_move_command import translated_mask
from loomstroke.core.comic_panels.convex_clip import inset_polygon
from loomstroke.core.comic_panels.diagnostics import diagnose_content
from loomstroke.core.comic_panels.edit_draft import PanelEditCommand
from loomstroke.core.comic_panels.image_placement import PLACEMENT_KEY, ImagePlacement, fit_image
from loomstroke.core.comic_panels.layer_patch_command import PanelLayerPatchCommand
from loomstroke.core.comic_panels.metadata import read_frame_layer
from loomstroke.core.comic_panels.models import PanelFrameError
from loomstroke.core.command_types import LayerTransform
from loomstroke.core.geometry import Affine2D

from .panel_clip_policy import authored_clip
from .panel_content_move_plan import content_move_commands
from .panel_edge_geometry import edge_side, segment_endpoints
from .panel_frame_layout_adapter import layout_snapshot
from .panel_layout_plan import materialized_layout_command


class PanelRepairPreview:
    def __init__(
        self, state, layout, *, page_size, fixed_frame=False, ownership=None, inspect_clips=True
    ):
        self.state, self.layout, self.page_size = state, layout, page_size
        self.fixed_frame = fixed_frame
        self.ownership = ownership
        from .panel_convex_geometry import has_inclined_cuts  # noqa: PLC0415
        from .panel_effective_convex import effective_convex_panels  # noqa: PLC0415

        self.polygons = (
            effective_convex_panels(layout, state.panel_records, page_size)
            if (state.panel_polygons or has_inclined_cuts(layout.root))
            else {}
        )
        self.rects = (
            {p: shape.rect for p, shape in self.polygons.items()}
            if self.polygons
            else {
                r.panel_id: r.rect
                for r in layout_snapshot(
                    layout, page_size, state.panel_records, owners_override=ownership
                ).regions
            }
        )
        if self.rects.keys() != state.panel_rects.keys():
            raise PanelFrameError("boundary repair cannot add or remove panels")
        self.image_repairs = {}
        self.authored_clip_panels = (
            tuple(
                pid
                for pid, record in state.panel_records.items()
                if read_frame_layer(record.metadata) and authored_clip(state, pid, page_size)
            )
            if not fixed_frame and inspect_clips
            else ()
        )
        self.regenerated_clips = set()
        self.clip_moves = {}
        self.moves = {}
        self.edge_policies = {}
        self._geometry_plan = None
        self._geometry_policies = None
        self.paint_after = None
        self.items = {}
        self.edge_items = {}
        for panel_id, record in state.panel_records.items():
            frame = read_frame_layer(record.metadata)
            if frame is None:
                continue
            for unit in state.panel_contents.get(frame.content_group_id, ()):
                self.items[unit.layer_id] = (panel_id, unit)
            for index, edge in enumerate(() if fixed_frame else frame.edges):
                if edge.render_mode == "raster":
                    self.edge_items[edge.edge_id] = (
                        panel_id,
                        edge,
                        None if self.polygons else edge_side(edge, index),
                    )

    def fit(self, item_id, *, policy="cover", focal=(0.5, 0.5)):
        panel_id, _ = self.items[item_id]
        part = self.state.panel_parts[item_id]
        if PLACEMENT_KEY not in (part.values["metadata"] or {}):
            raise PanelFrameError("choose an imported image to adjust its composition")
        image = part.values["image"]
        self.image_repairs[item_id] = fit_image(
            (image.width(), image.height()), self.clip_rect(panel_id), policy=policy, focal=focal
        )
        self.moves.pop(item_id, None)

    def move(self, item_id, dx, dy):
        if item_id not in self.items or not all(math.isfinite(v) for v in (dx, dy)):
            raise PanelFrameError("invalid content repair position")
        # The existing command port expresses whole-unit moves in page pixels.
        self.moves[item_id] = (round(dx), round(dy))

    def content_bounds(self, item_id):
        _, unit = self.items[item_id]
        part = self.state.panel_parts[item_id]
        if PLACEMENT_KEY in (part.values["metadata"] or {}):
            placement = self.image_repairs.get(item_id)
            affine = (placement.transform if placement else part.values["transform"]).to_affine()
            image = part.values["image"]
            points = tuple(
                affine.apply(*p)
                for p in (
                    (0, 0),
                    (image.width(), 0),
                    (image.width(), image.height()),
                    (0, image.height()),
                )
            )
            xs, ys = zip(*points, strict=True)
            bounds = min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)
        else:
            bounds = unit.bounds.bounds
        if bounds is None:
            raise PanelFrameError("Bounds could not be determined")
        dx, dy = self.moves.get(item_id, (0, 0))
        return bounds[0] + dx, bounds[1] + dy, *bounds[2:]

    def scale_image(self, item_id, factor):
        if not math.isfinite(factor) or not 0.05 <= factor <= 20:
            raise PanelFrameError("invalid content repair scale")
        part = self.state.panel_parts[item_id]
        metadata = part.values["metadata"] or {}
        if PLACEMENT_KEY not in metadata:
            raise PanelFrameError("choose an imported image to adjust its composition")
        placement = self.image_repairs.get(item_id)
        affine = (placement.transform if placement else part.values["transform"]).to_affine()
        x, y, w, h = self.content_bounds(item_id)
        cx, cy = x + w / 2, y + h / 2
        dx, dy = self.moves.pop(item_id, (0, 0))
        changed = Affine2D(
            affine.a * factor,
            affine.b * factor,
            affine.c * factor,
            affine.d * factor,
            cx + (affine.tx + dx - cx) * factor,
            cy + (affine.ty + dy - cy) * factor,
        )
        image = part.values["image"]
        self.image_repairs[item_id] = ImagePlacement(
            (image.width(), image.height()),
            LayerTransform.from_affine(changed),
            metadata[PLACEMENT_KEY].get("fit_policy", "cover"),
        )

    def edge_policy(self, edge_id, policy):
        if edge_id not in self.edge_items or policy not in {"keep_pixels", "stretch_tangent"}:
            raise PanelFrameError("invalid raster edge repair policy")
        self.edge_policies[edge_id] = policy

    def regenerate_clip(self, panel_id, enabled=True):
        if panel_id not in self.authored_clip_panels:
            raise PanelFrameError("choose a panel with an edited content mask")
        if enabled:
            self.regenerated_clips.add(panel_id)
        else:
            self.regenerated_clips.discard(panel_id)

    def clip_rect(self, panel_id):
        x, y, w, h = self.rects[panel_id]
        frame = read_frame_layer(self.state.panel_records[panel_id].metadata)
        inset = frame.clip_inset_px
        if self.polygons:
            points = inset_polygon(self.polygons[panel_id].points, inset)
            xs, ys = zip(*points, strict=True)
            return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)
        return x + inset, y + inset, w - 2 * inset, h - 2 * inset

    def diagnostics(self):
        result = []
        for item_id, (panel_id, unit) in self.items.items():
            part = self.state.panel_parts[item_id]
            image = PLACEMENT_KEY in (part.values["metadata"] or {})
            fit = self.image_repairs.get(item_id)
            policy = (
                fit.fit_policy
                if fit
                else (part.values["metadata"] or {})
                .get(PLACEMENT_KEY, {})
                .get("fit_policy", "keep")
            )
            if image:
                source = part.values["image"]
                affine = (fit.transform if fit else part.values["transform"]).to_affine()
                w, h = source.width(), source.height()
                points = tuple(affine.apply(*p) for p in ((0, 0), (w, 0), (w, h), (0, h)))
            elif unit.bounds.bounds:
                x, y, w, h = unit.bounds.bounds
                points = ((x, y), (x + w, y), (x + w, y + h), (x, y + h))
            else:
                points = None
            dx, dy = self.moves.get(item_id, (0, 0))
            if points:
                points = tuple((x + dx, y + dy) for x, y in points)
            result.append(
                diagnose_content(
                    item_id,
                    points,
                    self.clip_rect(panel_id),
                    image=image,
                    fit_policy=policy,
                    visible=part.values["visible"],
                    clip_polygon=(
                        inset_polygon(
                            self.polygons[panel_id].points,
                            read_frame_layer(
                                self.state.panel_records[panel_id].metadata
                            ).clip_inset_px,
                        )
                        if self.polygons
                        else None
                    ),
                )
            )
        return tuple(result)

    def edge_diagnostics(self):
        result = []
        for edge_id, (panel_id, edge, side) in self.edge_items.items():
            if self.polygons:
                polygon = self.polygons[panel_id]
                position = polygon.boundaries.index(edge.boundary_ref)
                a, b = (
                    polygon.points[position],
                    polygon.points[(position + 1) % len(polygon.points)],
                )
                start, end = tuple(
                    tuple(a[i] + t * (b[i] - a[i]) for i in (0, 1)) for t in edge.span
                )
            else:
                start, end = segment_endpoints(self.rects[panel_id], side, edge.span)
            length = math.dist(start, end)
            policy = self.edge_policies.get(edge_id, edge.raster_resize_policy)
            difference = (
                0
                if policy == "stretch_tangent"
                else length - edge.raster_anchor.base_edge_length_px
            )
            result.append(
                (
                    edge_id,
                    "short" if difference > 0.01 else "overrun" if difference < -0.01 else "fits",
                    abs(difference),
                )
            )
        return tuple(result)

    def geometry_command(self):
        if self.fixed_frame:
            return None
        key = (tuple(sorted(self.edge_policies.items())), tuple(sorted(self.regenerated_clips)))
        if self._geometry_plan is not None and key == self._geometry_policies:
            return self._geometry_plan
        planner = materialized_layout_command
        if self.polygons:
            from .panel_convex_plan import convex_layout_command  # noqa: PLC0415

            planner = convex_layout_command
        self._geometry_plan = planner(
            self.state,
            self.layout,
            page_size=self.page_size,
            label="Resize panels and repair content",
            edge_policies=self.edge_policies,
            regenerate_clips=self.regenerated_clips,
            **({"ownership": self.ownership} if self.ownership is not None else {}),
        )
        self._geometry_policies = key
        return self._geometry_plan

    def command(self):
        layout_command = self.geometry_command()
        commands = [layout_command] if layout_command is not None else []
        for panel_id, (dx, dy) in self.clip_moves.items():
            if panel_id in self.regenerated_clips:
                continue
            frame = read_frame_layer(self.state.panel_records[panel_id].metadata)
            part = self.state.panel_parts[frame.content_group_id]
            mask = part.values["layer_mask"]
            if mask is not None and (dx or dy):
                commands.append(
                    PanelLayerPatchCommand(
                        part.layer_id,
                        before={"layer_mask": mask},
                        after={"layer_mask": translated_mask(mask, dx, dy)},
                    )
                )
        if self.fixed_frame:
            from .panel_edit_gate import require_editable_parts  # noqa: PLC0415
            from .panel_source_guard import source_guard  # noqa: PLC0415

            require_editable_parts(self.state, set(self.image_repairs) | set(self.moves))
            commands.append(source_guard(self.state, page_size=self.page_size))
        images = set(self.image_repairs) | {
            item_id
            for item_id in self.moves
            if PLACEMENT_KEY in (self.state.panel_parts[item_id].values["metadata"] or {})
        }
        for item_id in images:
            part = self.state.panel_parts[item_id]
            metadata = copy.deepcopy(part.values["metadata"])
            placement = self.image_repairs.get(item_id)
            transform = placement.transform if placement else part.values["transform"]
            if placement:
                metadata[PLACEMENT_KEY]["fit_policy"] = placement.fit_policy
            dx, dy = self.moves.get(item_id, (0, 0))
            transform = transform.with_translation(transform.tx + dx, transform.ty + dy)
            mask_updates = {
                name: translated_mask(part.values[name], dx, dy)
                for name in ("layer_mask", "effect_mask")
                if (dx or dy) and part.values.get(name) is not None
            }
            commands.append(
                PanelLayerPatchCommand(
                    item_id,
                    before={
                        "metadata": part.values["metadata"],
                        "transform": part.values["transform"],
                        **{name: part.values[name] for name in mask_updates},
                    },
                    after={"metadata": metadata, "transform": transform, **mask_updates},
                )
            )
        for item_id, (dx, dy) in self.moves.items():
            if item_id not in images and (dx or dy):
                commands.extend(content_move_commands(self.state, item_id, dx, dy))
        label = (
            "Adjust selected content on canvas"
            if self.fixed_frame
            else "Resize panels and repair content"
        )
        return PanelEditCommand(commands, label=label)

    def content_changed(self):
        if any(dx or dy for dx, dy in self.moves.values()):
            return True
        for item_id, placement in self.image_repairs.items():
            values = self.state.panel_parts[item_id].values
            original_policy = (values["metadata"] or {}).get(PLACEMENT_KEY, {}).get("fit_policy")
            if (
                placement.transform != values["transform"]
                or placement.fit_policy != original_policy
            ):
                return True
        return False
