"""The same orthographic projection as the native wgpu 3D layer renderer."""

from __future__ import annotations

import numpy as np

from ...rendering_3d.cameras import OrbitCameraState
from ..assets_3d.scene import Scene3DDocument


def projection_scale(
    scene: Scene3DDocument, camera: OrbitCameraState, width: int, height: int
) -> float:
    low, high = scene.bounds()
    radius = float(np.linalg.norm(high - low) * 0.5)
    return min(width, height) * 0.42 * camera.zoom / max(radius, 1e-8)


def project_points(
    points: np.ndarray,
    scene: Scene3DDocument,
    camera: OrbitCameraState,
    width: int,
    height: int,
    offset: tuple[float, float] = (0, 0),
) -> np.ndarray:
    rotated = (np.asarray(points) - camera.target) @ camera.rotation_matrix().T
    scale = projection_scale(scene, camera, width, height)
    return np.column_stack(
        (
            width * 0.5 + rotated[:, 0] * scale + offset[0],
            height * 0.5 - rotated[:, 1] * scale + offset[1],
        )
    )


def screen_delta_to_world(
    delta: tuple[float, float],
    scene: Scene3DDocument,
    camera: OrbitCameraState,
    width: int,
    height: int,
) -> np.ndarray:
    scale = projection_scale(scene, camera, width, height)
    return camera.rotation_matrix().T @ np.array((delta[0] / scale, -delta[1] / scale, 0.0))
