"""Manga physical page profiles (CT-07 Phase T1).

Owns the physical model of a manga page: trim size in mm, bleed and safe
margins, DPI — and derives from it the pixel canvas size, the trim /
bleed / safe guide positions, and the standard layer stack.  Pure and
Qt-free; the UI never computes physical dimensions itself (spec 07 §7).

Conventions:

- The canvas covers **trim + bleed** (bleed edge == canvas edge), so the
  bleed guides sit exactly on the canvas border.
- Pixel conversion is ``round(mm / 25.4 * dpi)`` and the result is shown
  to the user, never silently adjusted (spec 07 §5.1).
- Built-in profile pixel sizes are validated against the import pixel
  cap so a profile can never create a document other subsystems refuse.
- Layer *display names* are translation keys; the stable role rides in
  layer metadata so renaming or translating never loses the meaning.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .page_templates import LayerTemplate

__all__ = [
    "BUILTIN_COMIC_PROFILES",
    "ComicProfile",
    "GuideTemplate",
    "PhysicalPageLayout",
    "canvas_pixel_size",
    "comic_profile",
    "guides_as_view_state",
    "layout_guides",
    "mm_to_px",
    "standard_manga_layers",
]

#: Same ceiling FileIO enforces on imports (8192 x 8192).
_MAX_CANVAS_PIXELS = 67_108_864

LAYER_ROLE_NAMESPACE = "loomstroke.comictools.layer"


@dataclass(frozen=True, slots=True)
class PhysicalPageLayout:
    """Physical page description; margins are (left, top, right, bottom)."""

    trim_mm: tuple[float, float]
    bleed_mm: tuple[float, float, float, float] = (3.0, 3.0, 3.0, 3.0)
    safe_mm: tuple[float, float, float, float] = (5.0, 5.0, 5.0, 5.0)
    dpi: tuple[float, float] = (600.0, 600.0)

    def __post_init__(self) -> None:
        if min(self.trim_mm) <= 0:
            raise ValueError("trim size must be positive")
        if min(self.dpi) <= 0:
            raise ValueError("dpi must be positive")
        if min(*self.bleed_mm, *self.safe_mm) < 0:
            raise ValueError("margins cannot be negative")
        width, height = canvas_pixel_size(self)
        if width * height > _MAX_CANVAS_PIXELS:
            raise ValueError(
                f"profile produces {width}x{height} px, above the "
                f"{_MAX_CANVAS_PIXELS} pixel canvas ceiling"
            )


@dataclass(frozen=True, slots=True)
class GuideTemplate:
    """One derived guide line, in canvas pixels."""

    role: Literal["trim", "bleed", "safe"]
    orientation: Literal["horizontal", "vertical"]
    position_px: float


@dataclass(frozen=True, slots=True)
class ComicProfile:
    """A built-in manga document preset (code asset, never persisted)."""

    id: str
    name: str
    color_intent: Literal["mono", "color"]
    layout: PhysicalPageLayout


def mm_to_px(mm: float, dpi: float) -> int:
    return round(mm / 25.4 * dpi)


def canvas_pixel_size(layout: PhysicalPageLayout) -> tuple[int, int]:
    """Canvas covers trim + bleed on every side."""
    left, top, right, bottom = layout.bleed_mm
    width = mm_to_px(layout.trim_mm[0] + left + right, layout.dpi[0])
    height = mm_to_px(layout.trim_mm[1] + top + bottom, layout.dpi[1])
    return width, height


def layout_guides(layout: PhysicalPageLayout) -> tuple[GuideTemplate, ...]:
    """Trim / bleed / safe guide lines for the layout's canvas."""
    width, height = canvas_pixel_size(layout)
    bleed_left, bleed_top, _r, _b = layout.bleed_mm
    safe_left, safe_top, safe_right, safe_bottom = layout.safe_mm
    dpi_x, dpi_y = layout.dpi

    trim_left = float(mm_to_px(bleed_left, dpi_x))
    trim_top = float(mm_to_px(bleed_top, dpi_y))
    trim_right = float(mm_to_px(bleed_left + layout.trim_mm[0], dpi_x))
    trim_bottom = float(mm_to_px(bleed_top + layout.trim_mm[1], dpi_y))

    return (
        # Bleed edge == canvas edge by construction.
        GuideTemplate("bleed", "vertical", 0.0),
        GuideTemplate("bleed", "vertical", float(width)),
        GuideTemplate("bleed", "horizontal", 0.0),
        GuideTemplate("bleed", "horizontal", float(height)),
        GuideTemplate("trim", "vertical", trim_left),
        GuideTemplate("trim", "vertical", trim_right),
        GuideTemplate("trim", "horizontal", trim_top),
        GuideTemplate("trim", "horizontal", trim_bottom),
        GuideTemplate("safe", "vertical", trim_left + mm_to_px(safe_left, dpi_x)),
        GuideTemplate("safe", "vertical", trim_right - mm_to_px(safe_right, dpi_x)),
        GuideTemplate("safe", "horizontal", trim_top + mm_to_px(safe_top, dpi_y)),
        GuideTemplate("safe", "horizontal", trim_bottom - mm_to_px(safe_bottom, dpi_y)),
    )


