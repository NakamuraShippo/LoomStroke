"""Stable hand bone identifiers, separate from whole-body pose handles."""

from collections.abc import Mapping

FINGERS = {
    "thumb": "thumb",
    "index": "f_index",
    "middle": "f_middle",
    "ring": "f_ring",
    "little": "f_pinky",
}
HAND_JOINTS = {
    f"{side}_{finger}_{joint}": f"DEF-{bone}.{joint:02d}.{suffix}"
    for side, suffix in (("left", "L"), ("right", "R"))
    for finger, bone in {**FINGERS, "palm": "palm"}.items()
    for joint in range(1, 5 if finger == "palm" else 4)
}


def hand_side(joint: str) -> str | None:
    if joint in HAND_JOINTS or joint in {"left_wrist", "right_wrist"}:
        return joint.split("_", 1)[0]
    return None


def hand_parent_overrides(
    rotations: Mapping[str, object], indices: Mapping[str, int]
) -> dict[int, int]:
    result = {}
    for key in rotations:
        if "_palm_" in key:
            side, _, number = key.split("_")
            suffix = "L" if side == "left" else "R"
            finger = ("index", "middle", "ring", "pinky")[int(number) - 1]
            result[indices[f"DEF-f_{finger}.01.{suffix}"]] = indices[HAND_JOINTS[key]]
    return result
