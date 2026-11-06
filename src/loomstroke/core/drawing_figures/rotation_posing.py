"""Constrained joint rotation with bounded body follow and positional pins."""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from .hand_joints import HAND_JOINTS, hand_parent_overrides
from .hand_posing import rotate_hand_joint
from .kinematics import (
    angles_from_matrix,
    constrained_angles,
    joint_positions,
    rotation,
    world_matrices,
)
from .model import JOINTS, LIMBS, FigurePose, load_asset
from .posing import solve_limb


def axis_rotation(axis: np.ndarray, degrees: float) -> np.ndarray:
    """Positive rotation in the canvas's left-handed world coordinates."""
    axis = np.asarray(axis, dtype=float)
    if axis.shape != (3,) or not np.isfinite(axis).all() or not math.isfinite(degrees):
        raise ValueError("Invalid rotation axis or angle")
    length = float(np.linalg.norm(axis))
    if length < 1e-8:
        raise ValueError("Rotation axis must be nonzero")
    x, y, z = axis / length
    skew = np.array(((0, -z, y), (z, 0, -x), (-y, x, 0)))
    angle = math.radians(-degrees)
    return np.asarray(
        np.eye(3) + math.sin(angle) * skew + (1 - math.cos(angle)) * (skew @ skew), dtype=np.float64
    )


def _axis_angle(matrix: np.ndarray) -> tuple[np.ndarray, float]:
    angle = math.acos(float(np.clip((np.trace(matrix) - 1) / 2, -1, 1)))
    if angle < 1e-8:
        return np.array((1.0, 0.0, 0.0)), 0.0
    if abs(math.sin(angle)) < 1e-6:
        _, vectors = np.linalg.eigh((matrix + matrix.T + 2 * np.eye(3)) / 4)
        axis = vectors[:, -1]
    else:
        axis = np.array(
            (matrix[2, 1] - matrix[1, 2], matrix[0, 2] - matrix[2, 0], matrix[1, 0] - matrix[0, 1])
        ) / (2 * math.sin(angle))
    return -axis, math.degrees(angle)


def _joint_world(pose: FigurePose, joint: str) -> np.ndarray:
    asset = load_asset(pose.model)
    return world_matrices(pose, asset)[asset.names.index({**JOINTS, **HAND_JOINTS}[joint]), :3, :3]


def _local_angles(pose: FigurePose, joint: str, desired: np.ndarray) -> tuple[float, float, float]:
    asset = load_asset(pose.model)
    index = asset.names.index({**JOINTS, **HAND_JOINTS}[joint])
    overrides = hand_parent_overrides(
        pose.hand_rotations, {name: i for i, name in enumerate(asset.names)}
    )
    parent = int(overrides.get(index, asset.parents[index]))
    parent_world = world_matrices(pose, asset)[parent, :3, :3] if parent >= 0 else np.eye(3)
    parent_rest = asset.rest[parent] if parent >= 0 else np.eye(4)
    rest = np.linalg.inv(parent_rest) @ asset.rest[index]
    return angles_from_matrix(np.linalg.solve(parent_world @ rest[:3, :3], desired))


def _set_world(pose: FigurePose, joint: str, desired: np.ndarray) -> FigurePose:
    angles = constrained_angles(joint, _local_angles(pose, joint, desired))
    return replace(pose, rotations={**pose.rotations, joint: angles})


def joint_axis_angles(
    pose: FigurePose,
    joint: str,
    world_axis: np.ndarray,
    degrees: float,
    reference: tuple[float, float, float] | None = None,
) -> tuple[float, float, float]:
    """Unwrap Euler branches before limits, so dragging past 180 cannot snap."""
    if joint not in JOINTS and joint not in HAND_JOINTS:
        raise ValueError("Unknown drawing figure joint")
    desired = axis_rotation(world_axis, degrees) @ _joint_world(pose, joint)
    raw = np.array(_local_angles(pose, joint, desired))
    previous = np.asarray(
        reference
        if reference is not None
        else {**pose.rotations, **pose.hand_rotations}.get(joint, (0, 0, 0))
    )
    if previous.shape != (3,) or not np.isfinite(previous).all():
        raise ValueError("Invalid rotation reference")
    alternative = np.array((raw[0] + 180, 180 - raw[1], raw[2] + 180))
    candidates = [value + 360 * np.round((previous - value) / 360) for value in (raw, alternative)]
    result = min(candidates, key=lambda value: float(np.linalg.norm(value - previous)))
    return tuple(float(v) for v in result)  # type: ignore[return-value]


def _followers(joint: str) -> tuple[tuple[str, float], ...]:
    trunk = {
        "head": (("chest", 0.03), ("neck", 0.2)),
        "neck": (("chest", 0.1),),
        "chest": (("pelvis", 0.06), ("waist", 0.2)),
        "waist": (("pelvis", 0.15),),
        "pelvis": (("waist", -0.12), ("chest", -0.04)),
    }
    if joint in trunk:
        return trunk[joint]
    side, part = joint.split("_", 1)
    return {
        "collar": (("waist", 0.03), ("chest", 0.12)),
        "shoulder": (("waist", 0.04), ("chest", 0.1), (f"{side}_collar", 0.25)),
        "elbow": ((f"{side}_collar", 0.04), (f"{side}_shoulder", 0.1)),
        "wrist": ((f"{side}_shoulder", 0.04), (f"{side}_elbow", 0.08)),
        "hip": (("pelvis", 0.12), ("waist", -0.04)),
        "knee": (("pelvis", 0.03), (f"{side}_hip", 0.08)),
        "ankle": ((f"{side}_hip", 0.03), (f"{side}_knee", 0.08)),
    }[part]


