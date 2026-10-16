"""Orchestration helpers for SAM3 video track run package exports."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .assets import Sam3AssetStore
from .metadata_scrub import scrub_sam3_metadata
from .video_track_run import Sam3VideoTrackRunResult
from .video_track_run_package_writer import (
    Sam3VideoTrackRunPackageWriterError,
    write_video_track_run_package_export,
)
from .video_track_run_payload_resolver import (
    Sam3VideoTrackRunAssetPayloadResolverError,
    resolve_video_track_run_package_asset_payloads,
)

SAM3_VIDEO_TRACK_RUN_PACKAGE_EXPORTER_RESULT_SCHEMA_VERSION = (
    "sam3.video_track_run_package_exporter_result.v1"
)


class Sam3VideoTrackRunPackageExporterError(ValueError):
    """Raised when a video track run package export orchestration fails."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def export_video_track_run_package(
    run_result: Sam3VideoTrackRunResult,
    asset_store: Sam3AssetStore,
    output_dir: str | Path,
    *,
    allowed_roots: Sequence[str | Path],
    package_id: str | None = None,
    metadata_path: str = "video_track_run_package.json",
    include_previews: bool = True,
) -> dict[str, object]:
    """Resolve assets and write a SAM3 video track run package under allowed roots."""

    try:
        asset_payloads = resolve_video_track_run_package_asset_payloads(
            run_result,
            asset_store,
            include_previews=include_previews,
        )
    except Sam3VideoTrackRunAssetPayloadResolverError as exc:
        raise Sam3VideoTrackRunPackageExporterError(exc.code, str(exc)) from exc

    try:
        write_result = write_video_track_run_package_export(
            run_result,
            output_dir,
            asset_payloads,
            allowed_roots=allowed_roots,
            package_id=package_id,
            metadata_path=metadata_path,
            include_previews=include_previews,
        )
    except Sam3VideoTrackRunPackageWriterError as exc:
        raise Sam3VideoTrackRunPackageExporterError(exc.code, str(exc)) from exc

    track_result = run_result.track_result
    result: dict[str, object] = {
        "schemaVersion": SAM3_VIDEO_TRACK_RUN_PACKAGE_EXPORTER_RESULT_SCHEMA_VERSION,
        "status": "exported",
        "requestId": run_result.request.request_id,
        "trackId": track_result.track_id if track_result is not None else None,
        "runStatus": run_result.status,
        "completed": run_result.completed,
        "incomplete": not run_result.completed,
        "partial": run_result.partial,
        "assetPayloadCount": len(asset_payloads),
        "export": dict(scrub_sam3_metadata(write_result)),
        "blockedReasonCodes": _string_list(write_result.get("blockedReasonCodes")),
        "incompleteReasonCodes": _string_list(write_result.get("incompleteReasonCodes")),
    }
    return dict(scrub_sam3_metadata(result))


def _string_list(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [str(item) for item in value if str(item).strip()]


__all__ = [
    "SAM3_VIDEO_TRACK_RUN_PACKAGE_EXPORTER_RESULT_SCHEMA_VERSION",
    "Sam3VideoTrackRunPackageExporterError",
    "export_video_track_run_package",
]
