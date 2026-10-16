"""Asset payload resolution for SAM3 video track run package exports."""

from __future__ import annotations

from pathlib import Path

from .assets import Sam3AssetStore
from .video_track_run import Sam3VideoTrackRunResult


class Sam3VideoTrackRunAssetPayloadResolverError(ValueError):
    """Raised when video track run package asset payloads cannot be resolved."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def resolve_video_track_run_package_asset_payloads(
    run_result: Sam3VideoTrackRunResult,
    asset_store: Sam3AssetStore,
    *,
    include_previews: bool = True,
) -> dict[str, bytes]:
    """Resolve video track run package assets to writer-ready bytes by asset id."""

    if not isinstance(run_result, Sam3VideoTrackRunResult):
        raise Sam3VideoTrackRunAssetPayloadResolverError(
            "video_track_run_asset_payload_result_invalid",
            "SAM3 video track run asset payload resolver requires a video track run result.",
        )
    if not isinstance(asset_store, Sam3AssetStore):
        raise Sam3VideoTrackRunAssetPayloadResolverError(
            "video_track_run_asset_payload_store_invalid",
            "SAM3 video track run asset payload resolver requires a SAM3 asset store.",
        )

    track_result = run_result.track_result
    if track_result is None:
        return {}

    payloads: dict[str, bytes] = {}
    for asset_id in _mask_asset_ids(run_result):
        if asset_id in payloads:
            continue
        payload = _read_mask_payload(asset_store, asset_id)
        if not payload:
            raise Sam3VideoTrackRunAssetPayloadResolverError(
                "video_track_run_asset_payload_empty",
                "SAM3 video track run asset payload must not be empty.",
            )
        payloads[asset_id] = payload

    if include_previews:
        for asset_id in _preview_asset_ids(run_result):
            if asset_id in payloads:
                continue
            payload = _read_preview_payload(asset_store, asset_id)
            if not payload:
                raise Sam3VideoTrackRunAssetPayloadResolverError(
                    "video_track_run_asset_payload_empty",
                    "SAM3 video track run asset payload must not be empty.",
                )
            payloads[asset_id] = payload

    return payloads


def _mask_asset_ids(run_result: Sam3VideoTrackRunResult) -> tuple[str, ...]:
    track_result = run_result.track_result
    if track_result is None:
        return ()
    asset_ids: list[str] = []
    for frame in track_result.frames:
        for asset_id in frame.object_masks.values():
            asset_ids.append(_asset_id(asset_id))
    return tuple(asset_ids)


def _preview_asset_ids(run_result: Sam3VideoTrackRunResult) -> tuple[str, ...]:
    track_result = run_result.track_result
    if track_result is None:
        return ()
    asset_ids: list[str] = []
    for frame in track_result.frames:
        if frame.preview_asset_id is not None:
            asset_ids.append(_asset_id(frame.preview_asset_id))
    return tuple(asset_ids)


def _read_mask_payload(asset_store: Sam3AssetStore, asset_id: str) -> bytes:
    try:
        asset = asset_store.load_mask_asset(asset_id)
        return _read_bytes(asset_store.mask_asset_path(asset))
    except Sam3VideoTrackRunAssetPayloadResolverError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise Sam3VideoTrackRunAssetPayloadResolverError(
            "video_track_run_asset_payload_read_failed",
            "SAM3 video track run mask asset payload could not be read.",
        ) from exc


def _read_preview_payload(asset_store: Sam3AssetStore, asset_id: str) -> bytes:
    try:
        asset = asset_store.load_preview_asset(asset_id)
        return _read_bytes(asset_store.preview_asset_path(asset))
    except Sam3VideoTrackRunAssetPayloadResolverError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise Sam3VideoTrackRunAssetPayloadResolverError(
            "video_track_run_asset_payload_read_failed",
            "SAM3 video track run preview asset payload could not be read.",
        ) from exc


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise Sam3VideoTrackRunAssetPayloadResolverError(
            "video_track_run_asset_payload_read_failed",
            "SAM3 video track run asset payload could not be read.",
        ) from exc


def _asset_id(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        raise Sam3VideoTrackRunAssetPayloadResolverError(
            "video_track_run_asset_payload_asset_id_missing",
            "SAM3 video track run asset payload requires an asset id.",
        )
    return text


__all__ = [
    "Sam3VideoTrackRunAssetPayloadResolverError",
    "resolve_video_track_run_package_asset_payloads",
]
