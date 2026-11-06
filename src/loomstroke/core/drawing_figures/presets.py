"""Named pose presets sharing the same editable skeleton as manual posing."""

from __future__ import annotations

import numpy as np

from .kinematics import joint_positions
from .model import LIMBS, FigurePose
from .posing import aim_joint

PRESETS = {
    "standing": "Standing",
    "arms_out": "Arms out",
    "sitting": "Sitting",
    "walking": "Walking",
    "crouching": "Crouching",
    "reaching": "Reaching",
}


def preset_pose(model: str, preset: str) -> FigurePose:
    if preset not in PRESETS:
        raise ValueError("Unknown drawing figure preset")
    pose = FigurePose(model)
    if preset == "arms_out":
        positions = joint_positions(pose)
        for side in ("left", "right"):
            sign = np.sign(positions[f"{side}_shoulder"][0] - positions["chest"][0])
            pose = _point_limb(pose, f"{side}_wrist", (sign, 0, 0), (sign, 0, 0))
    elif preset in {"sitting", "crouching"}:
        upper = (0, 0.17, -1) if preset == "crouching" else (0, -0.05, -1)
        lower = (0, -0.7, 0.7) if preset == "crouching" else (0, -1, 0)
        for side in ("left", "right"):
            pose = _point_limb(pose, f"{side}_ankle", upper, lower)
    elif preset == "walking":
        pose = _point_limb(pose, "left_ankle", (0, -0.9, -0.4), (0, -1, 0))
        pose = _point_limb(pose, "right_ankle", (0, -0.9, 0.4), (0, -0.85, 0.5))
        pose = _point_limb(pose, "left_wrist", (0, -0.85, 0.4), (0, -0.9, -0.1))
        pose = _point_limb(pose, "right_wrist", (0, -0.85, -0.4), (0, -0.85, -0.5))
    elif preset == "reaching":
        pose = _point_limb(pose, "left_wrist", (0.25, 0.9, -0.15), (0.1, 1, -0.1))
    return FigurePose.from_dict(pose.to_dict())


def _point_limb(pose: FigurePose, endpoint: str, upper: tuple, lower: tuple) -> FigurePose:
    a, b, c = LIMBS[endpoint]
    positions = joint_positions(pose)
    pose = aim_joint(pose, a, b, positions[a] + upper)
    return aim_joint(pose, b, c, joint_positions(pose)[b] + lower)
