"""Expose native single-click scopes without changing the predictor's chosen mask.

The observer lives for one synchronous still-image request only. It does not
change model outputs, weights, the tracker state, or the video inference path.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import numpy as np

from .point_mask_policy import Point, _component

logger = logging.getLogger(__name__)
_DUPLICATE_IOU = 0.98
_MIN_CONNECTED_SHARE = 0.8


def _point_component(mask: np.ndarray, points: tuple[Point, ...]) -> np.ndarray:
    """A single click targets its connected scope, not distant prediction islands."""
    if len(points) != 1 or points[0][2] != 1:
        return mask
    height, width = mask.shape
    x, y, _label = points[0]
    seed = (min(height - 1, int(y * height)), min(width - 1, int(x * width)))
    # Do not guess a replacement seed when the model misses the exact click.
    if not mask[seed]:
        return mask
    connected = _component(mask, seed, diagonal=True)
    # Disconnected substantial parts may be occluded clothing/accessories.
    # Only clean a scope dominated by the clicked component, not split objects.
    return connected if connected.sum() >= _MIN_CONNECTED_SHARE * mask.sum() else mask


@contextmanager
def capture_alternatives(predictor: Any, shape: tuple[int, int]) -> Iterator[list[np.ndarray]]:
    """Observe the multiplex SAM head and restore the exact binding in finally."""
    model = getattr(getattr(getattr(predictor, "model", None), "tracker", None), "model", None)
    original = getattr(model, "_forward_sam_heads", None)
    masks: list[np.ndarray] = []
    if not callable(original):
        logger.warning("SAM31 point alternatives unavailable: unsupported tracker interface")
        yield masks
        return
    had_binding = "_forward_sam_heads" in vars(model)
    binding = vars(model).get("_forward_sam_heads")

    def observe(*args: Any, **kwargs: Any) -> Any:
        outputs = original(*args, **kwargs)
        logits = outputs.get("high_res_multimasks") if isinstance(outputs, dict) else None
        if logits is None or logits.ndim != 4 or logits.shape[:2] != (1, 3):
            logger.warning("SAM31 point alternatives unavailable: unexpected SAM head output")
            return outputs
        from torch.nn import functional  # noqa: PLC0415

        resized = functional.interpolate(
            logits.detach().float(), size=shape, mode="bilinear", align_corners=False
        )
        masks[:] = list((resized[0] > 0).cpu().numpy())
        return outputs

    model._forward_sam_heads = observe
    try:
        yield masks
    finally:
        if had_binding:
            model._forward_sam_heads = binding
        else:
            delattr(model, "_forward_sam_heads")


def expand_outputs(
    outputs: dict[str, Any], masks: list[np.ndarray], points: tuple[Point, ...], object_id: int
) -> dict[str, Any]:
    """Preferred mask first, distinct wider alternatives next; all share one object.

    Native IoU-head values are not calibrated probabilities, so they are not used
    as a threshold or exposed as confidence. Every scope inherits the same object
    score; choosing a scope is a user decision through the existing candidate UI.
    """
    indices = np.flatnonzero(np.asarray(outputs["out_obj_ids"]) == object_id)
    if len(indices) != 1 or not masks:
        return outputs
    index = int(indices[0])
    original = np.asarray(outputs["out_binary_masks"][index], dtype=bool)
    preferred = _point_component(original, points)
    height, width = preferred.shape
    retained = [preferred]
    for proposal in sorted(masks, key=lambda value: int(value.sum()), reverse=True):
        if proposal.shape != preferred.shape:
            raise ValueError("point alternative dimensions differ from the source image")
        mask = _point_component(proposal, points)
        if not mask.any() or not all(
            bool(mask[min(height - 1, int(y * height)), min(width - 1, int(x * width))])
            == bool(label)
            for x, y, label in points
        ):
            continue
        if any(
            np.count_nonzero(mask & previous) >= _DUPLICATE_IOU * np.count_nonzero(mask | previous)
            for previous in retained
        ):
            continue
        retained.append(mask)
    if len(retained) == 1 and np.array_equal(preferred, original):
        return outputs
    boxes = []
    for mask in retained:
        ys, xs = np.nonzero(mask)
        boxes.append(
            [
                xs.min() / width,
                ys.min() / height,
                (xs.max() - xs.min() + 1) / width,
                (ys.max() - ys.min() + 1) / height,
            ]
            if len(xs)
            else [0.0, 0.0, 0.0, 0.0]
        )
    return {
        **outputs,
        "out_binary_masks": np.array(retained),
        "out_obj_ids": np.full(len(retained), object_id),
        "out_probs": np.full(len(retained), outputs["out_probs"][index]),
        "out_boxes_xywh": np.array(boxes),
        "out_point_variants": ["model_preferred", *(["alternative"] * (len(retained) - 1))],
    }
