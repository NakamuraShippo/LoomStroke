"""Canvas-centred frame operations using the existing HostAPI command port."""

from loomstroke.core.comic_panels.drawing_state import drawing_states, panel_input_busy
from loomstroke.core.comic_panels.drawing_target import PanelDrawingTargets
from loomstroke.core.comic_panels.edit_draft import PanelEditCommand, PanelEditDraft
from loomstroke.core.comic_panels.metadata import read_frame_layer
from loomstroke.core.comic_panels.models import PanelFrameError
from loomstroke.core.comic_panels.pending_drawing import queue_panel_drawing

from .frame_draft_lifecycle import FrameDraftLifecycle
from .frame_preview_preparation import FramePreviewPreparation
from .frame_vector_erase import FrameVectorEraseGesture
from .panel_content_plan import place_images_command
from .panel_edge_paint_plan import bake_edge_for_paint_command
from .panel_edge_plan import rasterize_edge_command
from .panel_merge_plan import merge_panel_command
from .panel_split_plan import split_preview


class FrameWorkflow:
    def __init__(self, tool):
        self.tool = tool
        self.targets = PanelDrawingTargets()
        self.draft = None
        self.split = None
        self.repair = None
        self.template = None
        self.image_tasks = set()
        self.render_preview = None
        self._preview_canvas = None
        self.lifecycle = FrameDraftLifecycle(self)
        self.preparation = FramePreviewPreparation(self)
        self.vector_erasure = FrameVectorEraseGesture(self)

    def page_id(self):
        host = self.tool.host_api
        pages = getattr(host, "pages", None)
        getter = getattr(pages, "get_active_page_id", None)
        value = getter() if callable(getter) else None
        if not value:
            raise PanelFrameError("active page identity is unavailable")
        return str(value)

    def state(self):
        state = self.tool.read_state()
        if state is None or state.panel_issues:
            raise PanelFrameError("panel state is unavailable or contains invalid references")
        return state

    def convert(self, panel_id):
        state = self.state()
        if read_frame_layer(state.panel_records[panel_id].metadata):
            return True
        return self.tool._submit(
            self.tool.controller.panel_frame_conversion_command(
                state, panel_id, page_size=self.tool._page_size(), max_group_depth=8
            )
        )

    def remember_current(self, panel_id):
        canvas = self.tool._canvas()
        if panel_input_busy(canvas):
            raise PanelFrameError("finish the current stroke or text composition first")
        target = drawing_states(canvas).get(canvas.current_layer().id)
        if target is None:
            raise PanelFrameError("choose a drawing layer in this panel first")
        self.targets.remember(self.page_id(), panel_id, target)

    def draw_here(self, panel_id, *, input_busy=False, new=False):
        self.vector_erasure.cancel()
        self._discard_idle_composition()
        if self.draft is not None:
            raise PanelFrameError("Confirm or cancel the current preview first.")
        canvas = self.tool._canvas()
        if input_busy or panel_input_busy(canvas):
            raise PanelFrameError("finish the current stroke or text composition first")
        state = self.state()
        frame = read_frame_layer(state.panel_records[panel_id].metadata)
        if frame is None:
            raise PanelFrameError("convert this panel to a frame layer first")
        units = state.panel_contents.get(frame.content_group_id, ())
        if new or not units:
            layer_id = queue_panel_drawing(
                canvas, panel_id=panel_id, parent_id=frame.content_group_id
            )
            self.targets.remember(self.page_id(), panel_id, drawing_states(canvas)[layer_id])
        else:
            layer_id = self.targets.resolve(self.page_id(), panel_id, drawing_states(canvas))
            canvas.set_active_layer(layer_id)
        self.tool.selected_panel_id = panel_id
        self.tool._notify_panel()
        return layer_id

    def preview_split(self, panel_id, orientation, *, ratio=0.5, count=2):
        self.cancel()
        self.split = split_preview(
            self.state(),
            panel_id,
            orientation,
            page_size=self.tool._page_size(),
            ratio=ratio,
            count=count,
        )
        return self._install_split(self.split)

    def _install_split(self, preview):
        self.split = preview
        self.split.page_id = self.page_id()
        self.draft = PanelEditDraft(page_id=self.page_id(), label="Split panel and repair content")
        return self.split

    def confirm(self):
        if self.draft is None or self.preparation.task is not None:
            raise PanelFrameError("prepare a preview first")
        if self.split:
            self.draft.put("split", self.split.command())
        if self.repair:
            if self.repair.fixed_frame and not self.repair.content_changed():
                self.cancel()
                return True
            self.draft.put("repair", self.repair.command())
        if self.template:
            self.draft.put("template", self.template.command())
        paint_after = self.repair.paint_after if self.repair else None
        command = self.draft.command(page_id=self.page_id())
        ok = self.tool._submit(command)
        if not ok:
            raise PanelFrameError(command.last_error)
        self.cancel()
        if paint_after:
            self.paint_edge(*paint_after)
        return True

    def preview_resize(self, layout, *, fixed_frame=False, state=None):
        from .panel_repair_preview import PanelRepairPreview  # noqa: PLC0415

        self.cancel()
        repair = PanelRepairPreview(
            state if state is not None else self.state(),
            layout,
            page_size=self.tool._page_size(),
            fixed_frame=fixed_frame,
        )
        repair.geometry_command()
        return self._install_repair(repair)

    def _install_repair(self, repair):
        self.repair = repair
        self.draft = PanelEditDraft(
            page_id=self.page_id(), label="Resize panels and repair content"
        )
        from loomstroke.ui.canvas.panel_frame_render_preview import (  # noqa: PLC0415
            PanelFrameRenderPreview,
        )

        self._preview_canvas = self.tool._canvas()
        self.render_preview = PanelFrameRenderPreview(self.repair, self._preview_canvas)
        self._preview_canvas._panel_frame_render_preview = self.render_preview
        self.refresh_view()
        return self.repair

    def preview_incline(self, panel_id, edge_id, value):
        state = self.state()
        return self.preview_resize(
            self.inclined_layout(state, panel_id, edge_id, value), state=state
        )

    def inclined_layout(self, state, panel_id, edge_id, value):
        from .panel_convex_geometry import with_cut_skews  # noqa: PLC0415

        frame = read_frame_layer(state.panel_records[panel_id].metadata)
        if frame is None:
            raise PanelFrameError("convert this panel first")
        edge = next((e for e in frame.edges if e.edge_id == edge_id), None)
        if edge is None or edge.boundary_ref.kind != "cut":
            raise PanelFrameError("choose a split boundary to incline")
        ref = edge.boundary_ref

        def find(node):
            if node.node_id == ref.node_id:
                return node
            return next((found for child in node.children if (found := find(child))), None)

        node = find(state.layout.root)
        if node is None:
            raise PanelFrameError("split boundary node is missing")
        skews = list(node.cut_skews or (0,) * (len(node.children) - 1))
        skews[ref.index] = value
        return with_cut_skews(state.layout, ref.node_id, skews, page_size=self.tool._page_size())

    def cancel(self):
        self.vector_erasure.cancel()
        self.preparation.cancel()
        if self._preview_canvas is not None:
            if (
                getattr(self._preview_canvas, "_panel_frame_render_preview", None)
                is self.render_preview
            ):
                self._preview_canvas._panel_frame_render_preview = None
            self.render_preview = self._preview_canvas = None
            self.refresh_view()
        if self.draft:
            self.draft.cancel()
        self.draft = self.split = self.repair = self.template = None

    def refresh_view(self):
        widget = self.tool._canvas_widget()
        if widget is not None:
            widget.update()
            refresh = getattr(widget, "request_native_surface_refresh", None)
            if callable(refresh):
                # Qt layout/overlay paints can cover an unchanged swapchain.
                # Re-present the cached projection and flush the HWND region;
                # a normal update may skip present because its scene is warm.
                refresh()
            changed = getattr(self.tool._canvas(), "composited_changed", None)
            emit = getattr(changed, "emit", None)
            if callable(emit):
                from PySide6.QtGui import QPixmap  # noqa: PLC0415

                # Detached repairs do not recompose the document. Notify the
                # existing coalesced live-preview path after queuing display.
                emit(QPixmap())

    def preview_template(self, layout):
        from .panel_template_plan import PanelTemplatePreview  # noqa: PLC0415

        self.cancel()
        return self._install_template(
            PanelTemplatePreview(self.state(), layout, page_size=self.tool._page_size())
        )

    def _install_template(self, preview):
        self.template = preview
        preview.page_id = self.page_id()
        self.draft = PanelEditDraft(page_id=self.page_id(), label="Replace panel template")
        return preview

    def merge(self, primary_id, secondary_id):
        self._discard_idle_composition()
        if self.draft is not None:
            raise PanelFrameError("Confirm or cancel the current preview first.")
        if panel_input_busy(self.tool._canvas()):
            raise PanelFrameError("finish the current stroke or text composition first")
        from .panel_current_format import current_panels  # noqa: PLC0415

        state, conversions = current_panels(self.state(), page_size=self.tool._page_size())
        command = merge_panel_command(
            state, primary_id, secondary_id, page_size=self.tool._page_size()
        )
        return self.tool._submit(
            PanelEditCommand([*conversions, command], label=command.label)
            if conversions
            else command
        )

    def delete_panel(self, panel_id, disposition):
        from .panel_delete_plan import delete_panel_command  # noqa: PLC0415

        self._discard_idle_composition()
        if self.draft is not None:
            raise PanelFrameError("Confirm or cancel the current preview first.")
        if panel_input_busy(self.tool._canvas()):
            raise PanelFrameError("finish the current stroke or text composition first")
        command = delete_panel_command(
            self.state(), panel_id, page_size=self.tool._page_size(), disposition=disposition
        )
        if not self.tool._submit(command):
            raise PanelFrameError(command.last_error)
        remaining = self.state().reading_order
        self.tool.selected_panel_id = remaining[0] if remaining else None
        self.tool._notify_panel()
        return True

    def _discard_idle_composition(self):
        if (
            self.repair is not None
            and self.repair.fixed_frame
            and not self.repair.content_changed()
        ):
            self.cancel()

    def rasterize(self, panel_id, edge_id):
        return self.tool._submit(rasterize_edge_command(self.state(), panel_id, edge_id))

    def restore(self, panel_id, edge_id):
        from .panel_edge_restore_plan import restore_vector_edge_command  # noqa: PLC0415

        return self.tool._submit(restore_vector_edge_command(self.state(), panel_id, edge_id))

    def paint_edge(self, panel_id, edge_id):
        command = bake_edge_for_paint_command(self.state(), panel_id, edge_id)
        if command is not None and not self.tool._submit(command):
            return False
        frame = read_frame_layer(self.state().panel_records[panel_id].metadata)
        edge = next(e for e in frame.edges if e.edge_id == edge_id)
        self.tool._canvas().set_active_layer(edge.part_layer_id)
        return True

    def place_images(self, sources, panel_ids, *, policy="cover"):
        return self.tool._submit(
            place_images_command(self.state(), sources, panel_ids=panel_ids, policy=policy)
        )

    def attach_balloon(self, balloon_id, panel_id):
        from .panel_balloon_plan import attach_balloon_command  # noqa: PLC0415

        return self.tool._submit(attach_balloon_command(self.state(), balloon_id, panel_id))

    def wrap_selected_text(self, panel_id):
        from dataclasses import replace  # noqa: PLC0415

        from .panel_part_snapshot import part_snapshots  # noqa: PLC0415
        from .panel_text_balloon_plan import text_balloon_command  # noqa: PLC0415

        canvas = self.tool._canvas()
        if panel_input_busy(canvas):
            raise PanelFrameError("finish the current stroke or text composition first")
        selection = getattr(canvas, "_vector_selection", None)
        if (
            not isinstance(selection, dict)
            or not selection.get("id")
            or not selection.get("layer_id")
        ):
            raise PanelFrameError("Select a text object first.")
        layers = tuple(canvas._flatten_layers())
        state = replace(
            self.state(),
            panel_parts=part_snapshots(layers, include_layer_ids={layer.id for layer in layers}),
        )
        command, record = text_balloon_command(
            state, selection["layer_id"], selection["id"], panel_id
        )
        if not self.tool._submit(command):
            raise PanelFrameError(command.last_error)
        self.tool.selected_balloon_id = record.balloon_id
        self.tool._notify_panel()
        return record.balloon_id

    def display_current(self, panel_id, policy):
        from loomstroke.core.comic_panels.membership import whole_unit_source  # noqa: PLC0415

        from .panel_display_plan import display_content_command  # noqa: PLC0415

        canvas = self.tool._canvas()
        item_id = whole_unit_source(canvas._layer_tree(), canvas.current_layer().id)
        return self.tool._submit(display_content_command(self.state(), item_id, panel_id, policy))

    def independent_current(self, panel_id):
        from loomstroke.core.comic_panels.membership import SHARED_KEY  # noqa: PLC0415

        from .panel_shared_plan import independent_shared_view_command  # noqa: PLC0415

        canvas = self.tool._canvas()
        node = canvas.current_layer()
        while node and SHARED_KEY not in (node.metadata or {}):
            node = canvas._get_layer_by_id(node.parent_id) if node.parent_id else None
        if node is None:
            raise PanelFrameError("select a shared source first")
        return self.tool._submit(
            independent_shared_view_command(
                self.state(),
                node.id,
                panel_id,
                page_id=self.page_id(),
                page_size=self.tool._page_size(),
            )
        )
