"""Restore expired image objects from the caller's persisted mask and prompts."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from loomstroke.ai_selection.data_model import BlockedReason, Sam3Readiness
from loomstroke.ai_selection.worker_client import AiSelectionWorkerUnavailable

from . import worker_protocol as protocol

PointHistory = tuple[tuple[float, float, int], ...]


def saved_history(asset: Any) -> PointHistory:
    state = asset.metadata.get("sam31Refine", {})
    if not isinstance(state, dict):
        return ()
    history = []
    values = state.get("points", ())
    if not isinstance(values, (list, tuple)):
        return ()
    for value in values:
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            continue
        x, y, label = value
        if (
            isinstance(x, (int, float))
            and isinstance(y, (int, float))
            and label in (0, 1)
            and math.isfinite(x)
            and math.isfinite(y)
            and 0 <= x <= 1
            and 0 <= y <= 1
        ):
            history.append((float(x), float(y), int(label)))
    return tuple(history)


def saved_text(asset: Any) -> str:
    state = asset.metadata.get("sam31Refine", {})
    value = state.get("text", "") if isinstance(state, dict) else ""
    return value.strip() if isinstance(value, str) else ""


def recover_refine(
    command: Callable[[str, dict[str, Any]], dict[str, Any]],
    *,
    base: dict[str, Any],
    asset: Any,
    store: Any,
    history: PointHistory,
    points: list[list[float]],
    labels: list[int],
    text: str,
    width: int,
    height: int,
) -> dict[str, Any]:
    import numpy as np  # noqa: PLC0415
    from PIL import Image  # noqa: PLC0415

    with Image.open(store.mask_asset_path(asset)) as image:
        coverage = np.minimum(
            np.asarray(image.convert("L")), np.asarray(image.convert("RGBA"))[..., 3]
        )
    ys, xs = np.nonzero(coverage)
    if not len(xs):
        raise _unavailable("The selected mask is empty; its refinement state cannot be restored.")
    anchors = [[x, y] for x, y, label in history if label]
    if not anchors:
        index = int(np.argmin((xs - xs.mean()) ** 2 + (ys - ys.mean()) ** 2))
        anchors = [
            [
                (asset.bbox[0] + int(xs[index]) + 0.5) / width,
                (asset.bbox[1] + int(ys[index]) + 0.5) / height,
            ]
        ]
    seed = command(
        protocol.CMD_SEGMENT_POINT,
        {
            **base,
            "points": anchors,
            "labels": [1] * len(anchors),
            "resetSession": True,
            # Use the bridge's existing lower bound so recovery does not discard
            # a low-confidence concept accepted by the original request.
            **({"prompt": text, "probThresh": 0.05} if text else {}),
        },
    )
    objects = seed.get("objects") or []
    if not objects:
        raise _unavailable(
            "The previous selection could not be restored from its mask and prompts."
        )
    object_id = seed.get("primaryObjectId")
    if object_id is None:
        x, y, w, h = asset.bbox
        best_overlap = 0
        for entry in objects:
            if entry.get("objectId") is None or not entry.get("maskPath"):
                continue
            with Image.open(entry["maskPath"]) as image:
                full = np.asarray(image.convert("L")) > 0
            if full.shape != (height, width):
                continue
            overlap = int(np.count_nonzero(full[y : y + h, x : x + w] & (coverage > 0)))
            if overlap > best_overlap:
                best_overlap, object_id = overlap, entry["objectId"]
    if object_id is None:
        raise _unavailable("The restored objects do not overlap the selected mask.")
    return command(
        protocol.CMD_REFINE_MASK,
        {
            **base,
            "objectId": int(object_id),
            "points": points,
            "labels": labels,
            "baseMaskPath": str(store.mask_asset_path(asset)),
            "baseMaskBbox": list(asset.bbox),
            "basePoints": [[x, y] for x, y, _label in history],
            "baseLabels": [label for _x, _y, label in history],
        },
    )


def _unavailable(message: str) -> AiSelectionWorkerUnavailable:
    action = "Run AI Select again for the intended object."
    return AiSelectionWorkerUnavailable(
        message,
        readiness=Sam3Readiness(
            state="blocked",
            runtime="sam3_1",
            blocked_reasons=(
                BlockedReason(
                    code="sam31_refine_recovery_unavailable", message=message, action=action
                ),
            ),
            recommended_actions=(action,),
        ),
    )
