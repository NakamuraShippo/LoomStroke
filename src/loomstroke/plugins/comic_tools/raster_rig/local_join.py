"""Conservative colour reconstruction inside overlapping junction ink only."""

import cv2
import numpy as np


def reconstruct_join(output, body, tail, stroke):
    """Return a copy and edited mask. Never changes alpha or outer coverage.

    Inputs are one small common ROI in straight RGBA. Distinct ink colours,
    translucent body, missing stroke evidence: leave the existing join alone.
    This is colour repair, not a new geometric solver or inpainting service.
    """
    result = output.copy()
    changed = np.zeros(output.shape[:2], bool)
    bcore = (body[:, :, 3] >= 240) & (stroke > 0.8)
    tcore = tail[:, :, 3] >= 240
    if not bcore.any() or not tcore.any():
        return result, changed
    bc = np.median(body[bcore, :3], axis=0)
    tc = np.median(tail[tcore, :3], axis=0)
    if np.linalg.norm(bc - tc) > 48:
        return result, changed
    # Exclude ink outliers (ornament/highlight/fill), then average only core ink.
    bcore &= np.linalg.norm(body[:, :, :3].astype(float) - bc, axis=2) < 48
    tcore &= np.linalg.norm(tail[:, :, :3].astype(float) - tc, axis=2) < 48
    weights = bcore.astype(np.float32) + tcore.astype(np.float32)
    numerator = body[:, :, :3].astype(np.float32) * bcore[:, :, None]
    numerator += tail[:, :, :3].astype(np.float32) * tcore[:, :, None]
    weight = cv2.GaussianBlur(weights, (9, 9), 2)
    rgb = cv2.GaussianBlur(numerator, (9, 9), 2) / np.maximum(weight[:, :, None], 1e-6)
    # A tail fringe sitting on opaque body INK is internal, not an outer edge.
    candidate = bcore & (tail[:, :, 3] > 8) & (weight > 0.05)
    changed = candidate & np.any(output[:, :, :3] != np.rint(rgb), axis=2)
    result[candidate, :3] = np.clip(np.rint(rgb[candidate]), 0, 255).astype(np.uint8)
    return result, changed
