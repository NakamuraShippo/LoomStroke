"""Async runner contracts for SAM3 video tracking."""

from __future__ import annotations

from typing import Protocol

from .data_model import BlockedReason
from .video_track_fake_runner import run_fake_video_track_request
from .video_track_request import Sam3VideoTrackRequest
from .video_track_run import Sam3VideoTrackRunResult, build_video_track_run_result


class Sam3VideoTrackRunner(Protocol):
    async def track(self, request: Sam3VideoTrackRequest) -> Sam3VideoTrackRunResult: ...

    async def cancel(self, request_id: str) -> bool: ...

    async def close(self) -> None: ...


class UnavailableSam3VideoTrackRunner:
    """Safe default runner for environments without SAM3 video tracking configured."""

    async def track(self, request: Sam3VideoTrackRequest) -> Sam3VideoTrackRunResult:
        return _blocked_result(
            request,
            code="video_track_runner_unavailable",
            message="SAM3 video track runner is unavailable.",
            action="Configure a SAM3.1 video track runner.",
            provider="unavailable",
            source="sam3_video_track_runner",
        )

    async def cancel(self, request_id: str) -> bool:
        del request_id
        return False

    async def close(self) -> None:
        return None


class FakeSam3VideoTrackRunner:
    """Deterministic async fake video track runner for tests and smoke flows."""

    def __init__(
        self,
        *,
        track_id: str | None = None,
        max_frames: int | None = None,
        include_previews: bool = True,
    ) -> None:
        self._track_id = track_id
        self._max_frames = max_frames
        self._include_previews = include_previews
        self._known_request_ids: set[str] = set()
        self._closed = False

    async def track(self, request: Sam3VideoTrackRequest) -> Sam3VideoTrackRunResult:
        if self._closed:
            return _blocked_result(
                request,
                code="fake_video_track_runner_closed",
                message="SAM3 fake video track runner is closed.",
                action="Create a new fake video track runner.",
                provider="fake",
                source="sam3_fake_video_track_runner",
            )
        result = run_fake_video_track_request(
            request,
            track_id=self._track_id,
            max_frames=self._max_frames,
            include_previews=self._include_previews,
        )
        self._known_request_ids.add(request.request_id)
        return result

    async def cancel(self, request_id: str) -> bool:
        return str(request_id or "").strip() in self._known_request_ids

    async def close(self) -> None:
        self._closed = True


def _blocked_result(
    request: Sam3VideoTrackRequest,
    *,
    code: str,
    message: str,
    action: str,
    provider: str,
    source: str,
) -> Sam3VideoTrackRunResult:
    return build_video_track_run_result(
        request=request,
        status="blocked",
        blocked_reasons=(BlockedReason(code=code, message=message, action=action),),
        recommended_actions=(action,),
        diagnostics={"provider": provider, "source": source},
    )


__all__ = [
    "FakeSam3VideoTrackRunner",
    "Sam3VideoTrackRunner",
    "UnavailableSam3VideoTrackRunner",
]
