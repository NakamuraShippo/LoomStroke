"""Export payload helpers for SAM3 comic mask packages."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .data_model import AssetRef
from .manga_mask_package import Sam3ComicMaskPackage
from .metadata_scrub import scrub_sam3_metadata

SAM3_COMIC_MASK_EXPORT_PAYLOAD_SCHEMA_VERSION = "sam3.comic_mask_export_payload.v1"


class Sam3ComicMaskExportError(ValueError):
    """Raised when a comic mask package export payload cannot be built."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def build_comic_mask_package_export_payload(
    package: Sam3ComicMaskPackage,
) -> dict[str, object]:
    """Build a file-write-free export payload for a comic mask package."""

    if not isinstance(package, Sam3ComicMaskPackage):
        raise Sam3ComicMaskExportError(
            "comic_mask_export_package_invalid",
            "SAM3 comic mask export requires a comic mask package.",
        )

    metadata_path = _safe_relative_path(
        package.metadata_path,
        code="comic_mask_export_package_invalid",
        message="SAM3 comic mask export requires a safe metadata path.",
    )
    manifest = dict(scrub_sam3_metadata(package.to_manifest_dict()))

    files: list[dict[str, object]] = [
        {
            "path": metadata_path,
            "role": "manifest",
            "kind": "json",
        }
    ]
    seen_paths = {metadata_path}
    for asset in package.export_assets:
        asset_path = _safe_relative_path(
            asset.uri,
            code="comic_mask_export_asset_path_unsafe",
            message="SAM3 comic mask export asset path must be package-relative.",
        )
        if asset_path in seen_paths:
            raise Sam3ComicMaskExportError(
                "comic_mask_export_duplicate_path",
                "SAM3 comic mask export paths must be unique.",
            )
        seen_paths.add(asset_path)
        files.append(_asset_file_entry(asset, asset_path))

    payload: dict[str, object] = {
        "schemaVersion": SAM3_COMIC_MASK_EXPORT_PAYLOAD_SCHEMA_VERSION,
        "packageId": package.package_id,
        "metadataPath": metadata_path,
        "manifest": manifest,
        "files": files,
        "blockedReasonCodes": [],
    }
    return dict(scrub_sam3_metadata(payload))


def _asset_file_entry(asset: AssetRef, path: str) -> dict[str, object]:
    return {
        "path": path,
        "role": "export_asset",
        "assetId": str(asset.asset_id).strip(),
        "kind": str(asset.kind).strip(),
        "width": int(asset.width),
        "height": int(asset.height),
        "contentHash": str(asset.content_hash).strip(),
        "bbox": list(_int_tuple4(asset.bbox)),
    }


def _safe_relative_path(value: object, *, code: str, message: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    lowered = text.lower()
    if not text:
        raise Sam3ComicMaskExportError(code, message)
    if "://" in text or lowered.startswith(("http:", "https:", "file:")):
        raise Sam3ComicMaskExportError(code, message)
    if text.startswith("/") or text.startswith("//") or ":" in text:
        raise Sam3ComicMaskExportError(code, message)
    parts = text.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise Sam3ComicMaskExportError(code, message)
    return "/".join(parts)


def _int_tuple4(value: object) -> tuple[int, int, int, int]:
    items = _sequence_items(value)
    if len(items) != 4:
        raise Sam3ComicMaskExportError(
            "comic_mask_export_package_invalid",
            "SAM3 comic mask export asset bbox must contain four values.",
        )
    try:
        return (int(items[0]), int(items[1]), int(items[2]), int(items[3]))
    except (TypeError, ValueError) as exc:
        raise Sam3ComicMaskExportError(
            "comic_mask_export_package_invalid",
            "SAM3 comic mask export asset bbox must contain integer values.",
        ) from exc


def _sequence_items(value: object) -> tuple[Any, ...]:
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(value)
    return ()


__all__ = [
    "SAM3_COMIC_MASK_EXPORT_PAYLOAD_SCHEMA_VERSION",
    "Sam3ComicMaskExportError",
    "build_comic_mask_package_export_payload",
]
