"""Select a destination slot and prepare an undoable panel-position exchange."""

from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import QComboBox, QFormLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .frame_controls import ts
from .panel_arrangement_plan import arrangement_preview


class FrameArrangementControls(QWidget):
    def __init__(self, controls):
        super().__init__(controls)
        self.controls = controls
        self.targets = QComboBox()
        self.button = QPushButton(ts("Exchange panel positions"))
        self.button.clicked.connect(lambda checked=False: controls.run(self.preview))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        layout.addLayout(form)
        self.target_label = QLabel(ts("Exchange with"))
        self.target_label.setBuddy(self.targets)
        form.addRow(self.target_label, self.targets)
        layout.addWidget(self.button)

    def refresh(self, state, selected):
        target = self.targets.currentData()
        with QSignalBlocker(self.targets):
            self.targets.clear()
            for pid in state.panel_records:
                if pid != selected:
                    self.targets.addItem(pid, pid)
            index = self.targets.findData(target)
            if index >= 0:
                self.targets.setCurrentIndex(index)
        self.button.setText(ts("Exchange panel positions"))
        self.target_label.setText(ts("Exchange with"))
        self.button.setEnabled(bool(self.targets.count() and selected in state.panel_records))

    def preview(self):
        workflow = self.controls.workflow
        state = workflow.state()
        return workflow.preparation.start(
            self.controls,
            state,
            (self.controls.panel_id(), self.targets.currentData()),
            factory=arrangement_preview,
            auto_commit=True,
        )
