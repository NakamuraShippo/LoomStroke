"""``File > Package Project…`` (CT-18 §5.3): plan, preflight dialog, directory package write."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QMessageBox

from loomstroke.core.linked_assets import (
    PackageAction,
    PackagePlan,
    PackageResult,
    ProjectPackagePlanner,
    ProjectPackageWriter,
)

from .dialogs.package_project_dialog import PackageProjectDialog
from .document_path_locator import DocumentPathLocator
from .package_font_inventory import package_font_families
from .resource_link_coordinator import ResourceLinkCoordinator
from .translations import translated_literal_domain

__all__ = ["MainWindowPackageAction"]


def _ts(text: str) -> str:
    return translated_literal_domain("PackageProjectDialog", text, use_fallback=True)


def _app_version() -> str:
    try:
        from importlib.metadata import version  # noqa: PLC0415

        return version("loomstroke")
    except Exception:
        return ""


class MainWindowPackageAction:
    def __init__(
        self,
        window: Any,
        *,
        dialog_factory: Callable[..., Any] = PackageProjectDialog,
        message_box: Any = QMessageBox,
    ) -> None:
        self._window = window
        self._dialog_factory = dialog_factory
        self._message_box = message_box

    def coordinator(self) -> ResourceLinkCoordinator:
        host = getattr(self._window, "_host_api", None) or getattr(self._window, "host", None)
        return ResourceLinkCoordinator(
            host, document_dir_provider=DocumentPathLocator(self._window).document_dir
        )

    def document_path(self) -> Path | None:
        return DocumentPathLocator(self._window).document_path()

    def run(self) -> bool:
        document = self.document_path()
        if document is None or not document.is_file():
            self._message_box.information(
                self._window,
                _ts("Package Project"),
                _ts("Save the document before packaging."),
            )
            return False
        dialog = self._dialog_factory(
            plan_factory=self.plan,
            writer=self.write,
            default_destination=document.parent / f"{document.stem}_package",
            parent=self._window,
        )
        dialog.exec()
        return True

    def plan(self, destination: Path, choices: dict[str, PackageAction]) -> PackagePlan:
        coordinator = self.coordinator()
        planner = ProjectPackagePlanner(coordinator.status_service())
        plan = planner.plan(
            coordinator.index().records(),
            destination=destination,
            document_source=self.document_path(),
            choices=choices,
        )
        return replace(plan, font_families=package_font_families(self._window))

    def write(self, plan: PackagePlan) -> PackageResult:
        writer = ProjectPackageWriter(app_version=_app_version())
        return writer.write(plan, lineage_id=self._lineage_id())

    def _lineage_id(self) -> str:
        host = getattr(self._window, "_host_api", None) or getattr(self._window, "host", None)
        document = getattr(host, "document", None)
        for name in ("current_document_id", "document_id"):
            value = getattr(document, name, None)
            value = value() if callable(value) else value
            if isinstance(value, str) and value:
                return value
        return "doc_" + uuid.uuid4().hex[:12]
