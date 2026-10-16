"""Boundary and content choices committed when each operation completes."""

from dataclasses import replace

from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from loomstroke.core.comic_panels.models import PanelFrameError

from .frame_controls import ts

_DISPLAY_SOURCES = {
    "Keep edited clip (stop automatic updates)": "Keep edited clip",
    "Regenerate clip from panel region": "Regenerate clip",
    "Adjust selected content on canvas": "Adjust on canvas",
    "Preview boundary change": "Change panel gutter",
    "Confirm and paint this edge": "Paint this edge",
}


class FrameRepairControls(QWidget):
    def __init__(self, controls):
        super().__init__(controls)
        self.controls = controls
        self.workflow = controls.workflow
        from .frame_repair_actions import FrameRepairActions  # noqa: PLC0415

        self.actions = FrameRepairActions(controls)
        self.workflow.lifecycle.on_discard = self.refresh
        self._sources = []
        self._labels = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        fields = QFormLayout()
        fields.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        layout.addLayout(fields)
        self.gutter = QDoubleSpinBox()
        self.gutter.setRange(0, 1000)
        self.gutter.setSuffix(" px")
        self._field(fields, "Panel gutter", self.gutter)
        self._button(layout, "Preview boundary change", self.preview)
        self.gutter.lineEdit().returnPressed.connect(lambda: self.controls.run(self.preview))
        self.items = QComboBox()
        self.items.currentIndexChanged.connect(self.selection_changed)
        self._field(fields, "Content to repair", self.items)
        self.clip_panels = QComboBox()
        self._field(fields, "Clip region", self.clip_panels)
        self._button(
            layout, "Keep edited clip (stop automatic updates)", lambda: self.clip_policy(False)
        )
        self._button(layout, "Regenerate clip from panel region", lambda: self.clip_policy(True))
        row = QHBoxLayout()
        for source, action in (
            ("Cover panel", lambda: self.fit("cover")),
            ("Fit whole image", lambda: self.fit("contain")),
        ):
            self._button(row, source, action)
        layout.addLayout(row)
        self._button(layout, "Center in panel", self.center)
        self.direct = self._button(layout, "Adjust selected content on canvas", self.toggle_canvas)
        self.direct.setCheckable(True)
        self.direct.setToolTip(ts("Drag to move or scale. Release to apply. Esc cancels the drag."))
        self.edges = QComboBox()
        self._field(fields, "Frame line to repair", self.edges)
        row = QHBoxLayout()
        self._button(row, "Keep source pixels", lambda: self.policy("keep_pixels"))
        self._button(row, "Stretch along edge", lambda: self.policy("stretch_tangent"))
        layout.addLayout(row)
        self._button(layout, "Confirm and paint this edge", self.confirm_and_paint)

    def _field(self, form, source, field):
        label = QLabel(ts(source))
        label.setWordWrap(True)
        label.setBuddy(field)
        self._labels.append((label, source))
        form.addRow(label, field)

    def _button(self, layout, source, action):
        button = QPushButton(ts(_DISPLAY_SOURCES.get(source, source)))
        button.setToolTip(ts(_DISPLAY_SOURCES.get(source, source)))
        self._sources.append((button, source))
        button.clicked.connect(lambda checked=False: self.controls.run(action))
        layout.addWidget(button)
        return button

    def toggle_canvas(self):
        if self.direct.isChecked() and self.workflow.repair is None:
            return self.adjust_image(self.items.currentData())
        if hasattr(self, "overlay"):
            try:
                self.overlay.gesture.enable(self.direct.isChecked())
            except PanelFrameError:
                self.overlay.gesture.enable(False)
                with QSignalBlocker(self.direct):
                    self.direct.setChecked(False)
                raise

    def adjust_image(self, item_id):
        if self.workflow.draft is not None and self.workflow.repair is None:
            raise PanelFrameError("Confirm or cancel the current preview first.")
        if self.workflow.repair is None:
            state = self.workflow.state()
            self.workflow.preview_resize(state.layout, fixed_frame=True, state=state)
        self.refresh()
        index = self.items.findData(item_id)
        if index < 0:
            raise PanelFrameError("choose an imported image to adjust its composition")
        self.items.setCurrentIndex(index)
        self.direct.setChecked(True)
        self.toggle_canvas()

    def selection_changed(self, _index):
        if hasattr(self, "overlay"):
            self.overlay.gesture.drag = None
            if self.overlay.gesture.enabled:
                self.controls.run(self.toggle_canvas)
            self.overlay.update()

    def preview(self):
        state = self.workflow.state()
        self.workflow.preparation.start(
            self.controls,
            state,
            replace(state.layout, gutter_px=self.gutter.value()),
            auto_commit=True,
        )
        self.refresh()

    def fit(self, policy):
        return self.actions.apply("fit", self.items.currentData(), policy)

    def center(self):
        return self.actions.apply("center", self.items.currentData())

    def policy(self, value):
        return self.actions.apply("edge", self.edges.currentData(), value)

    def clip_policy(self, regenerate):
        return self.actions.apply("clip", self.clip_panels.currentData(), regenerate)

    def confirm_and_paint(self):
        repair = self.actions.query()
        edge_id = self.edges.currentData()
        panel_id = repair.edge_items[edge_id][0]
        return self.workflow.paint_edge(panel_id, edge_id)

    def _button_enabled(self, source, repair):
        if source == "Preview boundary change":
            return not (repair and repair.fixed_frame)
        if source in {
            "Keep edited clip (stop automatic updates)",
            "Regenerate clip from panel region",
        }:
            return bool(repair and self.clip_panels.count())
        count = (
            self.edges.count()
            if "edge" in source or source == "Keep source pixels"
            else self.items.count()
        )
        return bool(repair and count)

    def refresh(self):
        self.controls.refresh_confirmation()
        self.workflow.refresh_view()
        repair = self.actions.query()
        self._refresh_clips(repair)
        if self.workflow.repair is None:
            with QSignalBlocker(self.direct):
                self.direct.setChecked(False)
        self.direct.setToolTip(ts("Drag to move or scale. Release to apply. Esc cancels the drag."))
        for widget in (self.items, self.edges):
            selected = widget.currentData()
            with QSignalBlocker(widget):
                widget.clear()
                if repair:
                    if widget is self.items:
                        for item in repair.diagnostics():
                            unit = repair.items[item.item_id][1]
                            widget.addItem(f"{unit.name}: {ts(item.status)}", item.item_id)
                    else:
                        for edge_id, status, amount in repair.edge_diagnostics():
                            widget.addItem(f"{edge_id}: {ts(status)} {amount:.1f} px", edge_id)
                index = widget.findData(selected)
                if index >= 0:
                    widget.setCurrentIndex(index)
        for button, source in self._sources:
            button.setText(ts(_DISPLAY_SOURCES.get(source, source)))
            button.setToolTip(ts(_DISPLAY_SOURCES.get(source, source)))
            button.setEnabled(self._button_enabled(source, repair))
        for label, source in self._labels:
            label.setText(ts(source))
        self._refresh_canvas_views(repair)

    def _refresh_canvas_views(self, repair):
        parent = self.workflow.tool._canvas_widget()
        self.direct.setEnabled(bool(parent is not None and repair and self.items.count()))
        if parent is not None:
            from .frame_preview_overlay import FramePreviewOverlay  # noqa: PLC0415

            if not hasattr(self, "overlay"):
                self.overlay = FramePreviewOverlay(self.workflow, parent, self)
            self.overlay.refresh()
        guides = getattr(self.controls, "guides", None)
        if guides is not None:
            guides.schedule_refresh()

    def _refresh_clips(self, repair):
        selected_clip = self.clip_panels.currentData()
        with QSignalBlocker(self.clip_panels):
            self.clip_panels.clear()
            if repair:
                for panel_id in repair.authored_clip_panels:
                    source = (
                        "Regenerate clip from panel region"
                        if panel_id in repair.regenerated_clips
                        else "Keep edited clip (stop automatic updates)"
                    )
                    self.clip_panels.addItem(f"{panel_id}: {ts(source)}", panel_id)
            index = self.clip_panels.findData(selected_clip)
            if index >= 0:
                self.clip_panels.setCurrentIndex(index)

    def dispose(self):
        self.workflow.lifecycle.dispose()
        if hasattr(self, "overlay"):
            self.overlay.deleteLater()
