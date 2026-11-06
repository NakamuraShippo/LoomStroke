"""Fixed-length limb IK, pins and bounded whole-body posing assistance."""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from .kinematics import align_vectors, angles_from_matrix, joint_positions, world_matrices
from .model import JOINTS, LIMBS, FigurePose, load_asset


def aim_joint(pose: FigurePose, joint: str, child: str, target: np.ndarray) -> FigurePose:
    asset = load_asset(pose.model)
    matrices = world_matrices(pose, asset)
    index = asset.names.index(JOINTS[joint])
    child_index = asset.names.index(JOINTS[child])
    origin = matrices[index, :3, 3]
    desired = align_vectors(matrices[child_index, :3, 3] - origin, target - origin)
    desired = desired @ matrices[index, :3, :3]
    parent = int(asset.parents[index])
    parent_world = matrices[parent, :3, :3] if parent >= 0 else np.eye(3)
    parent_rest = asset.rest[parent] if parent >= 0 else np.eye(4)
    rest_local = np.linalg.inv(parent_rest) @ asset.rest[index]
    angles = angles_from_matrix(rest_local[:3, :3].T @ parent_world.T @ desired)
    return replace(pose, rotations={**pose.rotations, joint: angles})


def solve_limb(pose: FigurePose, endpoint: str, target: np.ndarray) -> FigurePose:
    """Reach toward target while retaining segment lengths and a stable bend plane."""
    if endpoint not in LIMBS:
        raise ValueError("Not a limb endpoint")
    target = np.asarray(target, dtype=float)
    if target.shape != (3,) or not np.isfinite(target).all():
        raise ValueError("Invalid limb target")
    upper, middle, end = LIMBS[endpoint]
    positions = joint_positions(pose)
    a, b, c = (positions[j] for j in (upper, middle, end))
    length_a, length_b = np.linalg.norm(b - a), np.linalg.norm(c - b)
    offset = target - a
    distance = float(np.linalg.norm(offset))
    direction = offset / distance if distance > 1e-8 else (c - a) / max(np.linalg.norm(c - a), 1e-8)
    # At most 145 degrees of flexion; avoid singular fully straight chains.
    minimum = math.sqrt(
        max(length_a**2 + length_b**2 - 2 * length_a * length_b * math.cos(math.radians(35)), 1e-8)
    )
    distance = float(np.clip(distance, minimum, length_a + length_b - 1e-5))
    along = (length_a**2 - length_b**2 + distance**2) / (2 * distance)
    perpendicular = b - a - direction * np.dot(b - a, direction)
    if np.linalg.norm(perpendicular) < 1e-4:
        pole = np.array((0.0, 0.0, -1.0))
        perpendicular = pole - direction * np.dot(pole, direction)
    if np.linalg.norm(perpendicular) < 1e-6:
        perpendicular = np.cross(direction, (1.0, 0.0, 0.0))
    perpendicular /= max(np.linalg.norm(perpendicular), 1e-8)
    elbow = a + direction * along + perpendicular * math.sqrt(max(length_a**2 - along**2, 0))
    pose = aim_joint(pose, upper, middle, elbow)
    return aim_joint(pose, middle, end, a + direction * distance)


