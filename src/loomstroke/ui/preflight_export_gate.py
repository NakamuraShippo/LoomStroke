"""Export entry-point gate over the last preflight report (CT-16 §6 / §14).

Used by Export Pages / Export for Print / Export Webtoon right before
they write.  Fatal issues in a *current* report need one explicit
confirmation; a stale report earns a reminder and the export proceeds;
no report means no gate (preflight is opt-in).  Warnings never block.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtWidgets import QMessageBox

from ..core.preflight.preflight_export_guard import PreflightExportGuard
from ..core.preflight.preflight_report import PreflightReport
from ..io.layer_io import DocumentMetadata
from .preflight_snapshot_builder import DocumentPreflightSnapshotBuilder
from .translations import translate_with_fallback

__all__ = ["PreflightExportGate"]

REPORT_ATTRIBUTE = "_preflight_last_report"


def _ts(text: str) -> str:
    return translate_with_fallback("MangaPreflightDialog", text)


class PreflightExportGate:
    """``allow(title)`` -> whether the export may continue."""

    def __init__(
        self,
        window: Any,
        *,
        document_metadata: Callable[[], DocumentMetadata] | None = None,
        message_box: Callable[[], Any] | None = None,
        guard: PreflightExportGuard | None = None,
    ) -> None:
        self._window = window
        self._document_metadata = document_metadata
        self._message_box = message_box or (lambda: QMessageBox)
        self._guard = guard or PreflightExportGuard()

    @staticmethod
    def last_report(window: Any) -> PreflightReport | None:
        report = getattr(window, REPORT_ATTRIBUTE, None)
        return report if isinstance(report, PreflightReport) else None

    @staticmethod
    def remember(window: Any, report: PreflightReport | None) -> None:
        setattr(window, REPORT_ATTRIBUTE, report)

    def allow(self, title: str) -> bool:
        report = self.last_report(self._window)
        if report is None:
            return True
        decision = self._guard.decide(
            report,
            structure_fingerprint=self._structure_fingerprint(),
            dirty_signature=self._dirty_signature(),
        )
        window = self._window
        if decision.status == "confirm_fatal":
            answer = self._message_box().question(
                window,
                _ts(title),
                _ts("Preflight found {count} fatal issue(s). Export anyway?").format(
                    count=decision.fatal_count
                ),
            )
            return answer == QMessageBox.StandardButton.Yes
        if decision.status == "stale":
            self._message_box().information(
                window,
                _ts(title),
                _ts("The preflight report is out of date; run Manga Preflight again."),
            )
        return True

    # -- helpers --------------------------------------------------------------------

    def _summaries(self) -> list[Any]:
        pages = getattr(getattr(self._window, "_host_api", None), "pages", None)
        list_pages = getattr(pages, "list_pages", None)
        return list(list_pages() or []) if callable(list_pages) else []

    def _metadata(self) -> DocumentMetadata:
        if self._document_metadata is not None:
            return self._document_metadata()
        host_api = getattr(self._window, "_host_api", None)
        canvas = getattr(host_api, "canvas", None) if host_api else None
        return DocumentMetadata(
            width=int(getattr(canvas, "_width", 1) or 1),
            height=int(getattr(canvas, "_height", 1) or 1),
        )

    def _structure_fingerprint(self) -> str:
        return DocumentPreflightSnapshotBuilder.structure_fingerprint_for(
            self._summaries(), self._metadata(), getattr(self._window, "_preflight_roles", None)
        )

    def _dirty_signature(self) -> str:
        return DocumentPreflightSnapshotBuilder.dirty_signature_for(self._summaries())
