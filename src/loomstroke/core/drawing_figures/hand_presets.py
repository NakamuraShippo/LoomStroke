"""Authored hand shape starting points; no object contact or wrist changes."""

from __future__ import annotations

from dataclasses import dataclass

FiveAngles = tuple[float, float, float, float, float]
JointOverride = tuple[str, int, tuple[float, float, float]]

PRESET_CATEGORIES = {
    "basic": "Basic hand poses",
    "gesture": "Hand gestures",
    "counting": "Finger counting",
    "grip": "Grasping poses",
    "expression": "Expressive hands",
}


@dataclass(frozen=True)
class HandPreset:
    label: str
    category: str
    curls: FiveAngles
    spreads: FiveAngles = (0, 0, 0, 0, 0)
    cup: float = 0
    overrides: tuple[JointOverride, ...] = ()
    mirror_lateral_axes: bool = True


def _pinch(
    thumb: tuple[float, float, float],
    thumb_curls: tuple[float, float],
    index_curls: tuple[float, float, float],
) -> tuple[JointOverride, ...]:
    """Authored opposition with independent bends instead of uniform finger curl."""
    return (
        ("thumb", 1, thumb),
        ("thumb", 2, (thumb_curls[0], 0, 0)),
        ("thumb", 3, (thumb_curls[1], 0, 0)),
        *(("index", joint, (angle, 0, 0)) for joint, angle in enumerate(index_curls, 1)),
    )


