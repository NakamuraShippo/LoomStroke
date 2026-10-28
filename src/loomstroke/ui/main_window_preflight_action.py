"""``File > Manga Preflight...`` action for MainWindow (CT-16 Phase B/C, spec 16 §6).

Builds the snapshot on the main thread (active page or all pages), runs
the pure service, shows the dialog, remembers the last report on the
window for the export gate and navigates to a page when an issue is
activated.  Scanning never touches the document, history or selection.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QMessageBox

from ..core.book_layout_service import BookLayoutService
from ..core.book_spread_layout import BookLayout
from ..core.preflight.manga_preflight_service import MangaPreflightService
from ..core.preflight.preflight_issue import PreflightIssue
from ..core.preflight.preflight_profile import MangaPreflightProfile
from ..core.preflight.preflight_report import PreflightReport
from ..io.layer_io import DocumentMetadata
from .dialogs.manga_preflight_dialog import MangaPreflightDialog
from .preflight_export_gate import PreflightExportGate
from .preflight_snapshot_builder import DocumentPreflightSnapshotBuilder
from .translations import translate_with_fallback

logger = logging.getLogger(__name__)

__all__ = ["MainWindowPreflightAction"]


def _ts(text: str) -> str:
    return translate_with_fallback("MangaPreflightDialog", text)


class MainWindowPreflightAction:
    """Open the Manga Preflight dialog."""

    def __init__(
        self,
        window: Any,
        *,
        document_metadata: Callable[[], DocumentMetadata] | None = None,
        service: MangaPreflightService | None = None,
        builder_factory: Callable[..., Any] | None = None,
        dialog_factory: Callable[..., Any] | None = None,
        message_box: Callable[[], Any] | None = None,
    ) -> None:
        self._window = window
        self._document_metadata = document_metadata or self._fallback_document_metadata
        self._service = service or MangaPreflightService()
        self._builder_factory = builder_factory or DocumentPreflightSnapshotBuilder
        self._dialog_factory = dialog_factory or MangaPreflightDialog
        self._message_box = message_box or (lambda: QMessageBox)
        self.last_report: PreflightReport | None = None

    def run(self) -> bool:
        pages = self._pages()
        list_pages = getattr(pages, "list_pages", None)
        summaries = list(list_pages() or []) if callable(list_pages) else []
        if not summaries:
            self._message_box().warning(
                self._window, _ts("Manga Preflight"), _ts("No pages are available to check.")
            )
            return False
        dialog = self._dialog_factory(
            profiles=MangaPreflightProfile.builtins(),
            scan=self.scan,
            on_navigate=self.navigate,
            page_names={str(s.id): str(getattr(s, "name", "") or s.id) for s in summaries},
            parent=self._window,
        )
        dialog.exec()
        return True

    def scan(self, profile: MangaPreflightProfile, scope: str) -> PreflightReport:
        """Build the snapshot for ``scope`` (``current`` / ``all``) and run the rules."""
        pages = self._pages()
        page_ids: list[str] | None = None
        if scope == "current":
            active = getattr(pages, "get_active_page_id", lambda: None)()
            page_ids = [str(active)] if active else []
        builder = self._builder_factory(
            pages,
            self._document_metadata(),
            document_id=self._document_name(),
            document_name=self._document_name(),
            has_color_profile=self._has_color_profile(),
            roles_by_id=self._roles_by_id(pages),
            book_layout=self._book_layout(),
            live_canvas=getattr(getattr(self._window, "_host_api", None), "canvas", None),
        )
        snapshot = builder.build(page_ids)
        report = self._service.run(snapshot, profile)
        self.last_report = report
        PreflightExportGate.remember(self._window, report)
        return report

    def navigate(self, issue: PreflightIssue) -> None:
        if not issue.page_id:
            return
        pages = self._pages()
        set_active = getattr(pages, "set_active_page", None)
        if callable(set_active):
            try:
                set_active(issue.page_id)
            except Exception:
                logger.debug("preflight navigate: page switch failed", exc_info=True)

    def _book_layout(self) -> BookLayout:
        getter = getattr(self._window, "_get_document_service", None)
        document = getter() if callable(getter) else None
        resolver = getattr(document, "resolve_book_layout", None)
        return resolver() if callable(resolver) else BookLayout()

    # -- helpers --------------------------------------------------------------------

    def _pages(self) -> Any:
        return getattr(getattr(self._window, "_host_api", None), "pages", None)

    def _document_name(self) -> str:
        document_path = getattr(self._window, "_document_path", None)
        return Path(document_path).stem if document_path else "untitled"

    def _has_color_profile(self) -> bool:
        canvas = getattr(getattr(self._window, "_host_api", None), "canvas", None)
        profile = getattr(canvas, "color_profile", None)
        return bool(profile is not None and getattr(profile, "data", None))

    def _roles_by_id(self, pages: Any) -> dict[str, str]:
        reader = getattr(pages, "get_page_metadata", None)
        list_pages = getattr(pages, "list_pages", None)
        if not callable(reader) or not callable(list_pages):
            return {}
        try:
            metadata_by_id = {str(s.id): reader(s.id) for s in list_pages() or []}
            attributes = BookLayoutService().resolve_page_attributes_by_id(metadata_by_id)
            roles = {page_id: str(attr.role) for page_id, attr in attributes.items()}
        except Exception:
            logger.debug("preflight: page roles unavailable", exc_info=True)
            return {}
        self._window._preflight_roles = roles
        return roles

    def _fallback_document_metadata(self) -> DocumentMetadata:
        canvas = getattr(getattr(self._window, "_host_api", None), "canvas", None)
        return DocumentMetadata(
            width=int(getattr(canvas, "_width", 1) or 1),
            height=int(getattr(canvas, "_height", 1) or 1),
        )
