"""UI-level action adapters (need Qt / renderer): text outline, page export (CT-20)."""

from __future__ import annotations

from loomstroke.core.actions import ActionCatalog

from .export_page_action import ExportPageAction
from .text_outline_page_action import TextOutlinePageAction

__all__ = ["ExportPageAction", "TextOutlinePageAction", "register_ui_actions"]


def register_ui_actions(catalog: ActionCatalog) -> ActionCatalog:
    outline = TextOutlinePageAction()
    catalog.register(outline.descriptor, outline.execute, replace=True)
    export = ExportPageAction()
    catalog.register(export.descriptor, export.execute, export.plan, replace=True)
    return catalog
