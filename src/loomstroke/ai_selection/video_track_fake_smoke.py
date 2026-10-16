"""End-to-end fake smoke helper for SAM3 video track package exports."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .assets import Sam3AssetStore
from .metadata_scrub import scrub_sam3_metadata
from .video_track_fake_assets import (
    Sam3VideoTrackFakeAssetMaterializerError,
    materialize_fake_video_track_assets,
)
from .video_track_fake_runner import (
    Sam3VideoTrackFakeRunnerError,
    run_fake_video_track_request,
)
from .video_track_request import Sam3VideoTrackRequest
from .video_track_run_exporter import (
    Sam3VideoTrackRunPackageExporterError,
    export_video_track_run_package,
)

SAM3_VIDEO_TRACK_FAKE_PACKAGE_SMOKE_SCHEMA_VERSION = "sam3.video_track_fake_package_smoke.v1"


class Sam3VideoTrackFakePackageSmokeError(ValueError):
    """Raised when a fake video track package smoke run fails."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def run_fake_video_track_package_smoke(
    request: Sam3VideoTrackRequest,
    asset_store: Sam3AssetStore,
    output_dir: str | Path,
    *,
    allowed_roots: Sequence[str | Path],
    track_id: str | None = None,
    max_frames: int | None = None,
    package_id: str | None = None,
    metadata_path: str = "video_track_run_package.json",
    include_previews: bool = True,
) -> dict[str, object]:
    """Run fake video tracking, materialize fake assets, and export a package."""

    try:
        run_result = run_fake_video_track_request(
            request,
            track_id=track_id,
            max_frames=max_frames,
            include_previews=include_previews,
        )
    except Sam3VideoTrackFakeRunnerError as exc:
        raise Sam3VideoTrackFakePackageSmokeError(exc.code, str(exc)) from exc

    try:
        materialization = materialize_fake_video_track_assets(
            run_result,
            asset_store,
            include_previews=include_previews,
        )
    except Sam3VideoTrackFakeAssetMaterializerError as exc:
        raise Sam3VideoTrackFakePackageSmokeError(exc.code, str(exc)) from exc

    try:
        export = export_video_track_run_package(
            run_result,
            asset_store,
            output_dir,
            allowed_roots=allowed_roots,
            package_id=package_id,
            metadata_path=metadata_path,
            include_previews=include_previews,
        )
    except Sam3VideoTrackRunPackageExporterError as exc:
        raise Sam3VideoTrackFakePackageSmokeError(exc.code, str(exc)) from exc

    track_result = run_result.track_result
    result: dict[str, object] = {
        "schemaVersion": SAM3_VIDEO_TRACK_FAKE_PACKAGE_SMOKE_SCHEMA_VERSION,
        "status": "exported",
        "requestId": request.request_id,
        "trackId": track_result.track_id if track_result is not None else None,
        "runStatus": run_result.status,
        "completed": run_result.completed,
        "incomplete": not run_result.completed,
        "partial": run_result.partial,
        "materialization": dict(scrub_sam3_metadata(materialization)),
        "export": dict(scrub_sam3_metadata(export)),
        "blockedReasonCodes": _string_list(export.get("blockedReasonCodes")),
        "incompleteReasonCodes": _string_list(export.get("incompleteReasonCodes")),
    }
    return dict(scrub_sam3_metadata(result))


def _string_list(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [str(item) for item in value if str(item).strip()]


__all__ = [
    "SAM3_VIDEO_TRACK_FAKE_PACKAGE_SMOKE_SCHEMA_VERSION",
    "Sam3VideoTrackFakePackageSmokeError",
    "run_fake_video_track_package_smoke",
]
