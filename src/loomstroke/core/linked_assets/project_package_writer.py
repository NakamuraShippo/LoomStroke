"""Writes a directory package through a staging directory (CT-18 §5.3 / §13 / §14).

Never overwrites an existing destination, never touches the source document
or assets, and leaves no manifest behind when any copy fails or is cancelled.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from .package_asset_entry import PackageAssetEntry
from .package_manifest import PackageManifest
from .package_plan import PackagePlan
from .package_result import PackageResult
from .resource_content_hasher import ResourceContentHasher
from .resource_locator_resolver import ResourceLocatorResolver

__all__ = ["ProjectPackageWriter"]

ProgressCallback = Callable[[int, int], None]
CancelCheck = Callable[[], bool]


class _PackageError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class ProjectPackageWriter:
    def __init__(
        self, hasher: ResourceContentHasher | None = None, *, app_version: str = ""
    ) -> None:
        self._hasher = hasher or ResourceContentHasher()
        self._app_version = app_version

    def write(
        self,
        plan: PackagePlan,
        *,
        lineage_id: str,
        progress: ProgressCallback | None = None,
        cancel: CancelCheck | None = None,
    ) -> PackageResult:
        destination = Path(plan.destination)
        if plan.document_source is None or not Path(plan.document_source).is_file():
            return PackageResult.failure(destination, "document_not_saved")
        if destination.exists():
            return PackageResult.failure(destination, "destination_exists")
        staging = destination.parent / f".{destination.name}.staging-{os.getpid()}"
        if staging.exists():
            return PackageResult.failure(destination, "staging_exists")
        try:
            result = self._write_staging(plan, staging, destination, lineage_id, progress, cancel)
        except _PackageError as error:
            self._cleanup(staging)
            return PackageResult.failure(destination, error.code, error.detail)
        except OSError as error:
            self._cleanup(staging)
            return PackageResult.failure(destination, "io_error", type(error).__name__)
        return result

    # -- internals ---------------------------------------------------------------------

    def _write_staging(
        self,
        plan: PackagePlan,
        staging: Path,
        destination: Path,
        lineage_id: str,
        progress: ProgressCallback | None,
        cancel: CancelCheck | None,
    ) -> PackageResult:
        staging.mkdir(parents=True)
        copies = plan.copies()
        total = len(copies) + 1
        document_target = self._target_for(staging, plan.document_package_name)
        shutil.copyfile(plan.document_source, document_target)
        document_hash = self._hasher.hash_file(document_target)
        document_size = document_target.stat().st_size
        if progress is not None:
            progress(1, total)
        copied: list[PackageAssetEntry] = []
        for index, entry in enumerate(copies, start=2):
            if cancel is not None and cancel():
                raise _PackageError("cancelled")
            copied.append(self._copy_entry(staging, entry))
            if progress is not None:
                progress(index, total)
        manifest = PackageManifest(
            app_version=self._app_version,
            document_name=plan.document_package_name,
            document_hash=document_hash,
            document_size=document_size,
            lineage_id=lineage_id,
            assets=copied,
            omitted=plan.omitted(),
        )
        (staging / PackageManifest.FILE_NAME).write_text(manifest.to_json(), encoding="utf-8")
        if plan.font_families is not None:
            (staging / "font_requirements.json").write_text(
                json.dumps(
                    {"font_files_included": False, "families": sorted(set(plan.font_families))},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        os.replace(staging, destination)
        return PackageResult(
            True,
            destination,
            manifest_path=destination / PackageManifest.FILE_NAME,
            copied_count=len(copied),
            omitted_count=len(plan.omitted()),
            document_hash=document_hash,
        )

    def _copy_entry(self, staging: Path, entry: PackageAssetEntry) -> PackageAssetEntry:
        assert entry.package_path is not None and entry.source_path is not None
        target = self._target_for(staging, entry.package_path)
        if ResourceLocatorResolver.is_symlink(entry.source_path):
            raise _PackageError("asset_symlink", entry.display_name)
        shutil.copyfile(entry.source_path, target)
        copied_hash = self._hasher.hash_file(target)
        if entry.content_hash and copied_hash != entry.content_hash:
            raise _PackageError("asset_changed_during_copy", entry.display_name)
        return entry.with_copied(copied_hash, target.stat().st_size)

    @staticmethod
    def _target_for(staging: Path, package_path: str) -> Path:
        relative = ResourceLocatorResolver.validate_relative(package_path)
        if relative is None:
            raise _PackageError("package_path_invalid", package_path)
        target = staging.joinpath(*PurePosixPath(relative).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise _PackageError("package_path_duplicate", package_path)
        return target

    @staticmethod
    def _cleanup(staging: Path) -> None:
        shutil.rmtree(staging, ignore_errors=True)