def _bounded(vector: np.ndarray, limit: float) -> np.ndarray:
    return vector * min(1.0, limit / max(float(np.linalg.norm(vector)), 1e-12))


def _fit_pins(pose: FigurePose, previous: FigurePose) -> FigurePose | None:
    if not pose.pins:
        return pose
    positions = joint_positions(pose)
    if pose.assistance:
        residual = np.mean([np.asarray(t) - positions[j] for j, t in pose.pins.items()], axis=0)
        shift = _bounded(residual * pose.assistance * 0.35, 0.08)
        if all(f"{side}_ankle" in pose.pins for side in ("left", "right")):
            center = positions["pelvis"] * 0.5 + positions["chest"] * 0.3 + positions["head"] * 0.2
            feet = np.array([pose.pins["left_ankle"], pose.pins["right_ankle"]])[:, (0, 2)]
            low, high = feet.min(axis=0) - 0.08, feet.max(axis=0) + 0.08
            shift[[0, 2]] += (np.clip(center[[0, 2]], low, high) - center[[0, 2]]) * pose.assistance
        # Project body translation into every pinned limb's reachable shell.
        # Origins change linearly with root translation; no FK is needed here.
        for _ in range(16):
            for endpoint, target in pose.pins.items():
                upper, middle, end = LIMBS[endpoint]
                a, b, c = (positions[j] for j in (upper, middle, end))
                la, lb = float(np.linalg.norm(b - a)), float(np.linalg.norm(c - b))
                low = math.sqrt(max(la**2 + lb**2 - 2 * la * lb * math.cos(math.radians(35)), 0))
                high = la + lb - 1e-5
                offset = np.asarray(target) - a - shift
                distance = float(np.linalg.norm(offset))
                if distance > 1e-8:
                    shift += offset / distance * (distance - float(np.clip(distance, low, high)))
                shift = _bounded(shift, 0.15)
        translation = np.asarray(pose.translation) + shift
        if np.any(np.abs(translation) > 5):
            return None
        pose = replace(
            pose, translation=(float(translation[0]), float(translation[1]), float(translation[2]))
        )
    for endpoint, target in pose.pins.items():
        pose = solve_limb(pose, endpoint, np.asarray(target))
    positions = joint_positions(pose)
    if any(np.linalg.norm(positions[j] - target) > 5e-5 for j, target in pose.pins.items()):
        return None
    if np.linalg.norm(np.asarray(pose.translation) - previous.translation) > 0.15001:
        return None
    return pose


def rotate_joint(pose: FigurePose, joint: str, angles: tuple[float, float, float]) -> FigurePose:
    """Apply local Euler input through the same body/pin solver as the rings."""
    if joint in HAND_JOINTS:
        return rotate_hand_joint(pose, joint, angles)
    values = np.asarray(angles, dtype=float)
    if joint not in JOINTS or values.shape != (3,) or not np.isfinite(values).all():
        raise ValueError("Invalid joint rotation")
    limited = constrained_angles(joint, (float(values[0]), float(values[1]), float(values[2])))
    if np.allclose(rotation(limited), rotation(pose.rotations.get(joint, (0, 0, 0))), atol=1e-9):
        return pose
    desired = replace(pose, rotations={**pose.rotations, joint: limited})
    original_world, desired_world = _joint_world(pose, joint), _joint_world(desired, joint)
    axis, degrees = _axis_angle(desired_world @ np.linalg.inv(original_world))

    def solve(factor: float) -> FigurePose | None:
        target = axis_rotation(axis, degrees * factor) @ original_world
        candidate = desired if factor == 1 else _set_world(pose, joint, target)
        primary_angles = candidate.rotations[joint]
        if pose.assistance:
            for neighbor, weight in _followers(joint):
                follow = float(np.clip(degrees * factor * weight * pose.assistance, -12, 12))
                candidate = _set_world(
                    candidate,
                    neighbor,
                    axis_rotation(axis, follow) @ _joint_world(candidate, neighbor),
                )
            # XYZ fields and rings edit local joint rotation. Ancestor follow
            # must not silently change the entered angle or its joint limit.
            candidate = replace(candidate, rotations={**candidate.rotations, joint: primary_angles})
        return _fit_pins(candidate, pose)

    corrected = solve(1.0)
    if corrected is not None:
        return corrected
    # Refine the feasible angle rather than jumping to half the request when
    # a pin reaches its limit. Bound work to seven constraint solves per move.
    low, high, best = 0.0, 1.0, pose
    for _ in range(6):
        factor = (low + high) * 0.5
        corrected = solve(factor)
        if corrected is None:
            high = factor
        else:
            low, best = factor, corrected
    return best


def rotate_joint_axis(
    pose: FigurePose, joint: str, world_axis: np.ndarray, degrees: float
) -> FigurePose:
    """Rotate about a drag-start world axis, independent of camera orientation."""
    if joint not in JOINTS:
        raise ValueError("Unknown drawing figure joint")
    delta = axis_rotation(world_axis, degrees)
    if np.allclose(delta, np.eye(3), atol=1e-9):
        return pose
    desired = _set_world(pose, joint, delta @ _joint_world(pose, joint))
    return rotate_joint(pose, joint, desired.rotations[joint])
