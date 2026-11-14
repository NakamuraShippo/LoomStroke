"""Physical formats and user print-submission presets in the page-template store."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from uuid import uuid4

from .comic_document_profile import PhysicalPageLayout
from .page_templates import PageTemplate, PageTemplateStore
from .print_mark_models import PrintMarkOptions


@dataclass(frozen=True)
class PrintSubmissionSettings:
    output: str = "pdf"
    with_marks: bool = True
    mark_options: PrintMarkOptions = field(default_factory=PrintMarkOptions)
    cmyk_profile_id: str | None = None
    intent: str = "relative"
    black_point_compensation: bool = True
    threshold: int = 128
    dither: str = "threshold"

    def __post_init__(self) -> None:
        if self.output not in (
            "pdf",
            "pdfx1a",
            "pdfx4",
            "cmyk_tiff",
            "grayscale_k_tiff",
            "grayscale_tiff",
            "bilevel_tiff",
        ):
            raise ValueError("Unsupported print output")
        if self.intent not in ("relative", "perceptual", "saturation", "absolute"):
            raise ValueError("Unsupported rendering intent")
        if self.cmyk_profile_id is not None and (
            not isinstance(self.cmyk_profile_id, str) or len(self.cmyk_profile_id) > 128
        ):
            raise ValueError("Invalid CMYK profile reference")
        if not 0 <= self.threshold <= 255 or self.dither not in ("threshold", "ordered4x4"):
            raise ValueError("Invalid bilevel conversion options")


@dataclass(frozen=True)
class PrintFormat:
    name: str
    layout: PhysicalPageLayout
    gutter_mm: float = 0.0
    print_settings: PrintSubmissionSettings | None = None

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
                settings_raw = (template.metadata or {}).get("print_submission")
                settings = None
                if settings_raw is not None:
                    if not isinstance(settings_raw, Mapping):
                        raise ValueError("Invalid print submission settings")
                    settings = PrintSubmissionSettings(
                        output=settings_raw["output"],
                        with_marks=settings_raw["with_marks"],
                        mark_options=PrintMarkOptions(**settings_raw["mark_options"]),
                        cmyk_profile_id=settings_raw.get("cmyk_profile_id"),
                        intent=settings_raw["intent"],
                        black_point_compensation=settings_raw["black_point_compensation"],
                        threshold=settings_raw.get("threshold", 128),
                        dither=settings_raw.get("dither", "threshold"),
                    )
                result[template.id] = PrintFormat(template.name, layout, raw["gutter_mm"], settings)
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
        return result

    def submission_presets(self) -> dict[str, PrintFormat]:
        return {key: value for key, value in self.formats().items() if value.print_settings}

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
                },
                **(
                    {
                        "print_submission": {
                            "output": value.print_settings.output,
                            "with_marks": value.print_settings.with_marks,
                            "mark_options": asdict(value.print_settings.mark_options),
                            "cmyk_profile_id": value.print_settings.cmyk_profile_id,
                            "intent": value.print_settings.intent,
                            "black_point_compensation": (
                                value.print_settings.black_point_compensation
                            ),
                            "threshold": value.print_settings.threshold,
                            "dither": value.print_settings.dither,
                        }
                    }
                    if value.print_settings is not None
                    else {}
                ),
            },
        )
        return self.store.save_user_template(template).id
