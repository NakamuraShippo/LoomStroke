"""``package_manifest.json`` (CT-18 §10): relative paths + hashes only, deterministic order."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, ClassVar

from .package_asset_entry import PackageAssetEntry
from .resource_locator_resolver import ResourceLocatorResolver

__all__ = ["PackageManifest"]


class PackageManifest:
    SCHEMA: ClassVar[str] = "loomstroke.package_manifest"
    SCHEMA_VERSION: ClassVar[int] = 1
    FILE_NAME: ClassVar[str] = "package_manifest.json"

    def __init__(
        self,
        *,
        app_version: str,
        document_name: str,
        document_hash: str,
        document_size: int,
        lineage_id: str,
        assets: Sequence[PackageAssetEntry],
        omitted: Sequence[PackageAssetEntry] = (),
    ) -> None:
        self._app_version = app_version
        self._document_name = document_name
        self._document_hash = document_hash
        self._document_size = document_size
        self._lineage_id = lineage_id
        self._assets = tuple(assets)
        self._omitted = tuple(omitted)

    def to_dict(self) -> dict[str, Any]:
        assets = sorted(
            (entry for entry in self._assets if entry.package_path), key=lambda e: e.package_path
        )
        return {
            "schema": self.SCHEMA,
            "schema_version": self.SCHEMA_VERSION,
            "app_version": self._app_version,
            "lineage_id": self._lineage_id,
            "document": {
                "path": self._document_name,
                "hash": self._document_hash,
                "byte_size": int(self._document_size),
            },
            "assets": [
                {
                    "resource_id": entry.resource_id,
                    "path": entry.package_path,
                    "hash": entry.content_hash,
                    "byte_size": int(entry.byte_size),
                    "media_type": entry.media_type,
                    "width": int(entry.width),
                    "height": int(entry.height),
                }
                for entry in assets
            ],
            "omitted": [
                {
                    "resource_id": entry.resource_id,
                    "display_name": entry.display_name,
                    "action": entry.action,
                    "reason": entry.reason_code or entry.state,
                }
                for entry in sorted(self._omitted, key=lambda e: (e.display_name, e.resource_id))
            ],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    @staticmethod
    def validate_dict(data: Any) -> tuple[str, ...]:
        """Issue codes for a manifest payload; empty means acceptable."""
        issues: list[str] = []
        if not isinstance(data, dict):
            return ("manifest_not_object",)
        if data.get("schema") != PackageManifest.SCHEMA:
            issues.append("manifest_schema_unknown")
        document = data.get("document")
        if not isinstance(document, dict):
            issues.append("manifest_document_missing")
        else:
            if ResourceLocatorResolver.validate_relative(document.get("path")) is None:
                issues.append("manifest_document_path_invalid")
            if not str(document.get("hash") or "").startswith("sha256:"):
                issues.append("manifest_document_hash_missing")
        assets = data.get("assets")
        if not isinstance(assets, list):
            issues.append("manifest_assets_missing")
            return tuple(issues)
        seen: set[str] = set()
        for asset in assets:
            path = asset.get("path") if isinstance(asset, dict) else None
            normalised = ResourceLocatorResolver.validate_relative(path)
            if normalised is None or normalised != path:
                issues.append("manifest_asset_path_invalid")
                continue
            if path in seen:
                issues.append("manifest_asset_path_duplicate")
            seen.add(path)
            if not str(asset.get("hash") or "").startswith("sha256:"):
                issues.append("manifest_asset_hash_missing")
        return tuple(issues)
