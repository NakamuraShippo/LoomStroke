"""``File > Font Diagnostics...`` action for MainWindow (CT-25 F1 / F2 / F4).

Inventory of requested fonts across the document (resolved against the
Qt font database), explicit substitution on the current page (one history
transaction of ``VectorReplaceCommand``s, original request kept in the
payload) and text-to-outline conversion (``ConvertTextToOutlinesCommand``,
restorable).  Other pages are reported, never edited silently.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtWidgets import QMessageBox

from ..core.commands_text_outline import ConvertTextToOutlinesCommand
from ..core.fonts.font_inventory_service import FontInventoryService
from ..core.fonts.font_request import FontRequest
from ..core.fonts.font_substitution_planner import FontSubstitutionPlanner
from ..core.fonts.font_usage import FontUsage
from ..core.fonts.font_usage_inventory import FontUsageInventory
from ..plugins.vector_tools.common import VectorObject, VectorReplaceCommand
from .dialogs.font_diagnostics_dialog import FontDiagnosticsDialog
from .document_text_payload_reader import DocumentTextPayloadReader
from .font_outline_service import FontOutlineService
from .qt_font_catalog import QtFontCatalog
from .translations import translate_with_fallback

logger = logging.getLogger(__name__)

__all__ = ["MainWindowFontDiagnosticsAction"]


def _ts(text: str) -> str:
    return translate_with_fallback("FontDiagnosticsDialog", text)


class MainWindowFontDiagnosticsAction:
    def __init__(
        self,
        window: Any,
        *,
        catalog: Any | None = None,
        reader_factory: Callable[[Any], Any] | None = None,
        dialog_factory: Callable[..., Any] | None = None,
        outline_factory: Callable[[Any], Any] | None = None,
        message_box: Callable[[], Any] | None = None,
    ) -> None:
        self._window = window
        self._catalog = catalog or QtFontCatalog()
        self._reader_factory = reader_factory or DocumentTextPayloadReader
        self._dialog_factory = dialog_factory or FontDiagnosticsDialog
        self._outline_factory = outline_factory or FontOutlineService
        self._message_box = message_box or (lambda: QMessageBox)
        self._planner = FontSubstitutionPlanner()

    # -- inventory -----------------------------------------------------------------

    def _host(self) -> Any:
        return getattr(self._window, "_host_api", None)

    def inventory(self) -> FontUsageInventory:
        reader = self._reader_factory(self._host())
        return FontInventoryService().inventory(reader.entries(), self._catalog)

    def run(self) -> bool:
        host = self._host()
        if host is None or getattr(host, "canvas", None) is None:
            self._message_box().warning(
                self._window, _ts("Font Diagnostics"), _ts("No document is open.")
            )
            return False
        inventory = self.inventory()
        dialog = self._dialog_factory(
            inventory=inventory,
            families=self._catalog.families(),
            on_substitute=self.substitute,
            on_outline=self.outline,
            parent=self._window,
        )
        dialog.exec()
        return True

    # -- substitution ----------------------------------------------------------------

    def substitute(self, usage: FontUsage, target: FontRequest, scope: str) -> str:
        """Apply ``target`` to the current page's objects using ``usage``'s font."""
        inventory = self.inventory()
        reader = self._reader_factory(self._host())
        page_id = reader.active_page_id() or "active"
        plan = self._planner.plan(inventory, source=usage.request, target=target, scope="document")
        refs_here = {ref[2] for ref in plan.refs_for_page(page_id)}
        other = len(plan.object_refs) - len(refs_here)
        commands: list[VectorReplaceCommand] = []
        for layer_id, obj in reader.active_page_objects():
            if str(getattr(obj, "id", "")) not in refs_here:
                continue
            meta = dict(getattr(obj, "meta", {}) or {})
            meta["text_tool"] = self._planner.apply_to_payload(meta.get("text_tool") or {}, plan)
            replacement = VectorObject(
                id=obj.id, paths=list(obj.paths), style=obj.style, meta=meta, preview=False
            )
            commands.append(VectorReplaceCommand(obj.id, replacement, layer_id=layer_id or None))
        applied = self._submit_all(commands, _ts("Substitute Font"))
        message = _ts("Substituted {count} text object(s) on the current page.").format(
            count=applied
        )
        if scope == "document" and other:
            message += " " + _ts("{count} object(s) on other pages were left unchanged.").format(
                count=other
            )
        return message

    # -- outline ---------------------------------------------------------------------

    def outline(self, usage: FontUsage) -> str:
        reader = self._reader_factory(self._host())
        page_id = reader.active_page_id() or "active"
        wanted = {ref[2] for ref in usage.object_refs if ref[0] == page_id}
        canvas = getattr(self._host(), "canvas", None)
        service = self._outline_factory(canvas)
        commands: list[ConvertTextToOutlinesCommand] = []
        skipped = 0
        for layer_id, obj in reader.active_page_objects():
            if str(getattr(obj, "id", "")) not in wanted:
                continue
            outline = service.outline(obj)
            if outline is None or outline.is_empty:
                skipped += 1
                continue
            commands.append(
                ConvertTextToOutlinesCommand(obj.id, outline, layer_id=layer_id or None)
            )
        applied = self._submit_all(commands, _ts("Convert Text to Outlines"))
        message = _ts("Converted {count} text object(s) to outlines.").format(count=applied)
        if skipped:
            message += " " + _ts("{count} object(s) could not be outlined.").format(count=skipped)
        return message

    # -- helpers ---------------------------------------------------------------------

    def _submit_all(self, commands: list[Any], label: str) -> int:
        canvas = getattr(self._host(), "canvas", None)
        submit = getattr(canvas, "submit", None)
        if not commands or not callable(submit):
            return 0
        begin = getattr(canvas, "begin_transaction", None)
        end = getattr(canvas, "end_transaction", None)
        applied = 0
        if callable(begin):
            begin(label)
        try:
            for command in commands:
                try:
                    if submit(command):
                        applied += 1
                except Exception:
                    logger.warning("font command failed", exc_info=True)
        finally:
            if callable(end):
                end()
        return applied
