"""Rig-following foot landmarks and a neutral, estimated face template."""

from __future__ import annotations

from functools import lru_cache

import numpy as np

from ..core.drawing_figures.kinematics import world_matrices
from ..core.drawing_figures.model import FigurePose, load_asset
from .cameras import OrbitCameraState

BODY_25_KEYS = (
    "nose",
    "neck",
    "right_shoulder",
    "right_elbow",
    "right_wrist",
    "left_shoulder",
    "left_elbow",
    "left_wrist",
    "mid_hip",
    "right_hip",
    "right_knee",
    "right_ankle",
    "left_hip",
    "left_knee",
    "left_ankle",
    "right_eye",
    "left_eye",
    "right_ear",
    "left_ear",
    "left_big_toe",
    "left_small_toe",
    "left_heel",
    "right_big_toe",
    "right_small_toe",
    "right_heel",
)
BODY_25_EDGES = (
    (1, 8),
    (1, 2),
    (1, 5),
    (2, 3),
    (3, 4),
    (5, 6),
    (6, 7),
    (8, 9),
    (9, 10),
    (10, 11),
    (8, 12),
    (12, 13),
    (13, 14),
    (1, 0),
    (0, 15),
    (15, 17),
    (0, 16),
    (16, 18),
    (14, 19),
    (19, 20),
    (14, 21),
    (11, 22),
    (22, 23),
    (11, 24),
)
BODY_25_COLORS = (
    (255, 0, 85),
    (255, 0, 0),
    (255, 85, 0),
    (255, 170, 0),
    (255, 255, 0),
    (170, 255, 0),
    (85, 255, 0),
    (0, 255, 0),
    (255, 0, 0),
    (0, 255, 85),
    (0, 255, 170),
    (0, 255, 255),
    (0, 170, 255),
    (0, 85, 255),
    (0, 0, 255),
    (255, 0, 170),
    (170, 0, 255),
    (255, 0, 255),
    (85, 0, 255),
    (0, 0, 255),
    (0, 0, 255),
    (0, 0, 255),
    (0, 255, 255),
    (0, 255, 255),
    (0, 255, 255),
)


def _transform(points: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    return np.asarray((np.column_stack((points, np.ones(len(points)))) @ matrix.T)[:, :3])


@lru_cache(maxsize=2)
def neutral_face(model: str) -> np.ndarray:
    """Return 68 facial landmarks and two pupils in OpenPose/dlib order."""
    asset = load_asset(model)
    head = asset.names.index("DEF-spine.006")
    mask = np.sum(asset.weights * (asset.joints == head), axis=1) > 0.5
    vertices = asset.vertices[mask]
    low, high = vertices.min(axis=0), vertices.max(axis=0)
    points: list[tuple[float, float]] = []
    for angle in np.linspace(np.pi, 0, 17):
        points.append((0.88 * np.cos(angle), 0.48 - 0.42 * np.sin(angle)))
    for left, right in ((-0.65, -0.15), (0.15, 0.65)):
        for x, angle in zip(np.linspace(left, right, 5), np.linspace(0, np.pi, 5), strict=True):
            points.append((x, 0.67 + 0.06 * np.sin(angle)))
    points.extend((0, y) for y in np.linspace(0.62, 0.42, 4))
    points.extend(
        (x, 0.34 - 0.03 * np.sin(angle))
        for x, angle in zip(np.linspace(-0.25, 0.25, 5), np.linspace(0, np.pi, 5), strict=True)
    )
    for center in (-0.4, 0.4):
        points.extend(
            (center + 0.22 * np.cos(a), 0.58 + 0.045 * np.sin(a))
            for a in np.linspace(np.pi, -np.pi, 6, endpoint=False)
        )
    for width, height, count in ((0.35, 0.075, 12), (0.22, 0.025, 8)):
        points.extend(
            (width * np.cos(a), 0.23 + height * np.sin(a))
            for a in np.linspace(np.pi, -np.pi, count, endpoint=False)
        )
    points.extend(((-0.4, 0.58), (0.4, 0.58)))
    uv = np.asarray(points)
    center = (low + high) / 2
    radii = (high - low) / 2
    x = center[0] + uv[:, 0] * radii[0]
    y = low[1] + uv[:, 1] * (high[1] - low[1])
    z = center[2] - radii[2] * np.sqrt(
        np.maximum(0.1, 1 - (uv[:, 0] * 0.8) ** 2 - ((uv[:, 1] - 0.55) * 1.5) ** 2)
    )
    result = np.column_stack((x, y, z))
    result[30] = asset.nose
    result.flags.writeable = False
    return result


@lru_cache(maxsize=4)
def _foot_rest(model: str, side: str) -> np.ndarray:
    asset = load_asset(model)
    suffix = "L" if side == "left" else "R"
    bones = (asset.names.index(f"DEF-foot.{suffix}"), asset.names.index(f"DEF-toe.{suffix}"))
    mask = np.sum(asset.weights * np.isin(asset.joints, bones), axis=1) > 0.5
    vertices = asset.vertices[mask]
    low, high = vertices.min(axis=0), vertices.max(axis=0)
    center, extent = (low + high) / 2, high - low
    sign = 1 if side == "left" else -1
    result = np.array(
        [
            (
                center[0] - sign * extent[0] * 0.22,
                low[1] + extent[1] * 0.15,
                low[2] + extent[2] * 0.07,
            ),
            (
                center[0] + sign * extent[0] * 0.30,
                low[1] + extent[1] * 0.15,
                low[2] + extent[2] * 0.07,
            ),
            (center[0], low[1] + extent[1] * 0.1, high[2] - extent[2] * 0.1),
        ]
    )
    result.flags.writeable = False
    return result


def detailed_landmarks(
    pose: FigurePose, camera: OrbitCameraState
) -> tuple[dict[str, np.ndarray], np.ndarray, bool]:
    asset = load_asset(pose.model)
    matrices = world_matrices(pose, asset)
    head = asset.names.index("DEF-spine.006")
    transform = matrices[head] @ np.linalg.inv(asset.rest[head])
    face = _transform(neutral_face(pose.model), transform)
    normal = camera.rotation_matrix() @ transform[:3, :3] @ np.array((0, 0, -1))
    front = bool(normal[2] < -0.05)
    points = {
        "right_eye": face[68],
        "left_eye": face[69],
        "right_ear": face[0],
        "left_ear": face[16],
    }
    for side, suffix in (("left", "L"), ("right", "R")):
        rest = _foot_rest(pose.model, side)
        for index, (name, bone) in enumerate(
            (("big_toe", "toe"), ("small_toe", "toe"), ("heel", "foot"))
        ):
            bone_index = asset.names.index(f"DEF-{bone}.{suffix}")
            matrix = matrices[bone_index] @ np.linalg.inv(asset.rest[bone_index])
            points[f"{side}_{name}"] = _transform(rest[index : index + 1], matrix)[0]
    return points, face, front
