"""Deterministic variations that remain identical at incremental command seams."""

from __future__ import annotations

import colorsys
import hashlib
import math
import random
from dataclasses import asdict, replace

from .dynamic_stroke_point import DynamicStrokePoint


class DabVariation:
    @staticmethod
    def apply(points, settings, *, size: float, color: tuple[int, int, int], stroke_id: int):
        varied = []
        for point in points:
            seed = f"{settings['variation_seed']}:{stroke_id}:{point.x}:{point.y}:{point.timestamp}"
            rng = random.Random(
                int.from_bytes(hashlib.blake2b(seed.encode(), digest_size=8).digest(), "big")
            )
            distance = size * settings["scatter"] * math.sqrt(rng.random())
            angle = rng.uniform(0.0, math.tau)
            hue, saturation, value = colorsys.rgb_to_hsv(*(channel / 255 for channel in color))
            hue += rng.uniform(-0.5, 0.5) * settings["color_variation"]
            rgb = colorsys.hsv_to_rgb(hue % 1.0, saturation, value)
            varied.append(
                DynamicStrokePoint(
                    **asdict(point),
                    size_scale=rng.uniform(
                        1 - settings["size_variation"], 1 + settings["size_variation"]
                    ),
                    tip_angle=rng.uniform(-180, 180) * settings["angle_variation"],
                    dab_color=tuple(round(channel * 255) for channel in rgb),
                )
            )
            varied[-1] = replace(
                varied[-1],
                x=point.x + distance * math.cos(angle),
                y=point.y + distance * math.sin(angle),
            )
        return varied

    @staticmethod
    def size(brush, point) -> int:
        return max(
            1,
            round(
                getattr(brush, "dab_size", brush.size)
                * point.pressure
                * getattr(point, "width_scale", 1.0)
                * getattr(point, "size_scale", 1.0)
                * 2
            ),
        )
