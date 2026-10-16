"""Turns resource records + user choices into a deterministic package plan."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath
from typing import ClassVar

from .package_asset_entry import PackageAction, PackageAssetEntry
from .package_plan import PackagePlan
from .resource_place_planner import ResourcePlacePlanner
from .resource_record import ResourceRecord
from .resource_status_service import ResourceStatusService

__all__ = ["ProjectPackagePlanner"]


class ProjectPackagePlanner:
    ASSETS_DIR: ClassVar[str] = "assets"

    def __init__(self, status_service: ResourceStatusService) -> None:
        self._status = status_service

    def plan(
        self,
        records: Iterable[ResourceRecord],
        *,
        destination: Path,
        document_source: Path | None,
        choices: Mapping[str, PackageAction] | None = None,
        default_action: PackageAction = "copy",
    ) -> PackagePlan:
        choices = dict(choices or {})
        issues: list[str] = []
        if document_source is None:
            issues.append("document_not_saved")
        entries: list[PackageAssetEntry] = []
        used: set[str] = set()
        ordered = sorted(records, key=lambda r: (r.display_name.lower(), r.resource_id))
        for record in ordered:
            entries.append(
                self._entry(record, choices.get(record.resource_id, default_action), used, issues)
            )
        document_name = document_source.name if document_source is not None else "document.lstroke"
        return PackagePlan(
            destination=Path(destination),
            document_source=document_source,
            document_package_name=document_name,
            entries=tuple(entries),
            issues=tuple(issues),
        )

    def _entry(
        self,
        record: ResourceRecord,
        action: PackageAction,
        used: set[str],
        issues: list[str],
    ) -> PackageAssetEntry:
        base = PackageAssetEntry(
            resource_id=record.resource_id,
            display_name=record.display_name,
            action="embed",
            state="ok",
            content_hash=record.content_hash,
            byte_size=record.byte_size,
            media_type=record.media_type,
            width=record.width,
            height=record.height,
        )
        if record.mode == "embedded":
            return base.with_action("embed", None)
        status = self._status.inspect(record, refresh_hash=True)
        if not status.is_available:
            issues.append(f"{status.state}:{record.display_name}")
            return PackageAssetEntry(
                resource_id=record.resource_id,
                display_name=record.display_name,
                action="missing",
                state=status.state,
                media_type=record.media_type,
                reason_code=status.reason_code or status.state,
            )
        if status.state == "modified":
            issues.append(f"modified:{record.display_name}")
        package_path = None
        if action == "copy":
            package_path = self._unique_package_path(record.display_name, used)
        elif action != "external":
            action = "external"
        return PackageAssetEntry(
            resource_id=record.resource_id,
            display_name=record.display_name,
            action=action,
            state=status.state,
            package_path=package_path,
            source_path=status.resolved_path,
            content_hash=status.current_hash or record.content_hash,
            byte_size=status.byte_size or record.byte_size,
            media_type=record.media_type,
            width=record.width,
            height=record.height,
            reason_code=status.reason_code,
        )

    def _unique_package_path(self, display_name: str, used: set[str]) -> str:
        name = ResourcePlacePlanner.sanitize_name(display_name)
        stem, suffix = PurePosixPath(name).stem, PurePosixPath(name).suffix
        candidate = name
        counter = 2
        while candidate.lower() in used:
            candidate = f"{stem}_{counter}{suffix}"
            counter += 1
        used.add(candidate.lower())
        return f"{self.ASSETS_DIR}/{candidate}"
