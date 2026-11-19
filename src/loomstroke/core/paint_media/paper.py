"""Deterministic paper tooth and water mobility in document coordinates."""

import cv2
import numpy as np

from loomstroke.core.paper_texture_mask import PaperTextureMask

# Sizing on a smoother sheet keeps more water mobile. Rough stock binds it sooner.
WATER_MOBILITY = {"Standard": 1.0, "Smooth": 1.12, "Rough": 0.70}


def sample_paper(rect):
    """Reference watercolor paper, including the halo needed by the grain filter."""
    x, y, w, h = rect.getRect()
    coarse = PaperTextureMask.sample(x - 2, y - 2, w + 4, h + 4, scale=3, strength=0.8, seed=17)
    fine = PaperTextureMask.sample(x, y, w, h, scale=1, strength=0.8, seed=29)
    coarse = cv2.GaussianBlur(coarse, (5, 5), 1.05)[2:-2, 2:-2]
    return 0.72 * coarse + 0.28 * fine


def sample_tooth(rect, paper, grain):
    """How high the surface stands at each pixel, 0..1: where a dry brush still leaves paint."""
    if paper == "Rough":
        return grain  # Already a continuous relief of fibres and hollows.
    # The smoothed weave of the standard surface, spread over the whole range. The halo
    # keeps the filter continuous across tile seams.
    x, y, w, h = rect.getRect()
    weave = PaperTextureMask.sample(x - 2, y - 2, w + 4, h + 4, scale=3, strength=0.8, seed=17)
    weave = cv2.GaussianBlur(weave, (5, 5), 1.05)[2:-2, 2:-2]
    tooth = np.clip((weave - 0.33) / 0.53, 0, 1)
    if paper == "Smooth":
        # A smooth surface has little relief, so paint breaks up on it late.
        tooth = 0.7 + 0.3 * tooth
    return tooth.astype(np.float32)


def sample_grain(rect, medium, paper):
    x, y, w, h = rect.getRect()
    if paper == "Smooth":
        return PaperTextureMask.sample(x, y, w, h, scale=1, strength=0.12, seed=29)
    if paper == "Rough":
        # Form irregular hollows from a continuous fibre field. Add shorter fibres
        # on top, sampling the full filter halo on both sides of each tile seam.
        fibres = PaperTextureMask.sample(x - 5, y - 5, w + 10, h + 10, scale=1, strength=1, seed=41)
        hollows = cv2.GaussianBlur(fibres, (11, 11), sigmaX=1.9, sigmaY=1.45)
        fine = cv2.GaussianBlur(fibres, (5, 3), sigmaX=1.1, sigmaY=0.55)
        tooth = 0.47 + 4.8 * (hollows - 0.5) + 0.9 * (fine - hollows)
        return np.clip(tooth[5:-5, 5:-5], 0, 1)
    if medium == "water":
        return sample_paper(rect)
    return PaperTextureMask.sample(x, y, w, h, scale=3, strength=0.8, seed=17)
