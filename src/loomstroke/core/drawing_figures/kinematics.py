"""Forward kinematics and bounded rotations; independent of Qt and Blender."""

from __future__ import annotations

import math
from functools import lru_cache

import numpy as np

from .hand_joints import HAND_JOINTS, hand_parent_overrides
from .model import JOINTS, FigureAsset, FigurePose, load_asset


def rotation(angles: tuple[float, float, float]) -> np.ndarray:
    x, y, z = np.radians(angles)
    cx, cy, cz = np.cos((x, y, z))
    sx, sy, sz = np.sin((x, y, z))
    return np.array(
        [
            [cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx],
            [sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx],
            [-sy, cy * sx, cy * cx],
        ],
        dtype=np.float64,
    )


def angles_from_matrix(matrix: np.ndarray) -> tuple[float, float, float]:
    y = math.asin(float(np.clip(-matrix[2, 0], -1, 1)))
    if abs(math.cos(y)) > 1e-7:
        x, z = math.atan2(matrix[2, 1], matrix[2, 2]), math.atan2(matrix[1, 0], matrix[0, 0])
    else:
        x, z = math.atan2(-matrix[1, 2], matrix[1, 1]), 0.0
    return tuple(float(v) for v in np.degrees((x, y, z)))  # type: ignore[return-value]


@lru_cache(maxsize=2)
def _local_rest(model: str) -> np.ndarray:
    asset = load_asset(model)
    return np.array(
        [
            (
                np.linalg.inv(asset.rest[int(parent)].astype(float)) @ asset.rest[i]
                if parent >= 0
                else asset.rest[i]
            )
            for i, parent in enumerate(asset.parents)
        ]
    )


def world_matrices(pose: FigurePose, asset: FigureAsset | None = None) -> np.ndarray:
    asset = asset or load_asset(pose.model)
    result = np.empty_like(asset.rest, dtype=np.float64)
    indices = {name: i for i, name in enumerate(asset.names)}
    rotations = {indices[JOINTS[k]]: rotation(v) for k, v in pose.rotations.items()}
    rotations.update({indices[HAND_JOINTS[k]]: rotation(v) for k, v in pose.hand_rotations.items()})
    parent_overrides = hand_parent_overrides(pose.hand_rotations, indices)
    done: set[int] = set()
    locals_ = _local_rest(pose.model)

    def visit(index: int) -> None:
        if index in done:
            return
        parent = int(parent_overrides.get(index, asset.parents[index]))
        if parent >= 0:
            visit(parent)
            # Recreate Rigify's palm-to-finger link when a palm bone is edited.
            local = (
                np.linalg.inv(asset.rest[parent].astype(float)) @ asset.rest[index]
                if index in parent_overrides
                else locals_[index].copy()
            )
        else:
            local = locals_[index].copy()
            local[:3, 3] += pose.translation
        if index in rotations:
            local[:3, :3] = local[:3, :3] @ rotations[index]
        result[index] = result[parent] @ local if parent >= 0 else local
        done.add(index)

    for index in range(len(asset.names)):
        visit(index)
    return result


def joint_positions(pose: FigurePose) -> dict[str, np.ndarray]:
    asset = load_asset(pose.model)
    matrices = world_matrices(pose, asset)
    return {key: matrices[asset.names.index(bone), :3, 3].copy() for key, bone in JOINTS.items()}


def align_vectors(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    a = source / max(np.linalg.norm(source), 1e-12)
    b = target / max(np.linalg.norm(target), 1e-12)
    cross = np.cross(a, b)
    cosine = float(np.clip(np.dot(a, b), -1, 1))
    if cosine < -0.999999:
        axis = np.cross(a, (1, 0, 0) if abs(a[0]) < 0.8 else (0, 1, 0))
        axis /= np.linalg.norm(axis)
        return 2 * np.outer(axis, axis) - np.eye(3)
    skew = np.array([[0, -cross[2], cross[1]], [cross[2], 0, -cross[0]], [-cross[1], cross[0], 0]])
    return np.eye(3) + skew + skew @ skew / max(1 + cosine, 1e-12)


def constrained_angles(
    joint: str, angles: tuple[float, float, float]
) -> tuple[float, float, float]:
    values = np.clip(angles, -170, 170).astype(float)
    if joint.endswith(("elbow", "knee")):
        values[0] = np.clip(values[0], 0, 145)
        values[1:] = np.clip(values[1:], -20, 20)
    elif joint in {"waist", "chest", "neck", "head"}:
        values = np.clip(values, -65, 65)
    return tuple(float(v) for v in values)  # type: ignore[return-value]
