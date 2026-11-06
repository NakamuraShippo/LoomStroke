"""Export legacy BODY_18 or detailed BODY_25/hand/neutral-face figure poses."""

from __future__ import annotations

from typing import Any

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter

from ..core.assets_3d.scene import Scene3DDocument
from ..core.drawing_figures.kinematics import joint_positions, world_matrices
from ..core.drawing_figures.model import FigurePose, load_asset
from ..core.drawing_figures.projection import project_points
from .cameras import OrbitCameraState
from .figure_landmarks import BODY_25_COLORS, BODY_25_EDGES, BODY_25_KEYS, detailed_landmarks
from .openpose_rendering import FACE_EDGES, HAND_COLORS, HAND_EDGES, draw_keypoints

BODY_KEYS = (
    "nose",
    "neck",
    "right_shoulder",
    "right_elbow",
    "right_wrist",
    "left_shoulder",
    "left_elbow",
    "left_wrist",
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
)
BODY_EDGES = (
    (1, 2),
    (1, 5),
    (2, 3),
    (3, 4),
    (5, 6),
    (6, 7),
    (1, 8),
    (8, 9),
    (9, 10),
    (1, 11),
    (11, 12),
    (12, 13),
    (1, 0),
    (0, 14),
    (14, 16),
    (0, 15),
    (15, 17),
)
COLORS = BODY_25_COLORS[:8] + BODY_25_COLORS[9:19]


def _hand_points(pose: FigurePose, side: str) -> np.ndarray:
    asset = load_asset(pose.model)
    matrices = world_matrices(pose, asset)
    suffix = "L" if side == "left" else "R"
    wrist = matrices[asset.names.index(f"DEF-hand.{suffix}"), :3, 3]
    points = [wrist]
    for finger in ("thumb", "f_index", "f_middle", "f_ring", "f_pinky"):
        joints = [
            matrices[asset.names.index(f"DEF-{finger}.{i:02d}.{suffix}"), :3, 3] for i in (1, 2, 3)
        ]
        index = asset.names.index(f"DEF-{finger}.03.{suffix}")
        transform = matrices[index] @ np.linalg.inv(asset.rest[index])
        tip = (transform @ np.append(asset.tails[index], 1))[:3]
        points.extend([*joints, tip])
    return np.asarray(points)


def pose_json(
    pose: FigurePose,
    scene: Scene3DDocument,
    camera: OrbitCameraState,
    width: int,
    height: int,
    *,
    offset: tuple[float, float] = (0, 0),
    include_details: bool = False,
) -> dict[str, Any]:
    if width <= 0 or height <= 0 or max(width, height) > 32768:
        raise ValueError("Invalid OpenPose image dimensions")
    positions = joint_positions(pose)
    asset = load_asset(pose.model)
    head = asset.names.index("DEF-spine.006")
    transform = world_matrices(pose, asset)[head] @ np.linalg.inv(asset.rest[head])
    positions["nose"] = (transform @ np.append(asset.nose, 1))[:3]
    keys = BODY_25_KEYS if include_details else BODY_KEYS
    face = np.empty((0, 3), dtype=float)
    front = True
    if include_details:
        extra, face, front = detailed_landmarks(pose, camera)
        positions.update(extra)
        positions["mid_hip"] = (positions["left_hip"] + positions["right_hip"]) / 2
    values = np.zeros((len(keys), 3), dtype=float)
    for index, key in enumerate(keys):
        if key not in positions:
            continue
        xy = project_points(np.array([positions[key]]), scene, camera, width, height, offset)[0]
        confidence = 0.5 if key.endswith(("eye", "ear", "toe", "heel")) else 1.0
        if key in {"nose", "left_eye", "right_eye", "left_ear", "right_ear"} and not front:
            confidence = 0.0
        values[index] = (
            xy[0],
            xy[1],
            confidence if 0 <= xy[0] < width and 0 <= xy[1] < height else 0.0,
        )

    face_xy = project_points(face, scene, camera, width, height, offset)
    face_visible = (
        (face_xy[:, 0] >= 0)
        & (face_xy[:, 0] < width)
        & (face_xy[:, 1] >= 0)
        & (face_xy[:, 1] < height)
    )
    face_values = np.column_stack((face_xy, face_visible.astype(float) * (0.5 if front else 0)))

    def hand(side: str) -> list[float]:
        xy = project_points(_hand_points(pose, side), scene, camera, width, height, offset)
        confidence = (xy[:, 0] >= 0) & (xy[:, 0] < width) & (xy[:, 1] >= 0) & (xy[:, 1] < height)
        return np.column_stack((xy, confidence)).reshape(-1).astype(float).tolist()

    payload: dict[str, Any] = {
        "version": 1.3,
        "canvas_width": width,
        "canvas_height": height,
        "people": [
            {
                "person_id": [-1],
                "pose_keypoints_2d": values.reshape(-1).tolist(),
                "face_keypoints_2d": face_values.reshape(-1).tolist(),
                "hand_left_keypoints_2d": hand("left"),
                "hand_right_keypoints_2d": hand("right"),
                "pose_keypoints_3d": [],
                "face_keypoints_3d": [],
                "hand_left_keypoints_3d": [],
                "hand_right_keypoints_3d": [],
            }
        ],
    }
    if include_details:
        payload["loomstroke"] = {
            "body_model": "BODY_25",
            "face_source": "neutral_head_template",
            "foot_source": "rig_mesh_estimate",
            "hand_source": "rig",
        }
    return payload


def pose_image(payload: dict[str, Any]) -> QImage:
    width, height = int(payload["canvas_width"]), int(payload["canvas_height"])
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.black)
    painter = QPainter(image)
    # OpenPose's CPU renderer uses OpenCV LINE_8, without antialias blending.
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    points = np.asarray(payload["people"][0]["pose_keypoints_2d"]).reshape(-1, 3)
    detailed = len(points) == 25
    if len(points) not in {18, 25}:
        painter.end()
        raise ValueError("Unsupported OpenPose body point count")
    edges, colors = (BODY_25_EDGES, BODY_25_COLORS) if detailed else (BODY_EDGES, COLORS)
    line_width = max(2.0, min(width, height) / 128)
    draw_keypoints(painter, points, edges, colors, line_width=line_width, radius=line_width)
    for side in ("left", "right"):
        points = np.asarray(payload["people"][0][f"hand_{side}_keypoints_2d"]).reshape(-1, 3)
        draw_keypoints(
            painter,
            points,
            HAND_EDGES,
            HAND_COLORS,
            line_width=max(1.0, line_width * 0.4),
            radius=max(0.75, line_width * 0.25),
        )
    face = np.asarray(payload["people"][0].get("face_keypoints_2d", [])).reshape(-1, 3)
    draw_keypoints(
        painter,
        face,
        FACE_EDGES,
        ((255, 255, 255),),
        line_width=max(1.0, line_width * 0.22),
        radius=max(1.0, line_width * 0.22),
    )
    painter.end()
    return image
