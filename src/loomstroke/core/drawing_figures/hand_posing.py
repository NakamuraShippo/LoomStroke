"""Independent hand FK controls without whole-body follow or pin solving."""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from . import hand_presets
from .hand_joints import FINGERS, HAND_JOINTS
from .kinematics import constrained_angles, world_matrices
from .model import FigurePose, load_asset

HAND_PRESETS = hand_presets.HAND_PRESETS


def rotate_hand_selection(
    pose: FigurePose, joint: str, angles: tuple[float, float, float]
) -> FigurePose:
    if joint in HAND_JOINTS:
        return rotate_hand_joint(pose, joint, angles)
    values = np.asarray(angles, dtype=float)
    if (
        joint not in {"left_wrist", "right_wrist"}
        or values.shape != (3,)
        or not np.isfinite(values).all()
    ):
        raise ValueError("Invalid hand selection")
    x, y, z = values
    limited = constrained_angles(joint, (float(x), float(y), float(z)))
    if limited == pose.rotations.get(joint, (0, 0, 0)):
        return pose
    return replace(pose, rotations={**pose.rotations, joint: limited})


def hand_angles(joint: str, angles: tuple[float, float, float]) -> tuple[float, float, float]:
    values = np.asarray(angles, dtype=float)
    if joint not in HAND_JOINTS or values.shape != (3,) or not np.isfinite(values).all():
        raise ValueError("Invalid hand joint rotation")
    low, high = (-30, -45, -45), (130, 45, 45)
    if "_thumb_" in joint:
        low, high = (-45, -100, -100), (130, 100, 100)
    elif "_palm_" in joint:
        low, high = (-40, -35, -35), (40, 35, 35)
    x, y, z = np.clip(values, low, high)
    return float(x), float(y), float(z)


def rotate_hand_joint(
    pose: FigurePose, joint: str, angles: tuple[float, float, float]
) -> FigurePose:
    limited = hand_angles(joint, angles)
    if limited == pose.hand_rotations.get(joint, (0, 0, 0)):
        return pose
    rotations = dict(pose.hand_rotations)
    if any(limited):
        rotations[joint] = limited
    else:
        rotations.pop(joint, None)
    return replace(pose, hand_rotations=rotations)


def set_finger(pose: FigurePose, side: str, finger: str, curl: float, spread: float) -> FigurePose:
    if side not in {"left", "right"} or finger not in FINGERS:
        raise ValueError("Invalid hand or finger")
    if not np.isfinite((curl, spread)).all():
        raise ValueError("Invalid finger angle")
    for index in (1, 2, 3):
        key = f"{side}_{finger}_{index}"
        old = pose.hand_rotations.get(key, (0, 0, 0))
        pose = rotate_hand_joint(pose, key, (curl, old[1], spread if index == 1 else old[2]))
    return pose


def cup_hand(pose: FigurePose, side: str, degrees: float) -> FigurePose:
    if side not in {"left", "right"} or not np.isfinite(degrees):
        raise ValueError("Invalid palm angle")
    for index in range(1, 5):
        pose = rotate_hand_joint(pose, f"{side}_palm_{index}", (degrees * (index - 1) / 3, 0, 0))
    return pose


def reset_hand(pose: FigurePose, side: str) -> FigurePose:
    return rotate_hand_selection(hand_preset(pose, side, "open"), f"{side}_wrist", (0, 0, 0))


def hand_preset(pose: FigurePose, side: str, preset: str) -> FigurePose:
    if side not in {"left", "right"} or preset not in HAND_PRESETS:
        raise ValueError("Unknown hand preset")
    pose = replace(
        pose,
        hand_rotations={
            k: v for k, v in pose.hand_rotations.items() if not k.startswith(side + "_")
        },
    )
    definition = hand_presets.PRESET_DEFINITIONS[preset]
    lateral = -1 if side == "right" and definition.mirror_lateral_axes else 1
    for finger, curl, spread in zip(FINGERS, definition.curls, definition.spreads, strict=True):
        pose = set_finger(pose, side, finger, curl, spread * lateral)
    if definition.cup:
        pose = cup_hand(pose, side, definition.cup)
    for finger, joint, angles in definition.overrides:
        x, y, z = angles
        pose = rotate_hand_joint(pose, f"{side}_{finger}_{joint}", (x, y * lateral, z * lateral))
    return pose


def hand_positions(pose: FigurePose, side: str, *, tips: bool = False) -> dict[str, np.ndarray]:
    if side not in {"left", "right"}:
        raise ValueError("Invalid hand")
    asset = load_asset(pose.model)
    matrices = world_matrices(pose, asset)
    result = {
        key: matrices[asset.names.index(bone), :3, 3].copy()
        for key, bone in HAND_JOINTS.items()
        if key.startswith(side + "_")
    }
    if tips:
        for finger in FINGERS:
            index = asset.names.index(HAND_JOINTS[f"{side}_{finger}_3"])
            transform = matrices[index] @ np.linalg.inv(asset.rest[index])
            result[f"{side}_{finger}_tip"] = (transform @ np.append(asset.tails[index], 1))[:3]
    return result
