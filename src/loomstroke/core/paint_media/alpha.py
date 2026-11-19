"""Composite brush coverage onto premultiplied BGRA8 working tiles."""

import numpy as np


def deposit(pixels, color_bgr, coverage):
    """Apply straight BGR paint with source-over coverage; keep RGB and alpha in step."""
    coverage = np.clip(np.asarray(coverage, dtype=np.float32), 0.0, 1.0)
    color = np.asarray(color_bgr, dtype=np.float32)
    # Per-channel 2D arithmetic also handles per-pixel colour and strided edge tiles.
    for channel in range(3):
        old = pixels[..., channel]
        old[:] = np.rint(old + (color[..., channel] - old) * coverage)
    alpha = pixels[..., 3]
    alpha[:] = np.rint(alpha + (255.0 - alpha) * coverage)


def restore_coverage(pixels, original_alpha):
    """Keep the new straight colour at the original alpha on an alpha-locked layer."""
    new_alpha = pixels[..., 3]
    changed = new_alpha != original_alpha
    if changed.any():
        source = new_alpha[changed].astype(np.uint16)[:, None]
        target = original_alpha[changed].astype(np.uint16)[:, None]
        rgb = pixels[..., :3][changed].astype(np.uint16)
        pixels[..., :3][changed] = (rgb * target + source // 2) // np.maximum(source, 1)
        new_alpha[changed] = original_alpha[changed]
