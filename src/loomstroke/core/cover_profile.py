"""Physical wrap-cover geometry and existing page-template materialization."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..host_api import CanvasSpec
from .comic_document_profile import (
    PhysicalPageLayout,
    canvas_pixel_size,
    guides_as_view_state,
    layout_guides,
    mm_to_px,
    standard_manga_layers,
)
from .page_templates import PageTemplate


@dataclass(frozen=True)
class CoverProfile:
    trim_mm: tuple[float, float] = (148.0, 210.0)
    page_count: int = 32
    paper_mm: float = 0.1
    spine_mm: float | None = None
    flap_mm: float = 0.0
    bleed_mm: float = 3.0
    dpi: tuple[float, float] = (350.0, 350.0)
    binding: str = "right"

    def __post_init__(self):
        values = (*self.trim_mm, self.paper_mm, self.flap_mm, self.bleed_mm, *self.dpi)
        if self.spine_mm is not None:
            values += (self.spine_mm,)
        if (
            not all(math.isfinite(v) for v in values)
            or min(self.trim_mm) <= 0
            or min(self.dpi) <= 0
        ):
            raise ValueError("Invalid cover dimensions")
        if min(self.paper_mm, self.flap_mm, self.bleed_mm, self.spine_width) < 0:
            raise ValueError("Negative cover margin")
        if self.page_count < 1 or self.page_count > 100000 or self.binding not in ("left", "right"):
            raise ValueError("Invalid cover settings")
        canvas_pixel_size(self.layout)

    @property
    def spine_width(self):
        return (
            self.spine_mm
            if self.spine_mm is not None
            else math.ceil(self.page_count / 2) * self.paper_mm
        )

    @property
    def layout(self):
        width = 2 * self.trim_mm[0] + self.spine_width + 2 * self.flap_mm
        return PhysicalPageLayout(
            trim_mm=(width, self.trim_mm[1]),
            bleed_mm=(self.bleed_mm,) * 4,
            safe_mm=(0, 0, 0, 0),
            dpi=self.dpi,
        )

    def faces(self):
        w, h = self.trim_mm
        x = self.bleed_mm + self.flap_mm
        left = "front" if self.binding == "right" else "back"
        right = "back" if left == "front" else "front"
        result = {
            left: (x, self.bleed_mm, x + w, self.bleed_mm + h),
            "spine": (x + w, self.bleed_mm, x + w + self.spine_width, self.bleed_mm + h),
            right: (
                x + w + self.spine_width,
                self.bleed_mm,
                x + 2 * w + self.spine_width,
                self.bleed_mm + h,
            ),
        }
        if self.flap_mm:
            result["left_flap"] = (self.bleed_mm, self.bleed_mm, x, self.bleed_mm + h)
            result["right_flap"] = (
                x + 2 * w + self.spine_width,
                self.bleed_mm,
                x + 2 * w + self.spine_width + self.flap_mm,
                self.bleed_mm + h,
            )
        return result

    def guides(self):
        guides = guides_as_view_state(layout_guides(self.layout))
        for face, box in self.faces().items():
            for kind, margin in (("trim", 0), ("bleed", self.bleed_mm)):
                for orientation, axis, values in (
                    ("vertical", 0, (box[0] - margin, box[2] + margin)),
                    ("horizontal", 1, (box[1] - margin, box[3] + margin)),
                ):
                    for i, value in enumerate(values):
                        guides.append(
                            {
                                "id": f"cover-{face}-{kind}-{orientation[0]}-{i}",
                                "orientation": orientation,
                                "position": float(mm_to_px(value, self.dpi[axis])),
                            }
                        )
        folds = {b[0] for b in self.faces().values()} | {b[2] for b in self.faces().values()}
        outer = (self.bleed_mm, self.layout.trim_mm[0] + self.bleed_mm)
        for i, value in enumerate(sorted(v for v in folds if v not in outer)):
            guides.append(
                {
                    "id": f"cover-fold-v-{i}",
                    "orientation": "vertical",
                    "position": float(mm_to_px(value, self.dpi[0])),
                }
            )
        return guides


def cover_template(profile: CoverProfile, *, name: str, template_id: str, source="user"):
    if not name.strip() or len(name) > 64 or any(ord(c) < 32 for c in name):
        raise ValueError("Invalid template name")
    width, height = canvas_pixel_size(profile.layout)
    return PageTemplate(
        template_id,
        name.strip(),
        "cover",
        source,
        "fixed",
        canvas=CanvasSpec(width, height, profile.dpi, (255, 255, 255, 255)),
        layers=standard_manga_layers(),
        metadata={
            "comic_profile": "cover",
            "cover_profile": {
                "trim_mm": list(profile.trim_mm),
                "spine_mm": profile.spine_width,
                "flap_mm": profile.flap_mm,
                "bleed_mm": profile.bleed_mm,
                "binding": profile.binding,
                "page_count": profile.page_count,
                "paper_mm": profile.paper_mm,
            },
            "view_state": {"guides": profile.guides()},
        },
    )


def builtin_cover_templates():
    return (
        cover_template(
            CoverProfile(),
            name="A5 Wrap Cover (Color)",
            template_id="comic.cover.a5.color",
            source="builtin",
        ),
    )
