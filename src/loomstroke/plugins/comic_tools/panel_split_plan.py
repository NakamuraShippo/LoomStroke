"""Content-preserving split previews with deterministic allocation and overrides."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from loomstroke.core.comic_panels.allocation import allocate_split
from loomstroke.core.comic_panels.edit_draft import PanelEditCommand
from loomstroke.core.comic_panels.metadata import read_frame_layer
from loomstroke.core.comic_panels.models import PanelFrameError, PanelRegion
from loomstroke.core.comic_panels.polygon_allocation import allocate_polygons

from . import layout_tree as lt
from .panel_content_copy_plan import copy_content_commands
from .panel_content_shell_plan import content_shell_parent
from .panel_display_plan import content_owner_commands
from .panel_layout_plan import materialized_layout_command


@dataclass
class PanelSplitPreview:
    state: object
    original_panel_id: str
    layout: lt.ComicPageLayout
    page_size: tuple[int, int]
    overrides: dict[str, str] = field(default_factory=dict)
    image_policies: dict[str, str] = field(default_factory=dict)
    page_id: str = ""
    ownership: dict = field(default_factory=dict)
    effective_children: tuple = ()
    polygons: dict = field(default_factory=dict)
    _prepared_layout: object = field(default=None, init=False, repr=False)

    def __post_init__(self):
        if self.state.panel_polygons:
            from .panel_convex_geometry import compute_convex_panels  # noqa: PLC0415

            self.polygons = compute_convex_panels(self.layout, self.page_size)

    @property
    def children(self):
        if self.effective_children:
            return self.effective_children
        if self.polygons:
            return tuple(
                PanelRegion(pid, (pid,), polygon.rect)
                for pid, polygon in self.polygons.items()
                if pid == self.original_panel_id or pid not in self.state.panel_records
            )
        old_rect = self.state.panel_rects[self.original_panel_id]
        x, y, w, h = old_rect
        result = []
        for panel_id, rect in lt.compute_panel_rects(self.layout, *self.page_size).items():
            rx, ry, rw, rh = rect
            if rx >= x and ry >= y and rx + rw <= x + w + 1e-6 and ry + rh <= y + h + 1e-6:
                result.append(PanelRegion(panel_id, (panel_id,), rect))
        return tuple(result)

    @property
    def allocations(self):
        frame = read_frame_layer(self.state.panel_records[self.original_panel_id].metadata)
        units = self.state.panel_contents.get(frame.content_group_id, ())
        if self.polygons:
            return allocate_polygons(
                [u.bounds for u in units],
                {c.panel_id: self.polygons[c.panel_id].points for c in self.children},
                self.original_panel_id,
                overrides=self.overrides,
            )
        return allocate_split(
            [u.bounds for u in units],
            self.children,
            self.original_panel_id,
            overrides=self.overrides,
        )

    def assign(self, item_id, panel_id):
        candidate = dict(self.overrides, **{item_id: panel_id})
        before, self.overrides = self.overrides, candidate
        try:
            _ = self.allocations
        except PanelFrameError:
            self.overrides = before
            raise

    def command(self):  # noqa: PLR0912 - explicit move/copy/shared dispositions
        from .panel_split_shared_plan import (  # noqa: PLC0415 - planner cycle
            require_shared_split,
            split_shared_commands,
        )

        require_shared_split(self)
        layout_command = self.layout_command()
        groups = {}
        panel_groups = {}
        for panel_id, record in self.state.panel_records.items():
            frame = read_frame_layer(record.metadata)
            if frame:
                groups[panel_id] = frame.content_group_id
                panel_groups[panel_id] = frame.panel_group_id
        for command in layout_command.commands:
            frame = getattr(command, "_frame", None)
            if frame:
                groups[command.panel_id] = frame.content_group_id
                panel_groups[command.panel_id] = frame.panel_group_id
        changes = [layout_command]
        shells = {}
        shared_groups = {}
        for allocation in self.allocations:
            policy = self.image_policies.get(allocation.item_id, "move")
            if policy == "copy":
                frame = read_frame_layer(self.state.panel_records[self.original_panel_id].metadata)
                unit = next(
                    u
                    for u in self.state.panel_contents[frame.content_group_id]
                    if u.layer_id == allocation.item_id
                )
                for child in self.children:
                    if child.panel_id != self.original_panel_id:
                        target = content_shell_parent(
                            self.state,
                            unit,
                            self.original_panel_id,
                            groups[child.panel_id],
                            self.page_size,
                            shells,
                            changes,
                        )
                        changes.extend(
                            copy_content_commands(
                                self.state,
                                unit,
                                child.panel_id,
                                target,
                                panel_groups[child.panel_id],
                            )
                        )
                continue
            if policy == "shared":
                # Planned carrier records are sufficient to derive all child masks.
                records = dict(self.state.panel_records)
                rects = dict(self.state.panel_rects)
                for child in self.children:
                    rects[child.panel_id] = child.rect
                for command in layout_command.commands:
                    frame = getattr(command, "_frame", None)
                    if frame:
                        records[command.panel_id] = type(records[self.original_panel_id]).from_meta(
                            command._carrier.meta["loomstroke:comic_panel"]
                        )
                planned = replace(
                    self.state,
                    panel_records=records,
                    panel_rects=rects,
                    panel_polygons=self.polygons or self.state.panel_polygons,
                )
                frame = read_frame_layer(self.state.panel_records[self.original_panel_id].metadata)
                unit = next(
                    u
                    for u in self.state.panel_contents[frame.content_group_id]
                    if u.layer_id == allocation.item_id
                )
                changes.extend(split_shared_commands(self, planned, unit, shared_groups, shells))
                continue
            if policy != "move":
                raise PanelFrameError("unsupported split image policy")
            if allocation.panel_id != self.original_panel_id:
                frame = read_frame_layer(self.state.panel_records[self.original_panel_id].metadata)
                unit = next(
                    u
                    for u in self.state.panel_contents[frame.content_group_id]
                    if u.layer_id == allocation.item_id
                )
                target = content_shell_parent(
                    self.state,
                    unit,
                    self.original_panel_id,
                    groups[allocation.panel_id],
                    self.page_size,
                    shells,
                    changes,
                )
                changes.extend(
                    content_owner_commands(
                        self.state,
                        allocation.item_id,
                        allocation.panel_id,
                        target,
                        panel_group_id=panel_groups[allocation.panel_id],
                    )
                )
        return PanelEditCommand(changes, label="Split panel and repair content")

    def layout_command(self):
        if self._prepared_layout is not None:
            return self._prepared_layout
        if self.polygons:
            from .panel_convex_split_plan import convex_split_command  # noqa: PLC0415

            return convex_split_command(
                self.state,
                self.original_panel_id,
                self.layout,
                self.children,
                self.polygons,
                page_size=self.page_size,
            )
        return materialized_layout_command(
            self.state,
            self.layout,
            page_size=self.page_size,
            label="Split panel and repair content",
            ownership=self.ownership or None,
        )

    def prepare(self):
        from .panel_raster_content_bounds import state_with_raster_bounds  # noqa: PLC0415

        self.state = state_with_raster_bounds(self.state)
        self._prepared_layout = self.layout_command()
        return self


def split_preview(state, panel_id, orientation, *, page_size, ratio=0.5, count=2):
    frame = read_frame_layer(state.panel_records[panel_id].metadata)
    if frame is None:
        raise PanelFrameError("split requires a materialized panel")
    if frame.layout_leaf_ids != (panel_id,):
        if state.panel_polygons:
            raise PanelFrameError("split merged inclined panels requires a convex ownership plan")
        if count != 2:
            raise PanelFrameError("split merged panels into two parts at a time")
        from .panel_effective_split import split_effective_panel  # noqa: PLC0415

        layout, owners, children = split_effective_panel(
            state, panel_id, orientation, ratio, page_size=page_size
        )
        return PanelSplitPreview(
            state, panel_id, layout, page_size, ownership=owners, effective_children=children
        )
    layout = lt.split_panel(state.layout, panel_id, orientation, ratio=ratio, count=count)
    return PanelSplitPreview(state, panel_id, layout, page_size)
