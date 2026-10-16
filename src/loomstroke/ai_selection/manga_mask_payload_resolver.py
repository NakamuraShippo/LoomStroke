"""Asset payload resolution for SAM3 comic mask package exports."""

from __future__ import annotations

from pathlib import Path

from .assets import Sam3AssetStore
from .data_model import AssetRef
from .manga_mask_package import Sam3ComicMaskPackage

_MASK_KINDS = frozenset({"mask", "alpha_mask", "binary_mask", "soft_mask", "instance_mask"})
_PREVIEW_KINDS = frozenset({"preview", "preview_png", "overlay_preview"})


class Sam3ComicMaskAssetPayloadResolverError(ValueError):
    """Raised when comic mask package asset payloads cannot be resolved."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def resolve_comic_mask_package_asset_payloads(
    package: Sam3ComicMaskPackage,
    asset_store: Sam3AssetStore,
) -> dict[str, bytes]:
    """Resolve package export assets to writer-ready bytes by asset id."""

    if not isinstance(package, Sam3ComicMaskPackage):
        raise Sam3ComicMaskAssetPayloadResolverError(
            "comic_mask_asset_payload_package_invalid",
            "SAM3 comic mask asset payload resolver requires a comic mask package.",
        )
    if not isinstance(asset_store, Sam3AssetStore):
        raise Sam3ComicMaskAssetPayloadResolverError(
            "comic_mask_asset_payload_package_invalid",
            "SAM3 comic mask asset payload resolver requires a SAM3 asset store.",
        )

    payloads: dict[str, bytes] = {}
    for asset in package.export_assets:
        asset_id = _asset_id(asset)
        kind = _asset_kind(asset)
        if asset_id in payloads:
            continue
        payload = _read_asset_payload(asset_store, asset_id, kind)
        if not payload:
            raise Sam3ComicMaskAssetPayloadResolverError(
                "comic_mask_asset_payload_empty",
                "SAM3 comic mask asset payload must not be empty.",
            )
        payloads[asset_id] = payload
    return payloads


def _read_asset_payload(asset_store: Sam3AssetStore, asset_id: str, kind: str) -> bytes:
    try:
        path = _asset_path(asset_store, asset_id, kind)
        return path.read_bytes()
    except Sam3ComicMaskAssetPayloadResolverError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise Sam3ComicMaskAssetPayloadResolverError(
            "comic_mask_asset_payload_read_failed",
            "SAM3 comic mask asset payload could not be read.",
        ) from exc


def _asset_path(asset_store: Sam3AssetStore, asset_id: str, kind: str) -> Path:
    if kind in _MASK_KINDS:
        asset = asset_store.load_mask_asset(asset_id)
        return asset_store.mask_asset_path(asset)
    if kind in _PREVIEW_KINDS:
        asset = asset_store.load_preview_asset(asset_id)
        return asset_store.preview_asset_path(asset)
    raise Sam3ComicMaskAssetPayloadResolverError(
        "comic_mask_asset_payload_kind_unsupported",
        "SAM3 comic mask asset payload kind is unsupported.",
    )


def _asset_id(asset: AssetRef) -> str:
    text = str(asset.asset_id or "").strip()
    if not text:
        raise Sam3ComicMaskAssetPayloadResolverError(
            "comic_mask_asset_payload_asset_id_missing",
            "SAM3 comic mask asset payload requires an asset id.",
        )
    return text


def _asset_kind(asset: AssetRef) -> str:
    kind = str(asset.kind or "").strip().lower().replace("-", "_")
    if not kind:
        raise Sam3ComicMaskAssetPayloadResolverError(
            "comic_mask_asset_payload_kind_unsupported",
            "SAM3 comic mask asset payload kind is unsupported.",
        )
    return kind


__all__ = [
    "Sam3ComicMaskAssetPayloadResolverError",
    "resolve_comic_mask_package_asset_payloads",
]
