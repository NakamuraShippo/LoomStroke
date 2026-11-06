"""Non-printing figure handles and cancellable, one-command pose drags."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from typing import Any

import numpy as np
from PySide6.QtCore import QEvent, QObject, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPen

from loomstroke.core.drawing_figures.commands import (
    SetDrawingFigurePoseCommand,
    apply_pose,
    capture_state,
    restore_state,
)
from loomstroke.core.drawing_figures.hand_joints import FINGERS, HAND_JOINTS, hand_side
from loomstroke.core.drawing_figures.hand_posing import hand_positions, rotate_hand_selection
from loomstroke.core.drawing_figures.kinematics import joint_positions
from loomstroke.core.drawing_figures.model import FigurePose, pose_from_layer
from loomstroke.core.drawing_figures.posing import move_joint
from loomstroke.core.drawing_figures.projection import project_points, screen_delta_to_world
from loomstroke.core.drawing_figures.rotation_posing import joint_axis_angles, rotate_joint
from loomstroke.core.layer_3d_rendering import orbit_camera_state

from .figure_hover import FigureHoverFeedback
from .figure_rotation_gizmo import (
    RotationDrag,
    draw_rotation_rings,
    hit_rotation_ring,
    rotation_rings,
)
from .guide_canvas_bridge import update_guide_overlay

_EDGES = (
    *(("pelvis", "waist"), ("waist", "chest"), ("chest", "neck"), ("neck", "head")),
    *tuple(
        (f"{side}_{a}", f"{side}_{b}")
        for side in ("left", "right")
        for a, b in (
            ("collar", "shoulder"),
            ("shoulder", "elbow"),
            ("elbow", "wrist"),
            ("hip", "knee"),
            ("knee", "ankle"),
        )
    ),
)


@dataclass
class _Drag:
    layer: Any
    pose: FigurePose
    joint: str
    start: tuple[float, float]
    origin: np.ndarray
    before: tuple[Any, Any, Any]
    expected: tuple[Any, Any, Any]
    preview: FigurePose
    capture_lease: Any = None
    rotation: RotationDrag | None = None
    rotation_angles: tuple[float, float, float] | None = None


class DrawingFigureInteraction(QObject):
    selection_changed = Signal(str)

    def __init__(self, owner: Any) -> None:
        super().__init__()
        self.owner = owner
        self.state: _Drag | None = None
        self.selected_joint = "left_wrist"
        self.hand_side: str | None = None
        self.hand_layer: Any = None
        self.hand_editor_active = False
        self.hover = FigureHoverFeedback(self)

    def select_joint(self, joint: str) -> None:
        self.selected_joint = joint
        self.hand_side = hand_side(joint)
        if self.hand_side is None:
            self.hand_editor_active = False
        self.hand_layer = self.layer() if self.hand_side is not None else None
        self.selection_changed.emit(joint)
        update_guide_overlay(self.owner._widget)

    def layer(self) -> Any:
        return self.owner._active_3d_layer()

    def _size(self) -> tuple[int, int]:
        state = self.owner._widget.state
        return int(state.width), int(state.height)

    def handles(self, layer: Any) -> dict[str, np.ndarray]:
        pose = pose_from_layer(layer)
        if pose is None:
            return {}
        positions = joint_positions(pose)
        if self.hand_side is not None and self.hand_layer is layer:
            positions.update(hand_positions(pose, self.hand_side))
            if self.hand_editor_active and self.owner.drag_mode() != "pose":
                positions = {
                    key: value
                    for key, value in positions.items()
                    if key in HAND_JOINTS or key == f"{self.hand_side}_wrist"
                }
        camera = orbit_camera_state(layer.camera_3d, layer.scene_3d)
        xy = project_points(
            np.asarray(list(positions.values())),
            layer.scene_3d,
            camera,
            *self._size(),
            (getattr(layer, "offset_x", 0), getattr(layer, "offset_y", 0)),
        )
        return dict(zip(positions, xy, strict=True))

    def hit_joint(self, layer: Any, widget_xy: tuple[float, float]) -> str | None:
        handles = self.handles(layer)
        to_widget = self.owner._widget.viewport.canvas_to_widget
        candidates = [
            (float(np.linalg.norm(np.asarray(to_widget(*xy)) - widget_xy)), key)
            for key, xy in handles.items()
        ]
        return min(
            (
                (distance, key)
                for distance, key in candidates
                if distance <= (6 if key in HAND_JOINTS else 14)
            ),
            default=(float("inf"), None),
            key=lambda item: item[0],
        )[1]

    def press(self, event: Any) -> bool:
        layer = self.layer()
        pose = pose_from_layer(layer)
        if pose is None:
            return False
        point = event.position()
        widget_xy = (float(point.x()), float(point.y()))
        canvas_xy = self.owner._widget.viewport.widget_to_canvas(*widget_xy)
        # Hit distance is in logical widget pixels, independent of zoom and DPR.
        joint = self.hit_joint(layer, widget_xy)
        rotation_drag = None
        if joint is None:
            ring = hit_rotation_ring(self.rings(layer), np.asarray(widget_xy))
            if ring is None:
                return False
            joint = self.selected_joint
            rotation_drag = RotationDrag(ring, np.asarray(widget_xy))
        visible = getattr(self.owner._widget, "_guide_viewport_host", self.owner._widget)
        for target in (visible, getattr(visible, "window", lambda: None)()):
            if isinstance(target, QObject):
                target.installEventFilter(self)
        self.select_joint(joint)
        before = capture_state(layer)
        self.state = _Drag(
            layer,
            pose,
            joint,
            canvas_xy,
            np.zeros(3) if joint in HAND_JOINTS else joint_positions(pose)[joint],
            before,
            before,
            pose,
        )
        self.state.rotation = rotation_drag
        self.state.rotation_angles = {**pose.rotations, **pose.hand_rotations}.get(joint, (0, 0, 0))
        self.hover.set_hit(
            layer,
            joint if rotation_drag is None else None,
            rotation_drag.ring.axis if rotation_drag else None,
        )
        begin = getattr(self.owner._canvas_service(), "_begin_transient_document_edit", None)
        if callable(begin):
            self.state.capture_lease = begin()
        self.selection_changed.emit(joint)
        event.accept()
        update_guide_overlay(self.owner._widget)
        return True

    def rings(self, layer: Any) -> Any:
        pose = pose_from_layer(layer)
        if pose is None:
            return ()
        return rotation_rings(
            pose,
            self.selected_joint,
            layer.scene_3d,
            orbit_camera_state(layer.camera_3d, layer.scene_3d),
            self._size(),
            (getattr(layer, "offset_x", 0), getattr(layer, "offset_y", 0)),
            self.owner._widget.viewport,
        )

    def _valid(self) -> bool:
        state = self.state
        return bool(
            state
            and self.layer() is state.layer
            and not getattr(state.layer, "locked", False)
            and state.layer.scene_3d is state.expected[0]
            and state.layer.gen_3d is state.expected[1]
            and state.layer.camera_3d is state.expected[2]
        )

    def move(self, event: Any) -> bool:
        state = self.state
        if state is None:
            return False
        if not self._valid():
            self.cancel()
            event.accept()
            return True
        point = event.position()
        if state.rotation is not None:
            angle = state.rotation.update(np.array((float(point.x()), float(point.y()))))
            angles = joint_axis_angles(
                state.pose,
                state.joint,
                state.rotation.ring.world_axis,
                angle,
                state.rotation_angles,
            )
            state.rotation_angles = angles
            rotate = (
                rotate_hand_selection
                if state.joint in HAND_JOINTS or self.hand_editor_active
                else rotate_joint
            )
            pose = rotate(state.pose, state.joint, angles)
            apply_pose(self.owner._canvas_service(), state.layer, pose)
            state.expected, state.preview = capture_state(state.layer), pose
            self.selection_changed.emit(state.joint)
            update_guide_overlay(self.owner._widget)
            event.accept()
            return True
        if state.joint in HAND_JOINTS or self.hand_editor_active:
            current = self.owner._widget.viewport.widget_to_canvas(
                float(point.x()), float(point.y())
            )
            angles = {**state.pose.rotations, **state.pose.hand_rotations}.get(
                state.joint, (0, 0, 0)
            )
            values = (
                angles[0] + (current[1] - state.start[1]) * 0.8,
                angles[1],
                angles[2] + (current[0] - state.start[0]) * 0.8,
            )
            pose = rotate_hand_selection(state.pose, state.joint, values)
            apply_pose(self.owner._canvas_service(), state.layer, pose)
            state.expected, state.preview = capture_state(state.layer), pose
            self.selection_changed.emit(state.joint)
            update_guide_overlay(self.owner._widget)
            event.accept()
            return True
        current = self.owner._widget.viewport.widget_to_canvas(float(point.x()), float(point.y()))
        delta = (current[0] - state.start[0], current[1] - state.start[1])
        camera = orbit_camera_state(state.before[2], state.before[0])
        world_delta = screen_delta_to_world(delta, state.before[0], camera, *self._size())
        if event.modifiers() & Qt.KeyboardModifier.AltModifier:
            depth = screen_delta_to_world((0, delta[1]), state.before[0], camera, *self._size())
            world_delta = camera.rotation_matrix().T @ np.array(
                (0, 0, np.linalg.norm(depth) * np.sign(-delta[1]))
            )
        pose = move_joint(state.pose, state.joint, state.origin + world_delta)
        apply_pose(self.owner._canvas_service(), state.layer, pose)
        state.expected, state.preview = capture_state(state.layer), pose
        self.selection_changed.emit(state.joint)
        update_guide_overlay(self.owner._widget)
        event.accept()
        return True

    def release(self, event: Any) -> bool:
        state = self.state
        if state is None:
            return False
        valid = self._valid()
        self.cancel()
        if valid and state.preview != state.pose:
            self.owner._submit(SetDrawingFigurePoseCommand(state.layer.id, state.preview))
        event.accept()
        return True

    def cancel(self) -> None:
        self.hover.clear()
        state = self.state
        if state is None:
            return
        # External history updates own the layer if they replaced the preview state.
        if state.layer.scene_3d is state.expected[0] and state.layer.gen_3d is state.expected[1]:
            camera = (
                state.before[2]
                if state.layer.camera_3d is state.expected[2]
                else state.layer.camera_3d
            )
            restore_state(self.owner._canvas_service(), state.layer, (*state.before[:2], camera))
        if state.capture_lease is not None:
            state.capture_lease.release()
        self.state = None
        update_guide_overlay(self.owner._widget)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() in {QEvent.Type.Hide, QEvent.Type.WindowDeactivate}:
            self.cancel()
        return super().eventFilter(watched, event)

    def cancel_if_invalid(self) -> None:
        if self.hand_layer is not None and self.layer() is not self.hand_layer:
            if self.selected_joint in HAND_JOINTS:
                self.selected_joint = f"{self.hand_side}_wrist"
            self.hand_side, self.hand_layer = None, None
            self.hand_editor_active = False
        if self.state is not None and not self._valid():
            self.cancel()
        if self.state is None and not self.hover.valid():
            self.hover.clear()

    def draw(self, painter: Any) -> None:
        self.cancel_if_invalid()
        if self.owner.drag_mode() != "pose" and not (
            self.hand_editor_active and self.owner.drag_mode() == "orbit"
        ):
            return
        layer = self.layer()
        pose = pose_from_layer(layer)
        if pose is None:
            return
        handles = self.handles(layer)
        painter.save()
        painter.setPen(QPen(QColor(80, 200, 255, 200), 1.5))
        for a, b in _EDGES:
            if a in handles and b in handles:
                painter.drawLine(QPointF(*handles[a]), QPointF(*handles[b]))
        if self.hand_side is not None and self.hand_layer is layer:
            positions = hand_positions(pose, self.hand_side, tips=True)
            tip_keys = [f"{self.hand_side}_{finger}_tip" for finger in FINGERS]
            tips = project_points(
                np.array([positions[key] for key in tip_keys]),
                layer.scene_3d,
                orbit_camera_state(layer.camera_3d, layer.scene_3d),
                *self._size(),
                (getattr(layer, "offset_x", 0), getattr(layer, "offset_y", 0)),
            )
            ends = {**handles, **dict(zip(tip_keys, tips, strict=True))}
            for finger in FINGERS:
                chain = [
                    f"{self.hand_side}_wrist",
                    *(f"{self.hand_side}_{finger}_{n}" for n in (1, 2, 3)),
                    f"{self.hand_side}_{finger}_tip",
                ]
                for a, b in pairwise(chain):
                    painter.drawLine(QPointF(*ends[a]), QPointF(*ends[b]))
        viewport_state = getattr(self.owner._widget.viewport, "state", None)
        zoom = max(float(getattr(viewport_state, "zoom", 1)), 0.01)
        active_axis = self.state.rotation.ring.axis if self.state and self.state.rotation else None
        draw_rotation_rings(painter, self.rings(layer), zoom, active_axis, self.hover.axis)
        for name, xy in handles.items():
            color = QColor("#ffbd4a" if name == self.selected_joint else "#50c8ff")
            if name in pose.pins:
                color = QColor("#ff6688")
            painter.setBrush(color)
            pen = QPen(QColor("#152633"), 1.5)
            pen.setCosmetic(True)
            painter.setPen(pen)
            # Radius remains usable under canvas zoom.
            viewport_state = getattr(self.owner._widget.viewport, "state", None)
            radius = 5 / max(float(getattr(viewport_state, "zoom", 1)), 0.01)
            painter.drawEllipse(QPointF(*xy), radius, radius)
        painter.restore()
