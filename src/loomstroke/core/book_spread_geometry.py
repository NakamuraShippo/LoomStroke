"""Placement geometry for a spread's two pages (CT-06 B4-c1).

Pure and Qt-free, like ``book_spread_layout``.  That module answers *which*
pages share a spread; this one answers *where* they sit relative to each
other so the canvas can draw both in one viewport (spec §6.3.1).

The two are deliberately separate files: deriving the logical spread list
from page order and computing display rectangles are independent reasons to
change.

Coordinates are "spread space": origin at the spread's top-left, x growing
right, in canvas pixels.  Binding direction is **not** applied here --
``SpreadDescriptor.slots`` already arrives in physical ``(left, right)``
order with the binding baked in, so re-interpreting it would flip the book
twice.

``Fit Spread`` needs no separate zoom maths: pass the returned
``width``/``height`` to the viewport's existing ``fit_to_content()``.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from loomstroke.core.book_spread_layout import SpreadDescriptor

logger = logging.getLogger(__name__)

PhysicalSide = Literal["left", "right"]

__all__ = [
    "SpreadGeometry",
    "SpreadPageRect",
    "compute_spread_geometry",
]


@dataclass(frozen=True, slots=True)
class SpreadPageRect:
    """Where one slot of a spread sits, in spread space.

    ``page_id`` is ``None`` for an empty slot (e.g. the blank facing the
    cover).  Empty slots still occupy their rectangle: dropping them would
    slide the cover into the middle of the spread instead of leaving it on
    its binding-correct side.
    """

    page_id: str | None
    physical_side: PhysicalSide
    x: float
    y: float
    width: float
    height: float

    @property
    def is_empty(self) -> bool:
        return self.page_id is None

    @property
    def right_edge(self) -> float:
        return self.x + self.width

    @property
    def bottom_edge(self) -> float:
        return self.y + self.height


@dataclass(frozen=True, slots=True)
class SpreadGeometry:
    """Both slots placed, plus the bounding box they span.

    The bounding box always starts at the origin, so ``width``/``height``
    are the full extent of the spread.
    """

    rects: tuple[SpreadPageRect, SpreadPageRect]
    width: float
    height: float

    @property
    def left(self) -> SpreadPageRect:
        return self.rects[0]

    @property
    def right(self) -> SpreadPageRect:
        return self.rects[1]

    def rect_for_page(self, page_id: str) -> SpreadPageRect | None:
        """The rectangle a real page occupies, or ``None`` if not in this spread."""
        for rect in self.rects:
            if rect.page_id is not None and rect.page_id == page_id:
                return rect
        return None

    def hit_test(self, x: float, y: float) -> SpreadPageRect | None:
        """Which slot contains a spread-space point, if any.

        Returns ``None`` for the gutter and anything outside the spread.
        Empty slots *are* returned: the caller decides what an empty side
        means (it is not a page, but it is not the surrounding canvas
        either).  Callers routing input must therefore check ``is_empty``
        as well -- the canvas must not hand tool input to a slot with no
        page behind it (spec §6.3).

        Edges belong to the rectangle: a point exactly on ``x`` or ``y`` is
        inside, one exactly on the far edge is not, so two touching pages
        with no gutter never both claim the same point.
        """
        for rect in self.rects:
            if rect.x <= x < rect.right_edge and rect.y <= y < rect.bottom_edge:
                return rect
        return None


def _slot_size(
    page_id: str | None,
    page_sizes: Mapping[str, tuple[float, float]],
    fallback: tuple[float, float],
) -> tuple[float, float]:
    if page_id is None:
        return fallback
    size = page_sizes.get(page_id)
    if size is None:
        # A page whose size we were not given still has to occupy its side,
        # otherwise the facing page would shift across the gutter.
        logger.warning("spread geometry: no size for page %r; using fallback", page_id)
        return fallback
    width, height = float(size[0]), float(size[1])
    if width <= 0 or height <= 0:
        logger.warning(
            "spread geometry: non-positive size %sx%s for page %r; using fallback",
            width,
            height,
            page_id,
        )
        return fallback
    return width, height


def compute_spread_geometry(
    spread: SpreadDescriptor,
    page_sizes: Mapping[str, tuple[float, float]],
    *,
    fallback_size: tuple[float, float],
    gutter: float = 0.0,
) -> SpreadGeometry:
    """Place a spread's two slots side by side in spread space.

    Args:
        spread: descriptor from ``resolve_spreads`` (slots already in
            physical left-to-right order).
        page_sizes: ``page_id -> (width, height)`` in canvas pixels.
            Missing or non-positive entries fall back to ``fallback_size``.
        fallback_size: size for empty slots and unknown pages. Must be
            positive -- there is nothing further to fall back to.
        gutter: gap between the two pages, in canvas pixels. Negative
            values are treated as 0.

    Pages of unequal height are centred vertically against each other, so a
    shorter page sits mid-height rather than hanging off the top edge.
    """
    fallback_width, fallback_height = float(fallback_size[0]), float(fallback_size[1])
    if fallback_width <= 0 or fallback_height <= 0:
        raise ValueError(f"fallback_size must be positive, got {fallback_size!r}")

    gap = max(0.0, float(gutter))

    left_slot, right_slot = spread.slots
    left_width, left_height = _slot_size(left_slot.page_id, page_sizes, fallback_size)
    right_width, right_height = _slot_size(right_slot.page_id, page_sizes, fallback_size)

    total_width = left_width + gap + right_width
    total_height = max(left_height, right_height)

    left_rect = SpreadPageRect(
        page_id=left_slot.page_id,
        physical_side=left_slot.physical_side,
        x=0.0,
        y=(total_height - left_height) / 2.0,
        width=left_width,
        height=left_height,
    )
    right_rect = SpreadPageRect(
        page_id=right_slot.page_id,
        physical_side=right_slot.physical_side,
        x=left_width + gap,
        y=(total_height - right_height) / 2.0,
        width=right_width,
        height=right_height,
    )

    return SpreadGeometry(
        rects=(left_rect, right_rect),
        width=total_width,
        height=total_height,
    )
