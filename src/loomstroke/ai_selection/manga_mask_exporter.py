"""Orchestration helpers for SAM3 comic mask package exports."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .assets import Sam3AssetStore
from .manga_mask_package import Sam3ComicMaskPackage
from .manga_mask_payload_resolver import (
    Sam3ComicMaskAssetPayloadResolverError,
    resolve_comic_mask_package_asset_payloads,
)
from .manga_mask_writer import (
    Sam3ComicMaskPackageWriterError,
    write_comic_mask_package_export,
)
from .metadata_scrub import scrub_sam3_metadata

SAM3_COMIC_MASK_PACKAGE_EXPORT_RESULT_SCHEMA_VERSION = "sam3.comic_mask_package_export_result.v1"


class Sam3ComicMaskPackageExportError(ValueError):
    """Raised when a comic mask package export orchestration fails."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def export_comic_mask_package(
    package: Sam3ComicMaskPackage,
    asset_store: Sam3AssetStore,
    output_dir: str | Path,
    *,
    allowed_roots: Sequence[str | Path],
) -> dict[str, object]:
    """Resolve assets and write a SAM3 comic mask package under allowed roots."""

    try:
        asset_payloads = resolve_comic_mask_package_asset_payloads(package, asset_store)
    except Sam3ComicMaskAssetPayloadResolverError as exc:
        raise Sam3ComicMaskPackageExportError(exc.code, str(exc)) from exc

    try:
        write_result = write_comic_mask_package_export(
            package,
            output_dir,
            asset_payloads,
            allowed_roots=allowed_roots,
        )
    except Sam3ComicMaskPackageWriterError as exc:
        raise Sam3ComicMaskPackageExportError(exc.code, str(exc)) from exc

    result: dict[str, object] = {
        "schemaVersion": SAM3_COMIC_MASK_PACKAGE_EXPORT_RESULT_SCHEMA_VERSION,
        "status": "exported",
        "packageId": package.package_id,
        "assetPayloadCount": len(asset_payloads),
        "export": dict(scrub_sam3_metadata(write_result)),
        "blockedReasonCodes": [],
    }
    return dict(scrub_sam3_metadata(result))


__all__ = [
    "SAM3_COMIC_MASK_PACKAGE_EXPORT_RESULT_SCHEMA_VERSION",
    "Sam3ComicMaskPackageExportError",
    "export_comic_mask_package",
]
