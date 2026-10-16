"""Whole-content allocation when replacing a materialized layout template."""

import copy
from dataclasses import replace

from loomstroke.core.comic_panels.allocation import allocate_split
from loomstroke.core.comic_panels.edit_draft import PanelEditCommand
from loomstroke.core.comic_panels.layer_patch_command import PanelLayerPatchCommand
from loomstroke.core.comic_panels.membership import PART_KEY, SHARED_KEY
from loomstroke.core.comic_panels.metadata import read_frame_layer
from loomstroke.core.comic_panels.models import PanelFrameError, PanelRegion
from loomstroke.core.comic_panels.polygon_allocation import (
    allocate_polygons,
    polygon_overlap,
    rect_points,
)
from loomstroke.core.comic_panels.regions import intersection_area
from loomstroke.core.comic_panels.shared_metadata import read_shared_content, write_shared_content

from . import layout_tree as lt
from .panel_content_shell_plan import content_shell_parent
from .panel_convex_geometry import compute_convex_panels, has_inclined_cuts
from .panel_display_plan import content_owner_commands
from .panel_layout_plan import materialized_layout_command
from .panel_shared_plan import _shared_mask
from .panel_source_guard import source_guard


class PanelTemplatePreview:
    def __init__(self, state, layout, *, page_size):
        from .panel_current_format import current_panels  # noqa: PLC0415

        state, self._conversions = current_panels(state, page_size=page_size)
        self.state, self.layout, self.page_size = state, layout, page_size
        self.polygons = (
            compute_convex_panels(layout, page_size)
            if (state.panel_polygons or has_inclined_cuts(layout.root))
            else {}
        )
        rects = (
            {pid: polygon.rect for pid, polygon in self.polygons.items()}
            if self.polygons
            else lt.compute_panel_rects(layout, *page_size)
        )
        self.children = tuple(PanelRegion(pid, (pid,), rect) for pid, rect in rects.items())
        self.units = tuple(
            unit
            for record in state.panel_records.values()
            for unit in state.panel_contents.get(
                read_frame_layer(record.metadata).content_group_id, ()
            )
        )
        self.overrides = {}
        self.page_id = ""
        self._slots = None
        self._convex_geometry = None
        self._prepare_geometry = False

    def prepare(self):
        from .panel_raster_content_bounds import state_with_raster_bounds  # noqa: PLC0415

        self.state = state_with_raster_bounds(self.state)
        units = {u.layer_id: u for items in self.state.panel_contents.values() for u in items}
        self.units = tuple(units[u.layer_id] for u in self.units)
        self._prepare_geometry = True
        self.command()
        return self

    @property
    def allocations(self):
        if len(self.children) == 1:
            from loomstroke.core.comic_panels.allocation import ContentAllocation  # noqa: PLC0415

            return tuple(
                ContentAllocation(u.layer_id, self.children[0].panel_id, "single_target")
                for u in self.units
            )
        # Every target is an explicit new template slot; ties use reading order,
        # with unresolved status visible for the operator to override.
        if self.polygons:
            return allocate_polygons(
                [u.bounds for u in self.units],
                {pid: polygon.points for pid, polygon in self.polygons.items()},
                self.children[0].panel_id,
                overrides=self.overrides,
            )
        return allocate_split(
            [u.bounds for u in self.units],
            self.children,
            self.children[0].panel_id,
            overrides=self.overrides,
        )

    def assign(self, item_id, panel_id):
        if item_id not in {u.layer_id for u in self.units} or panel_id not in {
            p.panel_id for p in self.children
        }:
            raise PanelFrameError("invalid template content allocation")
        self.overrides[item_id] = panel_id

    def command(self):  # noqa: PLR0915 - retire/allocation/shared references in one edit
        from .panel_edit_gate import require_panel_editable  # noqa: PLC0415

        for panel_id in self.state.panel_records:
            require_panel_editable(self.state, panel_id, geometry=True)
        targets = {p.panel_id for p in self.children}
        retained = {
            pid: record for pid, record in self.state.panel_records.items() if pid in targets
        }
        no_shared = {
            key: part
            for key, part in self.state.panel_parts.items()
            if SHARED_KEY not in (part.values["metadata"] or {})
        }
        filtered = replace(
            self.state,
            panel_records=retained,
            panel_sources={
                pid: source for pid, source in self.state.panel_sources.items() if pid in retained
            },
            panel_parts=no_shared,
        )
        layout_command = self.slot_command(filtered)
        groups = {
            pid: read_frame_layer(record.metadata).content_group_id
            for pid, record in retained.items()
        }
        panel_groups = {
            pid: read_frame_layer(record.metadata).panel_group_id
            for pid, record in retained.items()
        }
        planned_records = dict(retained)
        from .model import PANEL_META_KEY, PanelRecord  # noqa: PLC0415

        for command in layout_command.commands:
            if getattr(command, "_frame", None):
                groups[command.panel_id] = command._frame.content_group_id
                panel_groups[command.panel_id] = command._frame.panel_group_id
                planned_records[command.panel_id] = PanelRecord.from_meta(
                    command._carrier.meta[PANEL_META_KEY]
                )
        commands = [
            *self._conversions,
            source_guard(self.state, page_size=self.page_size),
            layout_command,
        ]
        shell_sources = {
            read_frame_layer(record.metadata).content_group_id: pid
            for pid, record in self.state.panel_records.items()
        }
        shells = {}
        for allocation in self.allocations:
            unit = next(u for u in self.units if u.layer_id == allocation.item_id)
            if unit.parent_id != groups[allocation.panel_id]:
                target = content_shell_parent(
                    self.state,
                    unit,
                    shell_sources.get(unit.parent_id),
                    groups[allocation.panel_id],
                    self.page_size,
                    shells,
                    commands,
                )
                commands.extend(
                    content_owner_commands(
                        self.state,
                        unit.layer_id,
                        allocation.panel_id,
                        target,
                        panel_group_id=panel_groups[allocation.panel_id],
                    )
                )
        for pid, record in self.state.panel_records.items():
            if pid in retained:
                continue
            frame = read_frame_layer(record.metadata)
            source_id, _ = self.state.panel_sources[pid]
            for layer_id, role, hide in (
                (frame.panel_group_id, "retired_panel", True),
                (source_id, "retired_carrier", False),
            ):
                part = self.state.panel_parts[layer_id]
                metadata = copy.deepcopy(part.values["metadata"])
                metadata[PART_KEY]["role"] = role
                updates = {"metadata": metadata}
                if hide:
                    updates["visible"] = False
                commands.append(
                    PanelLayerPatchCommand(
                        layer_id, before={key: part.values[key] for key in updates}, after=updates
                    )
                )
        planned = replace(
            self.state,
            panel_records=planned_records,
            panel_rects={p.panel_id: p.rect for p in self.children},
            panel_polygons=self.polygons,
        )
        for group_id, part in self.state.panel_parts.items():
            if SHARED_KEY not in (part.values["metadata"] or {}):
                continue
            shared = read_shared_content(part.values["metadata"], group_id=group_id)
            mapped = tuple(
                dict.fromkeys(
                    max(
                        self.children,
                        key=lambda p, old=old: self.overlap(old, p),
                    ).panel_id
                    for old in shared.panel_ids
                )
            )
            metadata = write_shared_content(
                part.values["metadata"], replace(shared, panel_ids=mapped)
            )
            commands.append(
                PanelLayerPatchCommand(
                    group_id,
                    before={
                        "metadata": part.values["metadata"],
                        "layer_mask": part.values["layer_mask"],
                    },
                    after={
                        "metadata": metadata,
                        "layer_mask": _shared_mask(
                            planned, mapped, tuple(map(int, self.page_size))
                        ),
                    },
                )
            )
        if self.polygons:
            commands.append(self.geometry_command(filtered, layout_command))
        return PanelEditCommand(commands, label="Replace template and keep panel content")

    def geometry_command(self, state, layout_command):
        from .convex_template_geometry_command import (  # noqa: PLC0415
            ConvexTemplateGeometryCommand,
        )
        from .panel_geometry_projection import project_panel_geometry  # noqa: PLC0415

        if self._convex_geometry is None:
            source = self.state
            if not source.panel_polygons:
                source = replace(
                    source,
                    panel_rects=lt.compute_panel_rects(self.layout, *self.page_size),
                    panel_polygons={},
                )
            self._convex_geometry = ConvexTemplateGeometryCommand(
                source, self.layout, self.page_size
            )
            if self._prepare_geometry:
                self._convex_geometry.prepare(
                    project_panel_geometry(state, layout_command, self.page_size)
                )
        return self._convex_geometry

    def overlap(self, old, target):
        if not self.polygons:
            return intersection_area(self.state.panel_rects[old], target.rect)
        source = self.state.panel_polygons.get(old)
        points = source.points if source else rect_points(self.state.panel_rects[old])
        return polygon_overlap(points, self.polygons[target.panel_id].points)

    def slot_command(self, state):
        if self._slots is not None:
            return self._slots
        if self.polygons and self.state.panel_polygons:
            from .panel_convex_template_plan import convex_template_slots  # noqa: PLC0415

            self._slots = convex_template_slots(
                state, self.layout, self.polygons, page_size=self.page_size
            )
            return self._slots
        command = materialized_layout_command(
            state,
            self.layout,
            page_size=self.page_size,
            label="Replace panel template",
            ownership={pid: (pid,) for pid in state.panel_records},
        )
        if self.polygons:
            first = next(iter(self.state.panel_records.values()))
            edge = read_frame_layer(first.metadata).edges[0]
            opacity = self.state.panel_parts[edge.part_layer_id].values["opacity"]
            if opacity != 1:
                from loomstroke.core.comic_panels.part_structure_command import (  # noqa: PLC0415
                    PanelLayerInsertCommand,
                )

                updates = []
                for item in command.commands:
                    if getattr(item, "_frame", None):
                        updates.extend(
                            PanelLayerPatchCommand(
                                new_edge.part_layer_id,
                                before={"opacity": 1.0},
                                after={"opacity": opacity},
                            )
                            for new_edge in item._frame.edges
                        )
                    elif (
                        isinstance(item, PanelLayerInsertCommand)
                        and item._layer.layer_type == "vector"
                    ):
                        item._layer.opacity = opacity
                command.commands += tuple(updates)
        self._slots = command
        return command
