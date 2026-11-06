"""Pose UI and explicit OpenPose export for a selected drawing figure layer."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from loomstroke.core.channels.channel_pixel_policy import ChannelPixelPolicy
from loomstroke.core.drawing_figures.commands import SetDrawingFigurePoseCommand
from loomstroke.core.drawing_figures.hand_joints import HAND_JOINTS, hand_side
from loomstroke.core.drawing_figures.model import JOINTS, pose_from_layer
from loomstroke.core.drawing_figures.openpose_command import AddFigureOpenPoseLayerCommand
from loomstroke.core.drawing_figures.posing import reset_pose_part, toggle_pin
from loomstroke.core.drawing_figures.presets import PRESETS, preset_pose
from loomstroke.core.drawing_figures.rotation_posing import rotate_joint
from loomstroke.core.layer_3d_rendering import orbit_camera_state
from loomstroke.rendering_3d.figure_pose_export import pose_image, pose_json

from .drawing_figure_add import figure_text as tr
from .drawing_figure_hand_editor import open_hand_editor
from .drawing_figure_pose_preset_controls import DrawingFigurePosePresetControls
from .drawing_figure_translations import ROTATION_HINT
from .translations import TranslationBinder


class DrawingFigureToolbarWidget(QWidget):
    def __init__(  # noqa: PLR0915 - widget layout construction
        self, controller: Any, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self._syncing = False
        interaction = self._figure_interaction()
        if interaction is not None:
            interaction.selection_changed.connect(self._selection_changed)
        self._translation_binder = TranslationBinder(tr)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        top, bottom, export = QHBoxLayout(), QHBoxLayout(), QHBoxLayout()
        layout.addLayout(top)
        layout.addLayout(bottom)
        layout.addLayout(export)
        self.pose_button = QPushButton(self)
        self.pose_button.setCheckable(True)
        self._bind(self.pose_button.setText, "Edit pose")
        self.pose_button.toggled.connect(self._toggle_mode)
        top.addWidget(self.pose_button)
        self.hand_button = QPushButton(self)
        self._bind(self.hand_button.setText, "Edit selected hand")
        self.hand_button.clicked.connect(lambda: open_hand_editor(self))
        top.addWidget(self.hand_button)
        self.presets = QComboBox(self)
        for key, source in PRESETS.items():
            self.presets.addItem(tr(source), key)
        top.addWidget(self.presets)
        apply_button = QPushButton(self)
        self._bind(apply_button.setText, "Apply pose")
        apply_button.clicked.connect(self.apply_preset)
        top.addWidget(apply_button)
        self.reset_button = QPushButton(self)
        self._bind(self.reset_button.setText, "Reset selected part")
        self.reset_button.clicked.connect(self.reset_selected_part)
        top.addWidget(self.reset_button)
        self.assist = QCheckBox(self)
        self._bind(self.assist.setText, "Pose assistance")
        self.assist.toggled.connect(self.set_assistance)
        top.addWidget(self.assist)
        self.pin_button = QPushButton(self)
        self._bind(self.pin_button.setText, "Pin / unpin joint")
        self.pin_button.clicked.connect(self.pin_selected)
        layer_button = QPushButton(self)
        self._bind(layer_button.setText, "OpenPose layer")
        layer_button.clicked.connect(self.add_openpose_layer)
        export.addWidget(layer_button)
        self.user_pose_presets = DrawingFigurePosePresetControls(self, self)
        export.addWidget(self.user_pose_presets)
        json_button = QPushButton("OpenPose JSON", self)
        json_button.clicked.connect(lambda: self.export_pose("json"))
        export.addWidget(json_button)
        self.joints = QComboBox(self)
        for key in JOINTS:
            self.joints.addItem(tr(key.replace("_", " ").title()), key)
        self.joints.currentIndexChanged.connect(self.select_joint)
        bottom.addWidget(self.joints)
        self.angles: list[QDoubleSpinBox] = []
        for axis in ("X", "Y", "Z"):
            bottom.addWidget(QLabel(axis, self))
            spin = QDoubleSpinBox(self)
            spin.setRange(-170, 170)
            spin.setSuffix("°")
            spin.setSingleStep(5)
            spin.editingFinished.connect(self.change_angles)
            bottom.addWidget(spin)
            self.angles.append(spin)
        bottom.addWidget(self.pin_button)
        tip = QLabel(self)
        tip.setWordWrap(True)
        tip.setMaximumWidth(580)
        self._bind(tip.setText, ROTATION_HINT)
        layout.addWidget(tip)

    def _bind(self, setter: Any, source: str) -> None:
        self._translation_binder.register(setter, source)

    def _selection_changed(self, _joint: str) -> None:
        self.sync_from_layer(self._layer())

    def _toggle_mode(self, enabled: bool) -> None:
        if not self._syncing:
            self.controller.set_interaction_mode("pose" if enabled else "orbit")

    def _layer(self) -> Any:
        return self.controller._active_3d_layer()

    def _figure_interaction(self) -> Any:
        return getattr(self.controller._interaction_controller(), "figure", None)

    def sync_from_layer(self, layer: Any) -> None:
        pose = pose_from_layer(layer)
        self.setVisible(pose is not None)
        if pose is None:
            return
        self._syncing = True
        try:
            self.setEnabled(not bool(getattr(layer, "locked", False)))
            self.assist.setChecked(pose.assistance > 0)
            self.pose_button.setChecked(self.controller.interaction_mode() == "pose")
            interaction = self._figure_interaction()
            joint = interaction.selected_joint if interaction else str(self.joints.currentData())
            self.hand_button.setEnabled(hand_side(joint) is not None)
            hand_joint = joint in HAND_JOINTS
            if hand_joint:
                joint = f"{hand_side(joint)}_wrist"
            self.joints.setCurrentIndex(self.joints.findData(joint))
            for spin, value in zip(self.angles, pose.rotations.get(joint, (0, 0, 0)), strict=True):
                spin.setEnabled(not hand_joint)
                spin.setValue(value)
            self.pin_button.setEnabled(joint.endswith(("wrist", "ankle")))
        finally:
            self._syncing = False
        editor = getattr(self.controller, "_figure_hand_editor", None)
        if editor is not None:
            editor.sync()
        self.user_pose_presets.refresh()

    def _submit(self, pose: Any) -> bool:
        layer = self._layer()
        if layer is None or pose_from_layer(layer) is None:
            return False
        interaction = self._figure_interaction()
        if interaction is not None:
            interaction.cancel()
        ok = bool(self.controller._canvas.submit(SetDrawingFigurePoseCommand(layer.id, pose)))
        self.sync_from_layer(layer)
        return ok

    def apply_preset(self) -> bool:
        current = pose_from_layer(self._layer())
        return bool(
            current
            and self._submit(
                replace(
                    preset_pose(current.model, str(self.presets.currentData())),
                    hand_rotations=current.hand_rotations,
                )
            )
        )

    def set_assistance(self, enabled: bool) -> None:
        if not self._syncing:
            pose = pose_from_layer(self._layer())
            if pose is not None:
                self._submit(replace(pose, assistance=0.5 if enabled else 0.0))

    def reset_selected_part(self) -> bool:
        layer = self._layer()
        joint = str(self.joints.currentData())
        if layer is None or getattr(layer, "locked", False) or joint not in JOINTS:
            return False
        interaction = self._figure_interaction()
        if interaction is not None:
            interaction.cancel()
        pose = pose_from_layer(layer)
        return bool(pose is not None and self._submit(reset_pose_part(pose, joint)))

    def select_joint(self, _index: int) -> None:
        if self._syncing:
            return
        interaction = self._figure_interaction()
        if interaction is not None:
            interaction.select_joint(str(self.joints.currentData()))
        self.sync_from_layer(self._layer())

    def pin_selected(self) -> None:
        pose = pose_from_layer(self._layer())
        if pose is not None:
            self._submit(toggle_pin(pose, str(self.joints.currentData())))

    def change_angles(self) -> None:
        if self._syncing:
            return
        pose = pose_from_layer(self._layer())
        if pose is not None:
            joint = str(self.joints.currentData())
            self._submit(rotate_joint(pose, joint, tuple(spin.value() for spin in self.angles)))

    def _export_payload(self) -> Any:
        interaction = self._figure_interaction()
        if interaction is not None:
            interaction.cancel()
        layer = self._layer()
        pose = pose_from_layer(layer)
        if pose is None:
            return None
        width, height = self.controller._canvas.get_canvas_size()
        return pose_json(
            pose,
            layer.scene_3d,
            orbit_camera_state(layer.camera_3d, layer.scene_3d),
            width,
            height,
            offset=(getattr(layer, "offset_x", 0), getattr(layer, "offset_y", 0)),
            include_details=True,
        )

    def add_openpose_layer(self) -> bool:
        payload = self._export_payload()
        if payload is None:
            return False
        pixels = ChannelPixelPolicy.pixels(pose_image(payload), straight=True)
        return bool(self.controller._canvas.submit(AddFigureOpenPoseLayerCommand(pixels)))

    def export_pose(self, kind: str) -> bool:
        if kind != "json":
            return False
        payload = self._export_payload()
        if payload is None:
            return False
        path, _selected = QFileDialog.getSaveFileName(
            self,
            tr("Export OpenPose"),
            f"pose.{kind}",
            "JSON (*.json)",
        )
        if not path:
            return False
        try:
            Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
            return True
        except (OSError, ValueError) as error:
            QMessageBox.warning(
                self, tr("Export OpenPose"), tr("Could not export OpenPose") + "\n" + str(error)
            )
            return False

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self._translation_binder.retranslate()
            for index, source in enumerate(PRESETS.values()):
                self.presets.setItemText(index, tr(source))
            for index, key in enumerate(JOINTS):
                self.joints.setItemText(index, tr(key.replace("_", " ").title()))
