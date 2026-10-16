"""Optional bounded per-frame mask persistence for the existing video protocol."""

from __future__ import annotations

from typing import Any

from loomstroke.sam31 import worker_protocol as protocol

from .runtime import WorkerError

MAX_VIDEO_MASK_PIXELS = 67_108_864


def persist_frame(
    runtime: Any, outputs: dict[str, Any], directory: Any, remaining_pixels: int
) -> tuple[dict[str, Any], int]:
    import numpy as np  # noqa: PLC0415

    from .image_api import _write_outputs  # noqa: PLC0415

    masks = np.asarray(outputs.get("out_binary_masks"))
    if masks.size > remaining_pixels:
        raise WorkerError(
            protocol.ERR_INVALID_REQUEST, "Video mask outputs exceed the 64 MP aggregate limit."
        )
    if not masks.size:
        return {"objects": [], "maskEncoding": "grayscale_coverage_png"}, 0
    if masks.ndim != 3 or masks.shape[0] > 32:
        raise WorkerError(
            protocol.ERR_INVALID_REQUEST,
            "A video frame supports at most 32 image-sized object masks.",
        )
    return _write_outputs(runtime, outputs, directory), int(masks.size)
