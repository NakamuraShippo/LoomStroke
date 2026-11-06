"""Modeless editor bound to one figure layer and one selected hand."""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from PySide6.QtCore import QEvent, QMetaObject, QObject, QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid as is_valid

from loomstroke.core.drawing_figures.commands import SetDrawingFigurePoseCommand
from loomstroke.core.drawing_figures.hand_joints import FINGERS, HAND_JOINTS, hand_side
from loomstroke.core.drawing_figures.hand_posing import (
    HAND_PRESETS,
    cup_hand,
    hand_preset,
    reset_hand,
    rotate_hand_selection,
    set_finger,
)
from loomstroke.core.drawing_figures.hand_presets import PRESET_CATEGORIES, PRESET_DEFINITIONS
from loomstroke.core.drawing_figures.model import pose_from_layer

from .canvas.guide_canvas_bridge import update_guide_overlay
from .drawing_figure_add import figure_text as tr
from .translations import TranslationBinder

FINGER_LABELS = {
    "thumb": "Thumb",
    "index": "Index finger",
    "middle": "Middle finger",
    "ring": "Ring finger",
    "little": "Little finger",
    "palm": "Palm",
    "wrist": "Wrist",
}
PRESET_LABELS = {key: value.label for key, value in PRESET_DEFINITIONS.items()}
CATEGORY_LABELS = {"all": "All hand poses", **PRESET_CATEGORIES}