PRESET_DEFINITIONS = {
    # Existing IDs and numerical poses stay unchanged.
    "open": HandPreset("Open hand", "basic", (0, 0, 0, 0, 0), mirror_lateral_axes=False),
    "relaxed": HandPreset(
        "Relaxed hand", "basic", (12.6, 18, 18, 18, 18), mirror_lateral_axes=False
    ),
    "fist": HandPreset("Fist", "basic", (85 * 0.7, 85, 85, 85, 85), mirror_lateral_axes=False),
    "point": HandPreset(
        "Pointing hand", "gesture", (85 * 0.7, 0, 85, 85, 85), mirror_lateral_axes=False
    ),
    "peace": HandPreset(
        "Peace sign",
        "gesture",
        (85 * 0.7, 0, 0, 85, 85),
        (0, -12, 12, 0, 0),
        mirror_lateral_axes=False,
    ),
    "fingers_together": HandPreset(
        "Fingers together", "basic", (0, -5, -5, -5, -5), (0, -8, 0, 6, 12)
    ),
    "fingers_spread": HandPreset("Fingers spread", "basic", (0, 0, 0, 0, 0), (25, 15, 0, -10, -22)),
    "flat_hand": HandPreset("Flat hand", "basic", (-5, -10, -10, -10, -10)),
    "soft_fist": HandPreset("Soft fist", "basic", (35, 45, 55, 55, 45)),
    "tight_fist": HandPreset("Tight fist", "basic", (65, 95, 100, 95, 90)),
    "half_closed": HandPreset("Half-closed hand", "basic", (20, 35, 40, 45, 50)),
    "cupped": HandPreset("Cupped hand", "basic", (10, 25, 30, 35, 40), cup=20),
    "palm_hollow": HandPreset("Deeply cupped hand", "basic", (15, 45, 50, 55, 60), cup=30),
    "thumbs_up": HandPreset(
        "Thumbs up", "gesture", (0, 85, 85, 85, 85), overrides=(("thumb", 1, (0, -15, -15)),)
    ),
    "ok_sign": HandPreset(
        "OK sign",
        "gesture",
        (40, 55, 0, 5, 10),
        (0, 0, 0, -8, -16),
        overrides=_pinch((5, -17, 5), (37, 20), (35, 45, 40)),
    ),
    "horns": HandPreset("Horns sign", "gesture", (55, 0, 85, 85, 0), (0, 8, 0, 0, -15)),
    "shaka": HandPreset("Shaka sign", "gesture", (0, 85, 85, 85, 0), (25, 0, 0, 0, -20)),
    "three_fingers": HandPreset(
        "Three extended fingers", "gesture", (55, 0, 0, 0, 85), (0, 10, 0, -10, 0)
    ),
    "four_fingers": HandPreset(
        "Four extended fingers", "gesture", (55, 0, 0, 0, 0), (0, 10, 0, -8, -16)
    ),
    "thumb_index": HandPreset("Thumb and index", "gesture", (0, 0, 85, 85, 85), (20, 10, 0, 0, 0)),
    "thumb_index_middle": HandPreset(
        "Thumb, index and middle", "gesture", (0, 0, 0, 85, 85), (20, 10, -10, 0, 0)
    ),
    "crossed_fingers": HandPreset(
        "Crossed fingers",
        "gesture",
        (55, 10, 10, 85, 85),
        (0, -15, 15, 0, 0),
        overrides=(("middle", 2, (25, 0, 0)),),
    ),
    "finger_heart": HandPreset(
        "Finger heart",
        "gesture",
        (35, 60, 85, 85, 85),
        overrides=_pinch((9, -2, 0), (17, 29), (7, 60, 50)),
    ),
    "beckon": HandPreset(
        "Beckoning finger",
        "gesture",
        (55, 45, 85, 85, 85),
        overrides=(("index", 2, (90, 0, 0)), ("index", 3, (60, 0, 0))),
    ),
    "stop": HandPreset("Stop gesture", "gesture", (-5, -5, -5, -5, -5), (20, 12, 0, -8, -18)),
    "count_one": HandPreset("Count one", "counting", (45, -5, 90, 90, 85)),
    "count_two": HandPreset("Count two", "counting", (40, -5, -5, 90, 85), (0, 5, -5, 0, 0)),
    "count_three": HandPreset("Count three", "counting", (45, -5, -5, -5, 85), (0, 6, 0, -6, 0)),
    "count_four": HandPreset("Count four", "counting", (50, -5, -5, -5, -5), (0, 8, 0, -6, -12)),
    "count_five": HandPreset("Count five", "counting", (0, 0, 0, 0, 0), (30, 20, 0, -15, -30)),
    "pinch_small": HandPreset(
        "Small pinch",
        "grip",
        (40, 45, 45, 60, 70),
        overrides=_pinch((5, -17, 5), (37, 20), (35, 45, 40)),
    ),
    "pinch_wide": HandPreset(
        "Wide pinch", "grip", (20, 20, 55, 65, 75), overrides=(("thumb", 1, (10, -20, -10)),)
    ),
    "tripod_pinch": HandPreset(
        "Tripod pinch",
        "grip",
        (40, 40, 40, 65, 75),
        overrides=_pinch((9, -30, 5), (31, 29), (42, 45, 25)),
    ),
    "precision_grip": HandPreset(
        "Precision grip",
        "grip",
        (30, 55, 65, 65, 60),
        overrides=_pinch((9, -30, 5), (31, 29), (42, 45, 25)),
    ),
    "pen_grip": HandPreset(
        "Pen grip",
        "grip",
        (40, 50, 45, 70, 75),
        overrides=_pinch((25, -30, 20), (10, 20), (68, 15, 10)),
    ),
    "brush_grip": HandPreset(
        "Brush grip",
        "grip",
        (30, 35, 35, 50, 65),
        overrides=_pinch((9, -16, 5), (34, 28), (34, 60, 36)),
    ),
    "cylinder_grip": HandPreset("Cylinder grip", "grip", (50, 65, 70, 75, 80), cup=15),
    "thin_handle": HandPreset("Thin handle grip", "grip", (65, 90, 90, 90, 85), cup=10),
    "ball_grip": HandPreset("Ball grip", "grip", (15, 35, 45, 45, 35), (25, 15, 0, -12, -22), 20),
    "wide_ball": HandPreset(
        "Large ball grip", "grip", (10, 20, 30, 35, 25), (30, 20, 0, -15, -28), 15
    ),
    "hook_grip": HandPreset(
        "Hook grip",
        "grip",
        (0, 0, 0, 0, 0),
        overrides=tuple(
            (finger, joint, (angle, 0, 0))
            for finger in ("index", "middle", "ring", "little")
            for joint, angle in ((2, 80), (3, 65))
        ),
    ),
    "key_pinch": HandPreset(
        "Key pinch",
        "grip",
        (40, 40, 80, 85, 85),
        overrides=(("thumb", 1, (15, -45, 20)), ("index", 1, (30, 10, 0))),
    ),
    "claw": HandPreset(
        "Claw hand",
        "expression",
        (35, 25, 25, 25, 25),
        (20, 15, 0, -12, -22),
        10,
        tuple(
            (finger, joint, (angle, 0, 0))
            for finger in ("index", "middle", "ring", "little")
            for joint, angle in ((2, 75), (3, 60))
        ),
    ),
    "gentle_touch": HandPreset(
        "Gentle touch", "expression", (0, 15, 20, 25, 30), (8, 4, 0, -4, -8)
    ),
    "fingertip_touch": HandPreset("Fingertip touch", "expression", (20, 20, 25, 35, 45), cup=10),
    "pinch_sleeve": HandPreset(
        "Fabric pinch",
        "expression",
        (45, 70, 55, 65, 70),
        overrides=_pinch((5, -17, 5), (37, 20), (35, 45, 40)),
    ),
    "delicate_hand": HandPreset(
        "Delicate hand", "expression", (10, 0, 15, 30, 45), (10, 8, 0, -6, -12)
    ),
    "tense_hand": HandPreset(
        "Tense hand", "expression", (-10, -10, -10, -10, -5), (35, 20, 0, -15, -25), -10
    ),
}
HAND_PRESETS = tuple(PRESET_DEFINITIONS)
