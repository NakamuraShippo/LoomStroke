"""Small identities for transient template-dialog input, without copying pages."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True, eq=False)
class PageTemplateEditScope:
    service: Any
    store: Any
    path: Path | None
    document: Any

    def matches(self, other: PageTemplateEditScope) -> bool:
        return (
            self.service is other.service
            and self.store is other.store
            and self.path == other.path
            and self.document is other.document
        )


def capture_page_template_edit_scope(service: Any) -> PageTemplateEditScope:
    manager = getattr(service, "_template_manager", None)
    store = getattr(manager, "template_store", None)
    path = getattr(store, "_template_file", None)
    canvas = getattr(service, "_canvas", None)
    current = getattr(canvas, "current_document", None)
    return PageTemplateEditScope(
        service,
        store,
        Path(path) if path is not None else None,
        current() if callable(current) else None,
    )
