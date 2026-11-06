"""Figure pose changes use the existing 3D layer command and metadata contracts."""

from __future__ import annotations

from typing import Any

import numpy as np

from ..commands_layer_3d import SetLayer3DSceneCommand, _refresh_3d_layer_preview, _resolve_3d_layer
from ..layer_3d_rendering import orbit_camera_state
from .model import FigurePose, pose_from_layer
from .scene import build_scene


def scene_retained_bytes(*scenes: Any) -> int:
    arrays = {
        id(array): array
        for scene in scenes
        if scene is not None
        for mesh in scene.meshes
        for primitive in mesh.primitives
        for array in (primitive.vertices, primitive.normals, primitive.indices)
        if array is not None
    }
    return sum(array.nbytes for array in arrays.values())


def capture_state(layer: Any) -> tuple[Any, Any, Any]:
    return layer.scene_3d, layer.gen_3d, layer.camera_3d


def restore_state(canvas: Any, layer: Any, state: tuple[Any, Any, Any]) -> None:
    layer.scene_3d, layer.gen_3d, layer.camera_3d = state
    _refresh_3d_layer_preview(canvas, layer)


def apply_pose(canvas: Any, layer: Any, pose: FigurePose) -> None:
    scene = build_scene(pose)
    camera = orbit_camera_state(layer.camera_3d, layer.scene_3d)
    old_low, old_high = layer.scene_3d.bounds()
    low, high = scene.bounds()
    # Preserve the world-to-canvas scale while the posed geometry bounds change.
    ratio = float(np.linalg.norm(high - low) / max(np.linalg.norm(old_high - old_low), 1e-8))
    layer.camera_3d = {
        "yaw_degrees": camera.yaw_degrees,
        "pitch_degrees": camera.pitch_degrees,
        "zoom": camera.zoom * ratio,
        "target": list(camera.target),
        "distance": camera.distance,
    }
    layer.scene_3d = scene
    layer.gen_3d = {**(layer.gen_3d or {}), "drawing_figure": pose.to_dict()}
    _refresh_3d_layer_preview(canvas, layer)


class SetDrawingFigurePoseCommand(SetLayer3DSceneCommand):
    """One complete pose edit, with original geometry/camera restored on Undo."""

    def __init__(self, layer_id: str, pose: FigurePose) -> None:
        super().__init__(layer_id, scene=None)
        self.layer_id = layer_id
        self.pose = FigurePose.from_dict(pose.to_dict())
        self._before: tuple[Any, Any, Any] | None = None
        self._after: tuple[Any, Any, Any] | None = None

    @property
    def retained_bytes(self) -> int:
        return scene_retained_bytes(*(state[0] for state in (self._before, self._after) if state))

    def apply(self, canvas: Any) -> bool:
        layer = _resolve_3d_layer(canvas, self.layer_id)
        current = pose_from_layer(layer)
        if current is None or current.model != self.pose.model:
            return False
        if self._after is not None:
            restore_state(canvas, layer, self._after)
        else:
            if current == self.pose:
                return False
            self._before = capture_state(layer)
            apply_pose(canvas, layer, self.pose)
            self._after = capture_state(layer)
        self._executed = True
        return True

    def revert(self, canvas: Any) -> bool:
        layer = _resolve_3d_layer(canvas, self.layer_id)
        if layer is None or self._before is None:
            return False
        restore_state(canvas, layer, self._before)
        self._executed = False
        return True