class DrawingFigureHandEditor(QDialog):
    def __init__(
        self, controller: Any, layer: Any, side: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent, Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.controller, self.target, self.side = controller, layer, side
        self._syncing = False
        self._finished = False
        self._connections: list[QMetaObject.Connection] = []
        self._binder = TranslationBinder(tr)
        self._binder.register(self.setWindowTitle, "Hand pose")
        layout = QVBoxLayout(self)
        self.side_label = QLabel(tr("Left Wrist" if side == "left" else "Right Wrist"), self)
        layout.addWidget(self.side_label)
        row = QHBoxLayout()
        layout.addLayout(row)
        self.category = QComboBox(self)
        for value, source in CATEGORY_LABELS.items():
            self.category.addItem(tr(source), value)
        row.addWidget(self.category)
        self.presets = QComboBox(self)
        for value in HAND_PRESETS:
            self.presets.addItem(tr(PRESET_LABELS[value]), value)
        row.addWidget(self.presets)
        self.presets.setMaxVisibleItems(12)
        self.category.currentIndexChanged.connect(self._filter_presets)
        self.apply_button = self._button("Apply pose", self.apply_preset)
        row.addWidget(self.apply_button)
        row.addWidget(self._button("Reset hand", self.reset_hand))
        grid = QGridLayout()
        layout.addLayout(grid)
        for column, source in enumerate(("Finger", "Finger curl", "Finger spread")):
            label = QLabel(self)
            self._binder.register(label.setText, source)
            grid.addWidget(label, 0, column)
        self.fingers: dict[str, tuple[QDoubleSpinBox, QDoubleSpinBox]] = {}
        for index, finger in enumerate(FINGERS, 1):
            label = QLabel(self)
            self._binder.register(label.setText, FINGER_LABELS[finger])
            grid.addWidget(label, index, 0)
            curl, spread = self._spin(-30, 130), self._spin(-45, 45)
            self.fingers[finger] = curl, spread
            grid.addWidget(curl, index, 1)
            grid.addWidget(spread, index, 2)
            for spin in (curl, spread):
                spin.editingFinished.connect(lambda finger=finger: self.change_finger(finger))
        label = QLabel(self)
        self._binder.register(label.setText, "Cup palm")
        grid.addWidget(label, 6, 0)
        self.cup = self._spin(-40, 40)
        self.cup.editingFinished.connect(self.change_palm)
        grid.addWidget(self.cup, 6, 1)
        self._build_details(layout)
        self._bind_runtime()
        self.sync()

    def _bind_runtime(self) -> None:
        figure = self._figure()
        self._bound_figure = figure
        if figure is not None:
            connection = figure.selection_changed.connect(self._selection_changed)
            if isinstance(connection, QMetaObject.Connection):
                self._connections.append(connection)
            self._selection_changed(figure.selected_joint)
        self._signal: Any = getattr(self.controller._canvas, "composited_changed", None)
        if self._signal is not None and callable(getattr(self._signal, "connect", None)):
            connection = self._signal.connect(self.sync)
            if isinstance(connection, QMetaObject.Connection):
                self._connections.append(connection)
        self.finished.connect(self._finish)
        self.destroyed.connect(lambda _object=None: self._finish())

    def _build_details(self, layout: QVBoxLayout) -> None:
        details = QHBoxLayout()
        layout.addLayout(details)
        self.finger = QComboBox(self)
        for key, source in FINGER_LABELS.items():
            self.finger.addItem(tr(source), key)
        details.addWidget(self.finger)
        label = QLabel(self)
        self._binder.register(label.setText, "Joint")
        details.addWidget(label)
        self.segment = QSpinBox(self)
        self.segment.setRange(1, 3)
        details.addWidget(self.segment)
        self.angles: list[QDoubleSpinBox] = []
        for axis in ("X", "Y", "Z"):
            details.addWidget(QLabel(axis, self))
            spin = self._spin(-170, 170)
            spin.editingFinished.connect(self.change_angles)
            details.addWidget(spin)
            self.angles.append(spin)
        self.finger.currentIndexChanged.connect(self._select_detail)
        self.segment.valueChanged.connect(self._select_detail)

    def _button(self, source: str, callback: Any) -> QPushButton:
        button = QPushButton(self)
        self._binder.register(button.setText, source)
        button.clicked.connect(callback)
        return button

    def _filter_presets(self, *_args: Any) -> None:
        selected = self.presets.currentData()
        category = self.category.currentData()
        with QSignalBlocker(self.presets):
            self.presets.clear()
            for key, preset in PRESET_DEFINITIONS.items():
                if category in ("all", preset.category):
                    self.presets.addItem(tr(preset.label), key)
            index = self.presets.findData(selected)
            self.presets.setCurrentIndex(max(0, index))

    def _spin(self, low: float, high: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox(self)
        spin.setRange(low, high)
        spin.setSuffix("°")
        spin.setSingleStep(5)
        spin.setKeyboardTracking(False)
        return spin

    def _figure(self) -> Any:
        return getattr(self.controller._interaction_controller(), "figure", None)

    def _pose(self) -> Any:
        if self._finished:
            return None
        canvas_getter = getattr(self.controller._canvas_widget, "_get_canvas_service", None)
        if callable(canvas_getter) and canvas_getter() is not self.controller._canvas:
            return None
        layer = self.controller._active_3d_layer()
        if layer is not self.target or getattr(layer, "locked", False):
            return None
        return pose_from_layer(layer)

    def sync(self, *_args: Any) -> None:
        if not is_valid(self):
            return
        pose = self._pose()
        self.setEnabled(pose is not None)
        if pose is None:
            return
        self._syncing = True
        try:
            for finger, (curl, spread) in self.fingers.items():
                values = [
                    pose.hand_rotations.get(f"{self.side}_{finger}_{n}", (0, 0, 0))
                    for n in (1, 2, 3)
                ]
                curl.setValue(sum(value[0] for value in values) / 3)
                spread.setValue(values[0][2])
            self.cup.setValue(pose.hand_rotations.get(f"{self.side}_palm_4", (0, 0, 0))[0])
            joint = self._detail_joint()
            values = pose.rotations if joint.endswith("wrist") else pose.hand_rotations
            for spin, value in zip(self.angles, values.get(joint, (0, 0, 0)), strict=True):
                spin.setValue(value)
        finally:
            self._syncing = False

    def _detail_joint(self) -> str:
        finger = str(self.finger.currentData())
        return (
            f"{self.side}_wrist"
            if finger == "wrist"
            else f"{self.side}_{finger}_{self.segment.value()}"
        )

    def _selection_changed(self, joint: str) -> None:
        if hand_side(joint) == self.side:
            finger, number = (
                (joint.split("_")[1], joint.split("_")[2])
                if joint in HAND_JOINTS
                else ("wrist", "1")
            )
            self._syncing = True
            self.finger.setCurrentIndex(self.finger.findData(finger))
            self.segment.setRange(1, 4 if finger == "palm" else 3)
            self.segment.setValue(int(number))
            self.segment.setEnabled(finger != "wrist")
            self._syncing = False
        self.sync()

    def _select_detail(self, *_args: Any) -> None:
        if self._syncing:
            return
        finger = str(self.finger.currentData())
        self._syncing = True
        self.segment.setRange(1, 4 if finger == "palm" else 3)
        self.segment.setEnabled(finger != "wrist")
        if finger == "wrist":
            self.segment.setValue(1)
        self._syncing = False
        figure = self._figure()
        if figure is not None and self._pose() is not None:
            figure.select_joint(self._detail_joint())
            figure.hand_editor_active = True
        self.sync()

    def _apply(self, operation: Any) -> bool:
        if self._syncing or self._pose() is None:
            return False
        figure = self._figure()
        if figure is not None:
            figure.cancel()
        pose = self._pose()
        if pose is None:
            return False
        result = bool(
            self.controller._canvas.submit(
                SetDrawingFigurePoseCommand(self.target.id, operation(pose))
            )
        )
        self.sync()
        update_guide_overlay(self.controller._canvas_widget)
        return result

    def apply_preset(self) -> bool:
        preset = str(self.presets.currentData())
        return self._apply(lambda pose: hand_preset(pose, self.side, preset))

    def reset_hand(self) -> bool:
        return self._apply(lambda pose: reset_hand(pose, self.side))

    def change_finger(self, finger: str) -> bool:
        curl, spread = self.fingers[finger]
        curl_value, spread_value = curl.value(), spread.value()
        return self._apply(
            lambda pose: set_finger(pose, self.side, finger, curl_value, spread_value)
        )

    def change_palm(self) -> bool:
        degrees = self.cup.value()
        return self._apply(lambda pose: cup_hand(pose, self.side, degrees))

    def change_angles(self) -> bool:
        x, y, z = (spin.value() for spin in self.angles)
        joint = self._detail_joint()
        return self._apply(lambda pose: rotate_hand_selection(pose, joint, (x, y, z)))

    def _finish(self, *_args: Any) -> None:
        if self._finished:
            return
        self._finished = True
        if getattr(self.controller, "_figure_hand_editor", None) is self:
            self.controller._figure_hand_editor = None
        figure = self._bound_figure
        if figure is not None:
            figure.hand_editor_active = False
            with suppress(RuntimeError):
                figure.cancel()
            with suppress(RuntimeError):
                update_guide_overlay(self.controller._canvas_widget)
        for connection in self._connections:
            QObject.disconnect(connection)
        self._connections.clear()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self._binder.retranslate()
            self.side_label.setText(tr("Left Wrist" if self.side == "left" else "Right Wrist"))
            for combo, labels in (
                (self.presets, PRESET_LABELS),
                (self.finger, FINGER_LABELS),
                (self.category, CATEGORY_LABELS),
            ):
                for index in range(combo.count()):
                    combo.setItemText(index, tr(labels[str(combo.itemData(index))]))


def open_hand_editor(toolbar: Any) -> DrawingFigureHandEditor | None:
    controller = toolbar.controller
    figure = toolbar._figure_interaction()
    joint = figure.selected_joint if figure is not None else str(toolbar.joints.currentData())
    side, layer = hand_side(joint), toolbar._layer()
    if side is None or pose_from_layer(layer) is None or getattr(layer, "locked", False):
        return None
    old = getattr(controller, "_figure_hand_editor", None)
    if isinstance(old, DrawingFigureHandEditor) and is_valid(old):
        if old.target is layer and old.side == side:
            if figure is not None:
                figure.cancel()
                figure.select_joint(joint)
                figure.hand_editor_active = True
            old.show()
            old.raise_()
            old.activateWindow()
            return old
        old.close()
    widget = controller._canvas_widget
    parent = widget.window() if isinstance(widget, QWidget) else None
    if figure is not None:
        figure.cancel()
        figure.select_joint(joint)
        figure.hand_editor_active = True
    editor = DrawingFigureHandEditor(controller, layer, side, parent)
    controller._figure_hand_editor = editor
    editor.show()
    return editor
