"""Deterministic fake runner for SAM3 Tier 6 video track requests."""

from __future__ import annotations

import re

from .video_track_request import Sam3VideoTrackRequest
from .video_track_run import Sam3VideoTrackRunResult, build_video_track_run_result
from .video_tracker_model import Sam3VideoMaskFrame, Sam3VideoTrackResult

_SAFE_ID_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")


class Sam3VideoTrackFakeRunnerError(ValueError):
    """Raised when a fake SAM3 video track run cannot be built."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def run_fake_video_track_request(
    request: Sam3VideoTrackRequest,
    *,
    track_id: str | None = None,
    max_frames: int | None = None,
    include_previews: bool = True,
) -> Sam3VideoTrackRunResult:
    """Create a deterministic fake video track run result for tests and smoke flows."""

    if not isinstance(request, Sam3VideoTrackRequest):
        raise Sam3VideoTrackFakeRunnerError(
            "video_track_fake_request_invalid",
            "SAM3 fake video track runner requires a video track request.",
        )

    source_frames = request.source_manifest.frames
    requested_count = len(source_frames) if max_frames is None else max(0, int(max_frames))
    frame_count = min(len(source_frames), requested_count)
    normalized_track_id = _safe_id(track_id or f"{request.request_id}_fake_track", fallback="track")
    frames = tuple(
        _fake_frame(
            request,
            track_id=normalized_track_id,
            frame_index=index,
            include_previews=include_previews,
        )
        for index in range(frame_count)
    )
    track_result = Sam3VideoTrackResult(
        track_id=normalized_track_id,
        source_video_asset_id=request.source_manifest.source_asset_id,
        objects=request.objects,
        frames=frames,
        fps=request.source_manifest.fps,
        width=request.source_manifest.width,
        height=request.source_manifest.height,
        diagnostics={
            "provider": "fake",
            "source": "sam3_fake_video_track_runner",
            "frameCount": frame_count,
            "sourceFrameCount": len(source_frames),
        },
    )
    status = "completed" if frame_count >= len(source_frames) else "cancelled"
    return build_video_track_run_result(
        request=request,
        status=status,
        track_result=track_result,
        recommended_actions=("keep_partial_package",) if status == "cancelled" else (),
        diagnostics={
            "provider": "fake",
            "source": "sam3_fake_video_track_runner",
            "frameCount": frame_count,
            "sourceFrameCount": len(source_frames),
        },
    )


def _fake_frame(
    request: Sam3VideoTrackRequest,
    *,
    track_id: str,
    frame_index: int,
    include_previews: bool,
) -> Sam3VideoMaskFrame:
    source_frame = request.source_manifest.frames[frame_index]
    object_masks = {
        obj.object_id: _fake_mask_asset_id(track_id, frame_index, obj.object_id)
        for obj in request.objects
    }
    preview_asset_id = (
        f"sam3-fake-video-preview-{track_id}-{frame_index + 1:06d}" if include_previews else None
    )
    return Sam3VideoMaskFrame(
        frame_index=source_frame.frame_index,
        timestamp_ms=source_frame.timestamp_ms,
        object_masks=object_masks,
        preview_asset_id=preview_asset_id,
    )


def _fake_mask_asset_id(track_id: str, frame_index: int, object_id: str) -> str:
    safe_object_id = _safe_id(object_id, fallback="object")
    return f"sam3-fake-video-mask-{track_id}-{frame_index + 1:06d}-{safe_object_id}"


def _safe_id(value: object, *, fallback: str) -> str:
    normalized = _SAFE_ID_PATTERN.sub("-", str(value or "").strip()).strip(".-_")
    if not normalized:
        normalized = fallback
    return normalized[:80]


__all__ = [
    "Sam3VideoTrackFakeRunnerError",
    "run_fake_video_track_request",
]
