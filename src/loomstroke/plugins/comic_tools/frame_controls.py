"""Frame controls and whole-content allocation, delegating all edits to workflow."""

from PySide6.QtCore import QEvent, QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QTableWidgetItem,
    QWidget,
)

from loomstroke.core.comic_panels.metadata import read_frame_layer
from loomstroke.core.comic_panels.models import PanelFrameError
from loomstroke.ui.translations import translated_literal_domain


def ts(source):
    return translated_literal_domain("ComicPanelFrames", source, use_fallback=True)


def _translated_item(source):
    item = QTableWidgetItem(ts(source))
    item.setData(Qt.ItemDataRole.UserRole, source)
    return item


def _allocation_item(reason):
    return _translated_item(
        {
            "manual": "Manually assigned",
            "unknown_bounds": "Bounds could not be determined",
            "outside": "Outside all child panels",
            "tie": "Equal overlap with child panels",
            "overlap": "Largest overlap with child panel",
        }.get(reason, reason)
    )


class FrameControls(QWidget):
    def __init__(self, workflow, parent=None):
        super().__init__(parent)
        self.workflow = workflow
        self._sources = []
        from .frame_controls_layout import build_frame_controls  # noqa: PLC0415

        build_frame_controls(self, ts)
        self.retranslate()
        self.repairs.refresh()
        self.guides = None

    def showEvent(self, event):
        super().showEvent(event)
        parent = self.workflow.tool._canvas_widget()
        if self.guides is None and parent is not None:
            from .frame_guides import FrameGuides  # noqa: PLC0415

            self.guides = FrameGuides(self, parent)

    def dispose(self):
        self._reveal_timer.stop()
        order_refresh = getattr(
            getattr(self.workflow.tool, "_panel_widget", None), "_order_refresh", None
        )
        if order_refresh is not None:
            order_refresh.dispose()
        self.workflow.vector_erasure.cancel()
        self.repairs.dispose()
        for task in self.workflow.image_tasks:
            task.cancel()
        if self.guides:
            self.guides.dispose()
            self.guides = None

    def panel_id(self):
        value = self.workflow.tool.selected_panel_id
        if not value:
            raise PanelFrameError("select a panel first")
        return value

    def edit_vector_edge(self):
        from .frame_vector_edit import edit_vector_edge  # noqa: PLC0415

        return edit_vector_edge(self.workflow, self.panel_id(), self.edge_id())

    def edge_id(self):
        frame = read_frame_layer(self.workflow.state().panel_records[self.panel_id()].metadata)
        if frame is None:
            raise PanelFrameError("convert this panel first")
        self.refresh_edges()
        value = self.edge.currentData()
        if value is None:
            raise PanelFrameError("choose a frame edge first")
        return value

    def refresh_confirmation(self):
        for button, source in self._sources:
            if source == "Cancel":
                button.setEnabled(self.workflow.draft is not None)
        from .frame_controls_layout import refresh_preview_context  # noqa: PLC0415

        refresh_preview_context(self, ts)

    def refresh_edges(self, state=None):
        self.refresh_confirmation()
        selected = self.edge.currentData()
        with QSignalBlocker(self.edge):
            self.edge.clear()
            panel_id = self.workflow.tool.selected_panel_id
            state = state if state is not None else self.workflow.tool.read_state()
            self._current_state = state
            if hasattr(self, "arrangement") and state:
                self.arrangement.refresh(state, panel_id)
            record = state.panel_records.get(panel_id) if state else None
            frame = read_frame_layer(record.metadata) if record else None
            if frame:
                for index, edge in enumerate(frame.edges):
                    if frame.geometry_mode == "layout_tree_convex":
                        label = (
                            ts("Page boundary")
                            if edge.boundary_ref.kind == "page"
                            else ts("Split boundary")
                        )
                        self.edge.addItem(f"{label} ({index+1})", edge.edge_id)
                        continue
                    side = edge.side or ("top", "right", "bottom", "left")[index % 4]
                    label = ts(
                        {"top": "Top", "right": "Right", "bottom": "Bottom", "left": "Left"}[side]
                    )
                    self.edge.addItem(f"{label} ({index+1})", edge.edge_id)
            index = self.edge.findData(selected)
            if index >= 0:
                self.edge.setCurrentIndex(index)
        from .frame_controls_layout import refresh_frame_context  # noqa: PLC0415

        refresh_frame_context(self, state, ts)

    def run(self, action):
        try:
            result = action()
            if result is False:
                raise PanelFrameError("command was rejected")
            self.status.setText("")
            self.refresh_edges()
            self.repairs.refresh()
            return result
        except PanelFrameError as exc:
            self.status.setText(ts("Frame operation failed: {reason}").format(reason=ts(str(exc))))
            self.status.show()
            return False

    def preview(self, orientation="vertical", *, ratio=0.5, count=2):
        from .panel_split_plan import split_preview  # noqa: PLC0415

        panel_id = self.panel_id()
        policy = self.split_policy.currentData()

        def prepare(state, layout, page_size):
            preview = split_preview(
                state, panel_id, orientation, page_size=page_size, ratio=ratio, count=count
            )
            for allocation in preview.allocations:
                preview.image_policies[allocation.item_id] = policy
            return preview.prepare()

        return self.workflow.preparation.start(
            self,
            self.workflow.state(),
            None,
            factory=prepare,
            installer=self._install_split,
            auto_commit=True,
        )

    def _install_split(self, preview):
        self.workflow._install_split(preview)
        frame = read_frame_layer(preview.state.panel_records[preview.original_panel_id].metadata)
        names = {
            u.layer_id: u.name for u in preview.state.panel_contents.get(frame.content_group_id, ())
        }
        self.table.setRowCount(len(preview.allocations))
        for row, allocation in enumerate(preview.allocations):
            self.table.setItem(row, 0, QTableWidgetItem(names[allocation.item_id]))
            combo = QComboBox()
            for child in preview.children:
                combo.addItem(child.panel_id, child.panel_id)
            combo.setCurrentIndex(combo.findData(allocation.panel_id))
            combo.currentIndexChanged.connect(
                lambda index, widget=combo, item_id=allocation.item_id: self.run(
                    lambda: preview.assign(item_id, widget.currentData())
                )
            )
            self.table.setCellWidget(row, 1, combo)
            policy = QComboBox()
            for value, source in (
                ("move", "Move"),
                ("copy", "Independent copy"),
                ("shared", "Shared page coordinates"),
            ):
                policy.addItem(ts(source), value)
            policy.currentIndexChanged.connect(
                lambda index, widget=policy, item_id=allocation.item_id: self.set_image_policy(
                    preview, item_id, widget.currentData()
                )
            )
            self.table.setCellWidget(row, 2, policy)
            self.table.setItem(row, 3, _allocation_item(allocation.reason))
        self.repairs.refresh()

    def preview_duplicate(self):
        from .panel_split_plan import split_preview  # noqa: PLC0415

        panel_id = self.panel_id()

        def prepare(state, layout, page_size):
            preview = split_preview(state, panel_id, "vertical", page_size=page_size)
            for allocation in preview.allocations:
                preview.image_policies[allocation.item_id] = "copy"
            return preview.prepare()

        return self.workflow.preparation.start(
            self,
            self.workflow.state(),
            None,
            factory=prepare,
            installer=self._install_copy_split,
            auto_commit=True,
        )

    def _install_copy_split(self, preview):
        self._install_split(preview)
        for row, allocation in enumerate(preview.allocations):
            preview.image_policies[allocation.item_id] = "copy"
            policy = self.table.cellWidget(row, 2)
            policy.setCurrentIndex(policy.findData("copy"))

    def preview_incline(self):
        state = self.workflow.state()
        layout = self.workflow.inclined_layout(
            state, self.panel_id(), self.edge.currentData(), self.incline.value()
        )
        self.workflow.preparation.start(self, state, layout, auto_commit=True)
        self.repairs.refresh()

    def cancel(self):
        self.workflow.cancel()
        self.table.setRowCount(0)
        self.repairs.refresh()

    def preview_template_incline(self):
        preview = self.workflow.template
        state = self.workflow.state()
        current = preview.layout if preview else state.layout
        if current.root.is_leaf:
            raise PanelFrameError("choose a split template first")
        from .panel_convex_geometry import with_cut_skews  # noqa: PLC0415

        layout = with_cut_skews(
            current,
            current.root.node_id,
            (self.incline.value(),) * (len(current.root.children) - 1),
            page_size=self.workflow.tool._page_size(),
        )
        if preview:
            return self.preview_template(layout, overrides=preview.overrides)
        return self.workflow.preparation.start(self, state, layout, auto_commit=True)

    def preview_template(self, layout, *, overrides=None):
        from .frame_preview_preparation import prepared_template  # noqa: PLC0415

        return self.workflow.preparation.start(
            self,
            self.workflow.state(),
            layout,
            factory=prepared_template,
            installer=lambda preview: self._install_template(preview, overrides),
            auto_commit=True,
        )

    def _install_template(self, preview, overrides):
        self.workflow._install_template(preview)
        for item_id, panel_id in (overrides or {}).items():
            preview.assign(item_id, panel_id)
        names = {unit.layer_id: unit.name for unit in preview.units}
        self.table.setRowCount(len(preview.allocations))
        for row, allocation in enumerate(preview.allocations):
            self.table.setItem(row, 0, QTableWidgetItem(names[allocation.item_id]))
            combo = QComboBox()
            for child in preview.children:
                combo.addItem(child.panel_id, child.panel_id)
            combo.setCurrentIndex(combo.findData(allocation.panel_id))
            combo.currentIndexChanged.connect(
                lambda index, widget=combo, item_id=allocation.item_id: self.run(
                    lambda: preview.assign(item_id, widget.currentData())
                )
            )
            self.table.setCellWidget(row, 1, combo)
            self.table.setCellWidget(row, 2, None)
            self.table.setItem(row, 2, _translated_item("Preserve page placement"))
            self.table.setItem(row, 3, _allocation_item(allocation.reason))
        self.repairs.refresh()

    def set_image_policy(self, preview, item_id, policy):
        from .panel_split_shared_plan import shared_split_reason  # noqa: PLC0415

        preview.image_policies[item_id] = policy
        reason = shared_split_reason(preview)
        self.status.setText(
            ts("Frame operation failed: {reason}").format(reason=ts(reason)) if reason else ""
        )
        self.status.setVisible(bool(reason))

    def retranslate(self):
        from .frame_controls_layout import ACTION_LABELS  # noqa: PLC0415

        self.erase_width.setToolTip(ts("Vector edge erase width"))
        for widget, source in self._sources:
            widget.setText(ts(ACTION_LABELS.get(source, source)))
            widget.setToolTip(ts(ACTION_LABELS.get(source, source)))
        for section in self.sections.values():
            section.retranslate()
        self.refresh_edges()
        self.table.setHorizontalHeaderLabels(
            [ts(s) for s in ("Item", "Destination", "Image handling", "Reason")]
        )
        for combo, sources in (
            (self.fit, ("Cover panel", "Fit whole image")),
            (self.distribution, ("All images in this panel", "Distribute in reading order")),
            (self.split_policy, ("Move", "Independent copy", "Shared page coordinates")),
        ):
            with QSignalBlocker(combo):
                for index, source in enumerate(sources):
                    combo.setItemText(index, ts(source))
        for row in range(self.table.rowCount()):
            for column in (2, 3):
                item = self.table.item(row, column)
                source = item.data(Qt.ItemDataRole.UserRole) if item else None
                if source:
                    item.setText(ts(source))
            combo = self.table.cellWidget(row, 2)
            if combo:
                with QSignalBlocker(combo):
                    for index, source in enumerate(
                        ("Move", "Independent copy", "Shared page coordinates")
                    ):
                        combo.setItemText(index, ts(source))

    def changeEvent(self, event):
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
            self.repairs.refresh()
        super().changeEvent(event)
