"""Bounded registered-frame preparation and regular SAM asset/video-model conversion."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any

from PIL import Image

from loomstroke.ai_selection.data_model import Sam3Prompt
from loomstroke.ai_selection.video_tracker_model import (
    Sam3VideoMaskFrame,
    Sam3VideoTrackObject,
    Sam3VideoTrackResult,
)

from .editing_common import invalid
from .sam_asset_common import MAX_AGGREGATE_MASK_PIXELS
from .types import McpStructuredError, ReasonCode


def prepare_frames(payloads: list[bytes], directory: Path) -> tuple[int, int]:
    size = None
    total = 0
    for index, payload in enumerate(payloads):
        with Image.open(io.BytesIO(payload)) as image:
            current = image.size
            total += image.width * image.height
            if total > MAX_AGGREGATE_MASK_PIXELS or (size is not None and current != size):
                raise invalid(
                    "Video frames must share a size and total at most 64 MP.", "frameAssetIds"
                )
            size = current
            image.convert("RGB").save(directory / f"{index:06d}.jpg", quality=100, subsampling=0)
    return size


def save_source_sequence(
    store: Any, track_id: str, ids: list[str], payloads: list[bytes], fps: float
) -> dict[str, Any]:
    """Preserve ordered frame snapshots using the existing source artifact contract."""
    snapshot_ids = [f"{track_id}-source"] + [
        f"{track_id}-source-f{index}" for index in range(1, len(ids))
    ]
    sequence = {
        "frameAssetIds": ids,
        "frameSnapshotIds": snapshot_ids,
        "frameContentHashes": [hashlib.sha256(payload).hexdigest() for payload in payloads],
        "fps": fps,
    }
    for index, payload in enumerate(payloads):
        with Image.open(io.BytesIO(payload)) as image:
            output = io.BytesIO()
            image.convert("RGBA").save(output, format="PNG")
            store.save_source_snapshot(
                output.getvalue(),
                snapshot_id=snapshot_ids[index],
                kind="video_sequence" if index == 0 else "video_sequence_frame",
                width=image.width,
                height=image.height,
                content_hash=sequence["frameContentHashes"][index],
                metadata={"trackId": track_id, "frameIndex": index, "sourceSequence": sequence},
            )
    return sequence


def materialize_video(
    store: Any,
    result: Any,
    directory: Path,
    track_id: str,
    frame_count: int,
    size: tuple[int, int],
    fps: float,
    text: str,
    output_ids: list[str],
    source_sequence: dict[str, Any],
) -> Sam3VideoTrackResult:
    width, height = size
    objects: dict[str, Sam3VideoTrackObject] = {}
    frames = []
    pixel_count = 0
    raw_frames = result.get("frames")
    if result.get("maskPersistence") != "grayscale_coverage_png" or not isinstance(
        raw_frames, dict
    ):
        raise McpStructuredError(
            ReasonCode.CAPABILITY_UNAVAILABLE,
            "Restart the local SAM31 worker to enable video mask persistence.",
        )
    for raw_index, frame in sorted(raw_frames.items(), key=lambda item: int(item[0])):
        index = int(raw_index)
        if index < 0 or index >= frame_count:
            raise invalid("The worker returned an out-of-range video frame.")
        masks = {}
        for entry in frame.get("objects", []):
            native_id = str(int(entry["objectId"]))
            object_id = f"object-{native_id}"
            path = Path(entry["maskPath"]).resolve()
            if not path.is_relative_to(directory.resolve()):
                raise McpStructuredError(
                    ReasonCode.PERMISSION_DENIED, "The worker mask escaped its output directory."
                )
            with Image.open(path) as image:
                pixel_count += image.width * image.height
                if image.size != size or pixel_count > MAX_AGGREGATE_MASK_PIXELS:
                    raise invalid("The worker video masks exceeded their shape or 64 MP limit.")
            asset_id = f"{track_id}-f{index}-{object_id}"
            store.save_mask_asset(
                path.read_bytes(),
                asset_id=asset_id,
                kind="alpha_mask",
                width=width,
                height=height,
                bbox=(0, 0, width, height),
                metadata={"backend": "sam3_1", "videoFrameIndex": index, "trackId": track_id},
            )
            output_ids.append(asset_id)
            masks[object_id] = asset_id
            objects.setdefault(
                object_id,
                Sam3VideoTrackObject(object_id, text, Sam3Prompt(mode="text", text=text), index),
            )
        frames.append(Sam3VideoMaskFrame(index, round(1000 * index / fps), masks))
    if len(frames) != frame_count:
        raise McpStructuredError(
            ReasonCode.BACKEND_FAILED, "The worker did not return every requested video frame."
        )
    return Sam3VideoTrackResult(
        track_id,
        source_sequence["frameSnapshotIds"][0],
        tuple(objects.values()),
        tuple(frames),
        fps,
        width,
        height,
        diagnostics={"backend": "sam3_1", "sourceSequence": source_sequence},
    )
