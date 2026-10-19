"""Independent CPU reference and RGB baking for additional layer styles."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from .relief_style_parameters import IDENTITY_PROFILE, evaluate_profile, relief_lights
from .style_blending import style_blend


def gaussian(values: np.ndarray, radius: int) -> np.ndarray:
    if radius <= 0:
        return values.copy()
    sigma = max(radius / 3.0, 1.0 / 3.0)
    offsets = np.arange(-radius, radius + 1, dtype=np.float32)
    weights = np.exp(-0.5 * (offsets / sigma) ** 2)
    weights /= weights.sum()
    result = values
    for axis in (0, 1):
        padding = [(0, 0)] * values.ndim
        padding[axis] = (radius, radius)
        padded = np.pad(result, padding)
        result = np.zeros_like(values)
        for index, weight in enumerate(weights):
            region = [slice(None)] * values.ndim
            region[axis] = slice(index, index + values.shape[axis])
            result += padded[tuple(region)] * weight
    return result


def mix_images(
    source: np.ndarray, blurred: np.ndarray, amount: float, mode: str = "normal"
) -> np.ndarray:
    a = source[..., 3:4] * (1 - amount) + blurred[..., 3:4] * amount
    color = source[..., :3] * source[..., 3:4] * (1 - amount)
    blended = style_blend(blurred[..., :3], source[..., :3], mode)
    target = blurred[..., :3] * (1 - source[..., 3:4]) + blended * source[..., 3:4]
    color += target * blurred[..., 3:4] * amount
    return np.concatenate((color / np.maximum(a, 1e-6), a), axis=-1)


def blur_image(source: np.ndarray, radius: int, opacity: float, mode: str = "normal") -> np.ndarray:
    premul = source.copy()
    premul[..., :3] *= premul[..., 3:4]
    blurred = gaussian(premul, radius)
    blurred[..., :3] /= np.maximum(blurred[..., 3:4], 1e-6)
    return mix_images(source, blurred, opacity, mode)


def gradient_overlay(
    image: np.ndarray,
    params: dict[str, Any],
    opacity: float,
    *,
    origin: tuple[int, int],
    size: tuple[int, int],
    mode: str = "normal",
) -> np.ndarray:
    y, x = np.mgrid[: image.shape[0], : image.shape[1]].astype(np.float32)
    u = (x + origin[0] + 0.5) / size[0] - params["center_x"]
    v = (y + origin[1] + 0.5) / size[1] - params["center_y"]
    angle = math.radians(params["angle"])
    if params["shape"] == "radial":
        t = np.sqrt(u * u + v * v) * 2 / params["scale"]
    else:
        t = (u * math.cos(angle) + v * math.sin(angle)) / params["scale"] + 0.5
    t = np.clip(t[..., None], 0, 1)
    start = np.asarray(params["color_start"], np.float32) / 255
    end = np.asarray(params["color_end"], np.float32) / 255
    alpha = start[3] * (1 - t) + end[3] * t
    color = (start[:3] * start[3] * (1 - t) + end[:3] * end[3] * t) / np.maximum(alpha, 1e-6)
    out = image.copy()
    out[..., :3] = (
        image[..., :3] * (1 - alpha * opacity)
        + style_blend(color, image[..., :3], mode) * alpha * opacity
    )
    return out


def clipped_color(
    image: np.ndarray, mask: np.ndarray, color: list[int], opacity: float, mode: str = "normal"
) -> np.ndarray:
    amount = np.clip(mask[..., None] * opacity * color[3] / 255, 0, 1)
    out = image.copy()
    blended = style_blend(np.asarray(color[:3]) / 255, image[..., :3], mode)
    out[..., :3] = image[..., :3] * (1 - amount) + blended * amount
    return out


def relief_weights(
    height: np.ndarray,
    alpha: np.ndarray,
    params: dict[str, Any],
    three_d: bool,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    # Central differences use a zero outside the finite support, like the GPU.
    padded = evaluate_profile(np.pad(height, 1), params.get("profile", IDENTITY_PROFILE))
    dx = (padded[1:-1, 2:] - padded[1:-1, :-2]) * 0.5
    dy = (padded[2:, 1:-1] - padded[:-2, 1:-1]) * 0.5
    direction = np.ones_like(height)
    if params.get("invert", False):
        direction *= -1
    if params.get("type") == "pillow":
        direction *= np.where(alpha >= 0.5, -1, 1)
    strength = params["depth"] * params["size"]
    nx, ny = -dx * strength * direction, -dy * strength * direction
    norm = np.sqrt(nx * nx + ny * ny + 1)
    nx, ny, nz = nx / norm, ny / norm, 1 / norm
    highlight, shadow, energy = (np.zeros_like(nx) for _ in range(3))
    high_color = np.zeros((*height.shape, 3))
    diffuse_color = np.zeros_like(high_color)
    base_highlight = np.asarray(params["highlight_color"][:3]) / 255
    for source in relief_lights(params):
        if not source["enabled"]:
            continue
        amount = source["intensity"] * source["color"][3] / 255
        angle, elevation = math.radians(source["angle"]), math.radians(source["elevation"])
        light = np.array(
            [
                math.cos(angle) * math.cos(elevation),
                -math.sin(angle) * math.cos(elevation),
                math.sin(elevation),
            ]
        )
        color = np.asarray(source["color"][:3]) / 255
        if three_d:
            diffuse = np.maximum(0, nx * light[0] + ny * light[1] + nz * light[2]) * amount
            energy += diffuse
            diffuse_color += diffuse[..., None] * color
            halfway = light + np.array([0, 0, 1])
            halfway /= np.linalg.norm(halfway)
            specular = np.maximum(0, nx * halfway[0] + ny * halfway[1] + nz * halfway[2])
            positive = params["specular"] * specular ** params["shininess"] * amount
        else:
            directional = (nx * light[0] + ny * light[1]) * amount
            positive = np.maximum(directional, 0)
            shadow += np.maximum(-directional, 0)
        highlight += positive
        high_color += positive[..., None] * base_highlight * color
    tint = np.ones_like(high_color)
    if three_d:
        total = params["ambient"] + params["diffuse"] * energy
        shadow = 1 - np.clip(total, 0, 1)
        tint = np.where(
            total[..., None] > 1e-6,
            (params["ambient"] + params["diffuse"] * diffuse_color)
            / np.maximum(total[..., None], 1e-6),
            tint,
        )
    high_color = np.where(
        highlight[..., None] > 1e-6,
        high_color / np.maximum(highlight[..., None], 1e-6),
        base_highlight,
    )
    return highlight, shadow, high_color, tint


def relief(
    image: np.ndarray,
    base: np.ndarray,
    params: dict[str, Any],
    opacity: float,
    *,
    three_d: bool,
    rasterizer: Any,
    mode: str = "normal",
) -> np.ndarray:
    height = gaussian(base[..., 3], params["size"])
    height = gaussian(height, params["soften"])
    highlight, shadow, highlight_color, tint = relief_weights(height, base[..., 3], params, three_d)
    out = image.copy()
    kind = params.get("type", "inner")
    if not three_d and kind in {"outer", "emboss", "pillow"}:
        ring = np.maximum(rasterizer._dilate(base[..., 3], params["size"]) - base[..., 3], 0)
        hi = np.asarray(params["highlight_color"], np.float32) / 255
        lo = np.asarray(params["shadow_color"], np.float32) / 255
        t = np.clip(0.5 + (highlight - shadow) * 0.5, 0, 1)[..., None]
        weight = np.clip(highlight[..., None], 0, 1)
        lit_color = hi[:3] * (1 - weight) + highlight_color * weight
        color = lo[:3] * (1 - t) + lit_color * t
        color = (
            color * (1 - image[..., 3:4])
            + style_blend(color, image[..., :3], mode) * image[..., 3:4]
        )
        a = ring[..., None] * opacity * (lo[3] * (1 - t) + hi[3] * t)
        under = np.concatenate((color, a), axis=-1)
        out = np.asarray(rasterizer._over(under, image))
        if kind == "outer":
            return out
    out[..., :3] = (
        out[..., :3] * (1 - opacity)
        + style_blend(out[..., :3] * tint, out[..., :3], mode) * opacity
    )
    out = clipped_color(out, shadow, params["shadow_color"], opacity, mode)
    amount = np.clip(highlight[..., None] * opacity * params["highlight_color"][3] / 255, 0, 1)
    out[..., :3] = (
        out[..., :3] * (1 - amount) + style_blend(highlight_color, out[..., :3], mode) * amount
    )
    return out