def move_joint(pose: FigurePose, joint: str, target: np.ndarray) -> FigurePose:
    """Move a joint with bounded trunk follow and restore user-pinned endpoints."""
    previous = pose
    target = np.asarray(target, dtype=float)
    if joint not in JOINTS or target.shape != (3,) or not np.isfinite(target).all():
        raise ValueError("Invalid joint target")
    if joint in pose.pins:
        return pose
    positions = joint_positions(pose)
    if joint in LIMBS:
        upper, middle, end = LIMBS[joint]
        reach = np.linalg.norm(positions[middle] - positions[upper]) + np.linalg.norm(
            positions[end] - positions[middle]
        )
        offset = target - positions[upper]
        excess = np.linalg.norm(offset) - reach * 0.9
        if pose.assistance and excess > 0 and joint.endswith("wrist"):
            # Bounded torso lean; pins are resolved again after the trunk movement.
            tilt = (
                np.clip(offset[[2, 0]] * (-1, 1) / max(reach, 1e-6), -1, 1) * pose.assistance * 18
            )
            chest = np.asarray(pose.rotations.get("chest", (0, 0, 0)), dtype=float)
            chest += (float(tilt[0]), 0.0, float(-tilt[1]))
            pose = replace(
                pose, rotations={**pose.rotations, "chest": tuple(np.clip(chest, -45, 45))}
            )
        pose = solve_limb(pose, joint, target)
    elif joint.endswith(("elbow", "knee")):
        side = joint.split("_")[0]
        parent = f"{side}_{'shoulder' if joint.endswith('elbow') else 'hip'}"
        pose = aim_joint(pose, parent, joint, target)
    elif joint in {"head", "neck", "chest"}:
        parent = {"head": "neck", "neck": "chest", "chest": "waist"}[joint]
        pose = aim_joint(pose, parent, joint, target)
    else:
        translation = np.asarray(pose.translation) + target - positions[joint]
        pose = replace(pose, translation=tuple(np.clip(translation, -5, 5)))
    return restore_pins(pose, previous=previous)


def restore_pins(pose: FigurePose, *, previous: FigurePose | None = None) -> FigurePose:
    if not pose.pins:
        return pose
    # Balance assistance only when both supporting feet were explicitly pinned.
    if pose.assistance and all(f"{side}_ankle" in pose.pins for side in ("left", "right")):
        positions = joint_positions(pose)
        center = positions["pelvis"] * 0.5 + positions["chest"] * 0.3 + positions["head"] * 0.2
        feet = np.array([pose.pins["left_ankle"], pose.pins["right_ankle"]])
        low, high = feet[:, (0, 2)].min(axis=0) - 0.08, feet[:, (0, 2)].max(axis=0) + 0.08
        correction = (np.clip(center[[0, 2]], low, high) - center[[0, 2]]) * pose.assistance
        translation = np.asarray(pose.translation).copy()
        translation[[0, 2]] += np.clip(correction, -0.08, 0.08)
        pose = replace(pose, translation=tuple(translation))
    for joint, target in pose.pins.items():
        pose = solve_limb(pose, joint, np.asarray(target))
    positions = joint_positions(pose)
    if previous is not None and any(
        np.linalg.norm(positions[joint] - target) > 5e-5 for joint, target in pose.pins.items()
    ):
        return previous
    return pose


def toggle_pin(pose: FigurePose, joint: str) -> FigurePose:
    if joint not in LIMBS:
        return pose
    pins = dict(pose.pins)
    if joint in pins:
        del pins[joint]
    else:
        pins[joint] = tuple(float(v) for v in joint_positions(pose)[joint])
    return replace(pose, pins=pins)


def reset_pose_part(pose: FigurePose, joint: str) -> FigurePose:
    """Restore one arm/leg, or one torso joint, without solving other parts."""
    if joint not in JOINTS:
        raise ValueError("Unknown drawing figure joint")
    parts: tuple[str, ...] = (joint,)
    if "_" in joint:
        side, part = joint.split("_", 1)
        chain: tuple[str, ...] = ("collar", "shoulder", "elbow", "wrist")
        if part not in chain:
            chain = ("hip", "knee", "ankle")
        parts = tuple(f"{side}_{name}" for name in chain)
    if not any(any(pose.rotations.get(name, (0, 0, 0))) for name in parts):
        return pose
    result = replace(
        pose, rotations={key: value for key, value in pose.rotations.items() if key not in parts}
    )
    if not pose.pins:
        return result
    # Keep pins enabled at the restored pose. Running IK here would bend the
    # reset limb again, or move unrelated body parts to reach its previous pin.
    asset = load_asset(pose.model)
    reset_indices = {asset.names.index(JOINTS[name]) for name in parts}
    positions = joint_positions(result)
    pins = dict(pose.pins)
    for endpoint in pins:
        index = asset.names.index(JOINTS[endpoint])
        while index >= 0:
            if index in reset_indices:
                x, y, z = positions[endpoint]
                pins[endpoint] = (float(x), float(y), float(z))
                break
            index = int(asset.parents[index])
    return replace(result, pins=pins)