def guides_as_view_state(guides: tuple[GuideTemplate, ...]) -> list[dict[str, object]]:
    """The exact shape ``GuideController`` reads from
    ``page.view_state["guides"]`` — ids are stable per role/side so a
    re-application replaces rather than duplicates."""
    entries: list[dict[str, object]] = []
    counters: dict[str, int] = {}
    for guide in guides:
        key = f"{guide.role}-{guide.orientation[0]}"
        counters[key] = counters.get(key, 0) + 1
        entries.append(
            {
                "id": f"tpl-{key}-{counters[key]}",
                "orientation": guide.orientation,
                "position": float(guide.position_px),
            }
        )
    return entries


def _layer_metadata(role: str) -> dict[str, object]:
    return {"plugins": {LAYER_ROLE_NAMESPACE: {"schema_version": 1, "role": role}}}


def standard_manga_layers() -> tuple[LayerTemplate, ...]:
    """Bottom-to-top standard stack (spec 07 §5.1).  Display names are
    translation keys; the stable role lives in metadata."""
    return (
        LayerTemplate(name="Paper", fill="white", locked=True, metadata=_layer_metadata("paper")),
        LayerTemplate(name="Draft", metadata=_layer_metadata("draft")),
        LayerTemplate(name="Ink", metadata=_layer_metadata("ink")),
        LayerTemplate(name="Tone", metadata=_layer_metadata("tone")),
        LayerTemplate(name="Text", metadata=_layer_metadata("text")),
        LayerTemplate(name="Notes", visible=False, metadata=_layer_metadata("notes")),
    )


def _profile(
    profile_id: str,
    name: str,
    color_intent: Literal["mono", "color"],
    trim_mm: tuple[float, float],
    dpi: float,
) -> ComicProfile:
    return ComicProfile(
        id=profile_id,
        name=name,
        color_intent=color_intent,
        layout=PhysicalPageLayout(trim_mm=trim_mm, dpi=(dpi, dpi)),
    )


#: Standard trims: B5 182x257 / A5 148x210 / A4 210x297; mono 600 dpi,
#: color 350 dpi; bleed 3 mm; safe 5 mm (printer specs take precedence —
#: the UI carries that note, spec 07 §5.1).
BUILTIN_COMIC_PROFILES: tuple[ComicProfile, ...] = (
    _profile("comic.b5.mono", "B5 Body (Monochrome)", "mono", (182.0, 257.0), 600.0),
    _profile("comic.b5.color", "B5 Body (Color)", "color", (182.0, 257.0), 350.0),
    _profile("comic.a5.mono", "A5 Body (Monochrome)", "mono", (148.0, 210.0), 600.0),
    _profile("comic.a5.color", "A5 Body (Color)", "color", (148.0, 210.0), 350.0),
    _profile("comic.a4.mono", "A4 Body (Monochrome)", "mono", (210.0, 297.0), 600.0),
    _profile("comic.a4.color", "A4 Body (Color)", "color", (210.0, 297.0), 350.0),
)


def comic_profile(profile_id: str) -> ComicProfile:
    for profile in BUILTIN_COMIC_PROFILES:
        if profile.id == profile_id:
            return profile
    raise KeyError(f"unknown comic profile: {profile_id!r}")


def builtin_comic_page_templates() -> tuple[Any, ...]:
    """The built-in profiles as ``PageTemplate`` entries (T1b).

    Guides ride in ``template.metadata["view_state"]`` — the page service
    merges that into the new page's view state after creation, which is
    exactly the §10.1 materialization (no schema change).
    """
    from ..host_api import CanvasSpec as HostCanvasSpec  # noqa: PLC0415
    from .page_templates import PageTemplate  # noqa: PLC0415

    templates = []
    for profile in BUILTIN_COMIC_PROFILES:
        layout = profile.layout
        width, height = canvas_pixel_size(layout)
        templates.append(
            PageTemplate(
                id=profile.id,
                name=profile.name,
                category="comic",
                source="builtin",
                canvas_mode="fixed",
                canvas=HostCanvasSpec(
                    width=width,
                    height=height,
                    dpi=layout.dpi,
                    background_color=(255, 255, 255, 255),
                ),
                layers=standard_manga_layers(),
                description=(
                    f"trim {layout.trim_mm[0]:g}x{layout.trim_mm[1]:g} mm, "
                    f"{layout.dpi[0]:g} dpi, bleed {layout.bleed_mm[0]:g} mm "
                    f"-> {width}x{height} px"
                ),
                metadata={
                    "comic_profile": profile.id,
                    "view_state": {"guides": guides_as_view_state(layout_guides(layout))},
                },
            )
        )
    return tuple(templates)
