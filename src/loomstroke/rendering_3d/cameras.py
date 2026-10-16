"""Orbit camera state for 3D preview and projection."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from loomstroke.core.assets_3d.scene import Scene3DDocument


@dataclass(frozen=True, slots=True)
class OrbitCameraState:
    """Simple orbit camera with fit-to-view helpers."""

    yaw_degrees: float = 35.0
    pitch_degrees: float = -20.0
    zoom: float = 1.0
    target: tuple[float, float, float] = (0.0, 0.0, 0.0)
    distance: float = 4.0

    def rotated(self, *, yaw_delta: float = 0.0, pitch_delta: float = 0.0) -> OrbitCameraState:
        return OrbitCameraState(
            yaw_degrees=self.yaw_degrees + yaw_delta,
            pitch_degrees=max(-89.0, min(89.0, self.pitch_degrees + pitch_delta)),
            zoom=self.zoom,
            target=self.target,
            distance=self.distance,
        )

    def zoomed(self, factor: float) -> OrbitCameraState:
        return OrbitCameraState(
            yaw_degrees=self.yaw_degrees,
            pitch_degrees=self.pitch_degrees,
            zoom=max(0.05, min(20.0, self.zoom * float(factor))),
            target=self.target,
            distance=self.distance,
        )

    def turntable(self, seconds: float, *, degrees_per_second: float = 24.0) -> OrbitCameraState:
        return self.rotated(yaw_delta=float(seconds) * float(degrees_per_second))

    @classmethod
    def fit_to_scene(cls, scene: Scene3DDocument) -> OrbitCameraState:
        low, high = scene.bounds()
        center = ((low + high) * 0.5).astype(float)
        radius = float(np.linalg.norm(high - low) * 0.5)
        if radius <= 1e-6:
            radius = 1.0
        return cls(
            target=(float(center[0]), float(center[1]), float(center[2])),
            distance=radius * 3.2,
        )

    def rotation_matrix(self) -> np.ndarray:
        yaw = math.radians(self.yaw_degrees)
        pitch = math.radians(self.pitch_degrees)
        cy, sy = math.cos(yaw), math.sin(yaw)
        cp, sp = math.cos(pitch), math.sin(pitch)
        yaw_matrix = np.array([[cy, 0.0, sy], [0.0, 1.0, 0.0], [-sy, 0.0, cy]], dtype=np.float32)
        pitch_matrix = np.array(
            [[1.0, 0.0, 0.0], [0.0, cp, -sp], [0.0, sp, cp]],
            dtype=np.float32,
        )
        return pitch_matrix @ yaw_matrix
