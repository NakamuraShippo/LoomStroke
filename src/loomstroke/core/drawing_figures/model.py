"""Validated figure poses and immutable bundled mesh/skeleton data."""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import numpy as np

from .hand_joints import HAND_JOINTS

JOINTS = {
    "pelvis": "DEF-spine",
    "waist": "DEF-spine.002",
    "chest": "DEF-spine.003",
    "neck": "DEF-spine.004",
    "head": "DEF-spine.006",
}
for _side, _suffix in (("left", "L"), ("right", "R")):
    JOINTS.update(
        {
            f"{_side}_{key}": f"DEF-{bone}.{_suffix}"
            for key, bone in (
                ("collar", "shoulder"),
                ("shoulder", "upper_arm"),
                ("elbow", "forearm"),
                ("wrist", "hand"),
                ("hip", "thigh"),
                ("knee", "shin"),
                ("ankle", "foot"),
            )
        }
    )
LIMBS = {
    f"{side}_{part}": tuple(f"{side}_{joint}" for joint in chain)
    for side in ("left", "right")
    for part, chain in (
        ("wrist", ("shoulder", "elbow", "wrist")),
        ("ankle", ("hip", "knee", "ankle")),
    )
}


def _vector(value: Any, limit: float) -> tuple[float, float, float]:
    array = np.asarray(value, dtype=float)
    if array.shape != (3,) or not np.isfinite(array).all() or np.any(np.abs(array) > limit):
        raise ValueError("Invalid drawing figure coordinate")
    return tuple(float(v) for v in array)  # type: ignore[return-value]


@dataclass(frozen=True)
class FigurePose:
    model: str = "bodykun"
    rotations: dict[str, tuple[float, float, float]] = field(default_factory=dict)
    translation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    pins: dict[str, tuple[float, float, float]] = field(default_factory=dict)
    assistance: float = 0.5
    hand_rotations: dict[str, tuple[float, float, float]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "version": 1,
            "model": self.model,
            "rotations": {k: list(v) for k, v in self.rotations.items()},
            "translation": list(self.translation),
            "pins": {k: list(v) for k, v in self.pins.items()},
            "assistance": self.assistance,
        }
        if self.hand_rotations:
            result["hand_rotations"] = {k: list(v) for k, v in self.hand_rotations.items()}
        return result

    @classmethod
    def from_dict(cls, data: Any) -> FigurePose:
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("Unsupported drawing figure pose")
        model = data.get("model")
        if model not in {"bodykun", "bodychan"}:
            raise ValueError("Unknown drawing figure model")
        rotations, pins = data.get("rotations", {}), data.get("pins", {})
        if not isinstance(rotations, dict) or not isinstance(pins, dict):
            raise ValueError("Invalid drawing figure pose")
        if set(rotations) - JOINTS.keys() or set(pins) - LIMBS.keys():
            raise ValueError("Unknown drawing figure joint")
        hands = data.get("hand_rotations", {})
        if not isinstance(hands, dict) or set(hands) - HAND_JOINTS.keys():
            raise ValueError("Unknown drawing figure hand joint")
        assistance = float(data.get("assistance", 0.5))
        if not np.isfinite(assistance) or not 0 <= assistance <= 1:
            raise ValueError("Invalid pose assistance")
        return cls(
            model,
            {k: _vector(v, 180) for k, v in rotations.items()},
            _vector(data.get("translation", (0, 0, 0)), 5),
            {k: _vector(v, 10) for k, v in pins.items()},
            assistance,
            {k: _vector(v, 180) for k, v in hands.items()},
        )


@dataclass(frozen=True)
class FigureAsset:
    vertices: np.ndarray
    normals: np.ndarray
    indices: np.ndarray
    joints: np.ndarray
    weights: np.ndarray
    rest: np.ndarray
    parents: np.ndarray
    tails: np.ndarray
    nose: np.ndarray
    names: tuple[str, ...]


@lru_cache(maxsize=2)
def load_asset(model: str) -> FigureAsset:
    if model == "bodykun":
        from .bodykun_data import PAYLOAD  # noqa: PLC0415
    elif model == "bodychan":
        from .bodychan_data import PAYLOAD  # noqa: PLC0415
    else:
        raise ValueError("Unknown drawing figure model")
    with np.load(io.BytesIO(base64.b85decode(PAYLOAD)), allow_pickle=False) as archive:
        arrays = {key: archive[key] for key in archive.files if key != "names"}
        names = tuple(str(n) for n in archive["names"])
    for value in arrays.values():
        value.flags.writeable = False
    return FigureAsset(**arrays, names=names)


def pose_from_layer(layer: Any) -> FigurePose | None:
    metadata = getattr(layer, "gen_3d", None)
    if not isinstance(metadata, dict) or "drawing_figure" not in metadata:
        return None
    try:
        return FigurePose.from_dict(metadata["drawing_figure"])
    except (ValueError, TypeError, OverflowError):
        return None
