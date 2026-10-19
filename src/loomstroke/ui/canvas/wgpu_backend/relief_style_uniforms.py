"""Fixed-size relief uniforms shared by RGB and native CMYK style passes."""

import math
from collections.abc import Callable
from typing import Any

from loomstroke.core.appearance.relief_style_parameters import (
    IDENTITY_PROFILE,
    MAX_LIGHTS,
    MAX_PROFILE_POINTS,
    profile_knots,
    relief_lights,
)

EMPTY_RELIEF = (0.0,) * 200


def relief_uniforms(
    params: dict[str, Any], convert: Callable[..., tuple[float, ...]]
) -> tuple[float, ...]:
    profile = params.get("profile", IDENTITY_PROFILE)
    knots = profile_knots(profile)
    lights = relief_lights(params)
    data: list[float] = [len(knots), float(profile["interpolation"] == "smooth"), 0, 0]
    for knot in knots:
        data.extend(knot)
    data.extend([0.0] * (4 * (MAX_PROFILE_POINTS - len(knots))))
    data.extend([len(lights), 0, 0, 0])
    colors: list[float] = []
    native = len(convert([255, 255, 255, 255])) == 4
    for light in lights:
        color = light["color"]
        amount = light["intensity"] * color[3] / 255 if light["enabled"] else 0
        data.extend([math.radians(light["angle"]), math.radians(light["elevation"]), amount, 0])
        # A neutral light must preserve existing ink values exactly, even with an ICC white offset.
        converted = (
            ((0.0,) * 4 if native else (1.0,) * 3) if color[:3] == [255] * 3 else convert(color)
        )
        colors.extend((*converted, 0.0, 0.0, 0.0, 0.0)[:4])
    data.extend([0.0] * (4 * (MAX_LIGHTS - len(lights))))
    colors.extend([0.0] * (4 * (MAX_LIGHTS - len(lights))))
    return tuple(data + colors)
