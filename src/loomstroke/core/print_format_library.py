"""Physical-only presets stored alongside existing page templates."""

from __future__ import annotations

import math
from dataclasses import dataclass
from uuid import uuid4

from .comic_document_profile import PhysicalPageLayout
from .page_templates import PageTemplate, PageTemplateStore


@dataclass(frozen=True)
class PrintFormat:
    name: str
    layout: PhysicalPageLayout
    gutter_mm: float = 0.0

    def __post_init__(self) -> None:
        values = (
            *self.layout.trim_mm,
            *self.layout.bleed_mm,
            *self.layout.safe_mm,
            *self.layout.dpi,
            self.gutter_mm,
        )
        if not self.name.strip() or len(self.name) > 64 or any(ord(c) < 32 for c in self.name):
            raise ValueError("Invalid format name")
        if not all(math.isfinite(v) for v in values) or self.gutter_mm < 0:
            raise ValueError("Invalid physical format")
        w, h = self.layout.trim_mm
        left, top, right, bottom = self.layout.safe_mm
        if left + right + self.gutter_mm >= w or top + bottom >= h:
            raise ValueError("No safe area remains")


class PrintFormatLibrary:
    def __init__(self, store: PageTemplateStore) -> None:
        self.store = store

    def formats(self) -> dict[str, PrintFormat]:
        result = {}
        for template in self.store.list_templates():
            if template.category != "print_format":
                continue
            raw = (template.metadata or {}).get("print_format", {})
            try:
                layout = PhysicalPageLayout(
                    trim_mm=tuple(raw["trim_mm"]),
                    bleed_mm=tuple(raw["bleed_mm"]),
                    safe_mm=tuple(raw["safe_mm"]),
                    dpi=tuple(raw["dpi"]),
                )
                result[template.id] = PrintFormat(template.name, layout, raw["gutter_mm"])
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
        return result

    def save(self, value: PrintFormat) -> str:
        layout = value.layout
        template = PageTemplate(
            id="print_format." + uuid4().hex,
            name=value.name.strip(),
            category="print_format",
            source="user",
            canvas_mode="document_default",
            metadata={
                "print_format": {
                    "trim_mm": list(layout.trim_mm),
                    "bleed_mm": list(layout.bleed_mm),
                    "safe_mm": list(layout.safe_mm),
                    "dpi": list(layout.dpi),
                    "gutter_mm": value.gutter_mm,
                }
            },
        )
        return self.store.save_user_template(template).id
