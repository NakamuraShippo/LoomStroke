"""Fake asset materialization helpers for SAM3 video track smoke flows."""

from __future__ import annotations

from .assets import Sam3AssetStore
from .metadata_scrub import scrub_sam3_metadata
from .video_track_run import Sam3VideoTrackRunResult
from .video_tracker_model import Sam3VideoMaskFrame, Sam3VideoTrackResult

SAM3_VIDEO_TRACK_FAKE_ASSET_MATERIALIZATION_SCHEMA_VERSION = (
    "sam3.video_track_fake_asset_materialization.v1"
)

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class Sam3VideoTrackFakeAssetMaterializerError(ValueError):
    """Raised when fake video track assets cannot be materialized."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def materialize_fake_video_track_assets(
    run_result: Sam3VideoTrackRunResult,
    asset_store: Sam3AssetStore,
    *,
    include_previews: bool = True,
) -> dict[str, object]:
    """Persist deterministic fake video mask and preview assets for smoke flows."""

    if not isinstance(run_result, Sam3VideoTrackRunResult):
        raise Sam3VideoTrackFakeAssetMaterializerError(
            "video_track_fake_asset_result_invalid",
            "SAM3 fake video track asset materializer requires a video track run result.",
        )
    if not isinstance(asset_store, Sam3AssetStore):
        raise Sam3VideoTrackFakeAssetMaterializerError(
            "video_track_fake_asset_store_invalid",
            "SAM3 fake video track asset materializer requires a SAM3 asset store.",
        )

    track_result = run_result.track_result
    mask_asset_ids: list[str] = []
    preview_asset_ids: list[str] = []
    if track_result is not None:
        if not _is_fake_run_result(run_result, track_result):
            raise Sam3VideoTrackFakeAssetMaterializerError(
                "video_track_fake_asset_result_not_fake",
                "SAM3 fake video track asset materializer only accepts fake run results.",
            )
        mask_asset_ids = _materialize_mask_assets(asset_store, track_result)
        if include_previews:
            preview_asset_ids = _materialize_preview_assets(asset_store, track_result)

    result: dict[str, object] = {
        "schemaVersion": SAM3_VIDEO_TRACK_FAKE_ASSET_MATERIALIZATION_SCHEMA_VERSION,
        "status": "materialized",
        "requestId": run_result.request.request_id,
        "trackId": track_result.track_id if track_result is not None else None,
        "maskAssetCount": len(mask_asset_ids),
        "previewAssetCount": len(preview_asset_ids),
        "assetIds": [*mask_asset_ids, *preview_asset_ids],
    }
    return dict(scrub_sam3_metadata(result))


def _materialize_mask_assets(
    asset_store: Sam3AssetStore,
    track_result: Sam3VideoTrackResult,
) -> list[str]:
    asset_ids: list[str] = []
    seen: set[str] = set()
    for frame in track_result.frames:
        for object_id, asset_id in frame.object_masks.items():
            if asset_id in seen:
                continue
            seen.add(asset_id)
            _save_mask_asset(asset_store, track_result, frame, object_id, asset_id)
            asset_ids.append(asset_id)
    return asset_ids


def _materialize_preview_assets(
    asset_store: Sam3AssetStore,
    track_result: Sam3VideoTrackResult,
) -> list[str]:
    asset_ids: list[str] = []
    seen: set[str] = set()
    for frame in track_result.frames:
        asset_id = frame.preview_asset_id
        if asset_id is None or asset_id in seen:
            continue
        seen.add(asset_id)
        _save_preview_asset(asset_store, track_result, frame, asset_id)
        asset_ids.append(asset_id)
    return asset_ids


def _save_mask_asset(
    asset_store: Sam3AssetStore,
    track_result: Sam3VideoTrackResult,
    frame: Sam3VideoMaskFrame,
    object_id: str,
    asset_id: str,
) -> None:
    try:
        asset_store.save_mask_asset(
            _fake_mask_payload(asset_id, frame.frame_index, object_id),
            asset_id=asset_id,
            kind="alpha_mask",
            width=_width(track_result),
            height=_height(track_result),
            bbox=_bbox(track_result),
            metadata={
                "provider": "fake",
                "source": "sam3_fake_video_track_asset_materializer",
                "frameIndex": frame.frame_index,
                "objectId": object_id,
            },
        )
    except (OSError, TypeError, ValueError) as exc:
        raise Sam3VideoTrackFakeAssetMaterializerError(
            "video_track_fake_asset_write_failed",
            "SAM3 fake video track mask asset could not be materialized.",
        ) from exc


def _save_preview_asset(
    asset_store: Sam3AssetStore,
    track_result: Sam3VideoTrackResult,
    frame: Sam3VideoMaskFrame,
    asset_id: str,
) -> None:
    try:
        asset_store.save_preview_asset(
            _fake_preview_payload(asset_id, frame.frame_index),
            asset_id=asset_id,
            width=_width(track_result),
            height=_height(track_result),
            bbox=_bbox(track_result),
            metadata={
                "provider": "fake",
                "source": "sam3_fake_video_track_asset_materializer",
                "frameIndex": frame.frame_index,
            },
        )
    except (OSError, TypeError, ValueError) as exc:
        raise Sam3VideoTrackFakeAssetMaterializerError(
            "video_track_fake_asset_write_failed",
            "SAM3 fake video track preview asset could not be materialized.",
        ) from exc


def _fake_mask_payload(asset_id: str, frame_index: int, object_id: str) -> bytes:
    metadata = (
        "SAM3_FAKE_VIDEO_MASK\n"
        f"asset={asset_id}\n"
        f"frame={frame_index}\n"
        f"object={object_id}\n"
    )
    return _PNG_SIGNATURE + metadata.encode()


def _fake_preview_payload(asset_id: str, frame_index: int) -> bytes:
    return f"SAM3_FAKE_VIDEO_PREVIEW\nasset={asset_id}\nframe={frame_index}\n".encode()


def _is_fake_run_result(
    run_result: Sam3VideoTrackRunResult,
    track_result: Sam3VideoTrackResult,
) -> bool:
    return (
        str(run_result.diagnostics.get("provider") or "").strip() == "fake"
        or str(track_result.diagnostics.get("provider") or "").strip() == "fake"
    )


def _width(track_result: Sam3VideoTrackResult) -> int:
    return max(1, int(track_result.width or 1))


def _height(track_result: Sam3VideoTrackResult) -> int:
    return max(1, int(track_result.height or 1))


def _bbox(track_result: Sam3VideoTrackResult) -> tuple[int, int, int, int]:
    return (0, 0, _width(track_result), _height(track_result))


__all__ = [
    "SAM3_VIDEO_TRACK_FAKE_ASSET_MATERIALIZATION_SCHEMA_VERSION",
    "Sam3VideoTrackFakeAssetMaterializerError",
    "materialize_fake_video_track_assets",
]
