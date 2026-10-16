"""Validate bounded normalized native box batches, retaining the single-box protocol."""

from __future__ import annotations

import math
from typing import Any

from loomstroke.sam31 import worker_protocol as protocol

from .runtime import WorkerError


def boxes_and_labels(request: dict[str, Any]) -> tuple[list[list[float]], list[int]]:
    boxes = request.get("boxesXywh")
    labels = request.get("positives")
    if boxes is None:
        box = request.get("boxXywh")
        boxes = [box] if box is not None else []
        labels = [request.get("positive", True)]
    if (
        not isinstance(boxes, list)
        or not 1 <= len(boxes) <= 32
        or not isinstance(labels, list)
        or len(labels) != len(boxes)
        or any(not isinstance(value, bool) for value in labels)
    ):
        raise WorkerError(
            protocol.ERR_INVALID_REQUEST,
            "Provide 1-32 normalized boxes and equally many boolean labels.",
        )
    normalized = []
    for box in boxes:
        if (
            not isinstance(box, (list, tuple))
            or len(box) != 4
            or any(
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(value)
                for value in box
            )
        ):
            raise WorkerError(
                protocol.ERR_INVALID_REQUEST,
                "Normalized box coordinates must be four finite numbers.",
            )
        x, y, width, height = [float(value) for value in box]
        if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 1 or y + height > 1:
            raise WorkerError(
                protocol.ERR_INVALID_REQUEST,
                "Normalized boxes must have positive area inside the image.",
            )
        normalized.append([x, y, width, height])
    return normalized, [1 if value else 0 for value in labels]
