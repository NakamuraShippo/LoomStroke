"""Derived spread layout resolver for comic books (CT-06 Phase B1).

Pure, Qt-free derivation of spread (見開き) display units from the
logical page order.  Nothing here is persisted: spreads, empty slots,
recto/verso assignments, and the body-start adjustment are always
re-derived from ``PageService`` order + the (runtime) ``BookLayout`` /
per-page attributes — the ``.lstroke`` payload, page order, and page ids
never change (CT-06 spec §5/§8: spread は常に derived data).

Model (fixed by this phase, spec §5/§15):

- Pages fill alternating *faces*: face 0 is a recto, face 1 a verso, and
  so on.  Spread 0 shows face 0 alone (the cover face) with a virtual
  empty slot on its verso side; spread ``k`` (k >= 1) shows faces
  ``(2k-1, 2k)`` — the verso is read before the recto within a spread.
- Physical sides follow the binding: **left binding** (Western) puts the
  recto on the RIGHT and the verso on the LEFT (a "2-3" spread shows 2
  left, 3 right); **right binding** (Japanese manga) mirrors it — recto
  LEFT, verso RIGHT, so the lower logical page of a spread sits on the
  right and reading runs right-to-left.
- ``body_start`` nudges the first ``role == "body"`` page onto the
  requested face parity by inserting at most ONE virtual empty slot
  before it (``auto`` never adjusts).  Empty slots are display-only:
  no page is created, page count and save payload are unchanged.
- Spreads containing no real page are not emitted; emitted spreads are
  indexed contiguously.

Changing the binding direction never reorders pages — only the physical
side assignment of the same logical sequence changes (spec §15).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

BindingDirection = Literal["right", "left"]
BodyStartSide = Literal["auto", "recto", "verso"]
DefaultView = Literal["single", "spread"]

_VALID_BINDINGS: tuple[str, ...] = ("right", "left")
_VALID_BODY_STARTS: tuple[str, ...] = ("auto", "recto", "verso")

#: Role string that the body-start rule anchors on.  Unknown roles are
#: accepted and simply treated as non-body front/back matter — the
#: resolver never reorders pages based on roles (spec §5).
BODY_ROLE = "body"

#: The roles the UI offers, as (stored value, English label) pairs.  A
#: menu, not a validator: metadata accepts any string and unknown roles
#: round-trip untouched (spec §10).  Shared by the Pages panel context
#: menu and the Comic Book Settings dialog so the two can never drift.
KNOWN_PAGE_ROLES: tuple[tuple[str, str], ...] = (
    ("cover", "Cover"),
    ("inside_cover", "Inside Cover"),
    ("title", "Title"),
    ("body", "Body"),
    ("colophon", "Colophon"),
)


@dataclass(frozen=True, slots=True)
class BookLayout:
    """Runtime book-level layout settings (CT-06 spec §8; not persisted
    in this phase — B3 owns metadata persistence)."""

    binding: BindingDirection = "left"
    body_start: BodyStartSide = "auto"
    default_view: DefaultView = "single"
    folio_start: int = 1
    #: Display-only decoration for auto folios ("P." -> "P.3").  Custom
    #: folios are user strings and never get the prefix.
    folio_prefix: str = ""
    #: Roles whose pages show no folio.  Display-only: the pages still
    #: consume their number (2026-08-02, user decision), so hiding the
    #: cover never renumbers the body.
    folio_hidden_roles: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PageBookAttributes:
    """Per-page book attributes (runtime input; persisted in B3)."""

    role: str = BODY_ROLE
    folio_mode: Literal["auto", "hidden", "custom"] = "auto"
    custom_folio: str | None = None


@dataclass(frozen=True, slots=True)
class SpreadSlot:
    """One physical side of a spread; ``page_id=None`` is an empty slot.

    Empty slots are virtual display artifacts (e.g. the blank side next
    to a cover): they are never saved, exported, or counted as pages.
    """

    physical_side: Literal["left", "right"]
    page_id: str | None


@dataclass(frozen=True, slots=True)
class SpreadDescriptor:
    """One derived display unit holding 0-2 real pages.

    ``slots`` is always ``(left, right)`` in physical order; use the
    binding to know the reading order (right binding reads right first).
    """

    index: int
    slots: tuple[SpreadSlot, SpreadSlot]

    @property
    def left(self) -> SpreadSlot:
        return self.slots[0]

    @property
    def right(self) -> SpreadSlot:
        return self.slots[1]

    def page_ids(self) -> tuple[str, ...]:
        """Real page ids in this spread, in physical left-to-right order."""
        return tuple(slot.page_id for slot in self.slots if slot.page_id is not None)


_DEFAULT_ATTRIBUTES = PageBookAttributes()


def _normalized_binding(layout: BookLayout) -> BindingDirection:
    binding = getattr(layout, "binding", "left")
    if binding not in _VALID_BINDINGS:
        logger.warning("Invalid book binding %r; falling back to 'left' (spec §13)", binding)
        return "left"
    return binding  # type: ignore[return-value]


def _normalized_body_start(layout: BookLayout) -> BodyStartSide:
    body_start = getattr(layout, "body_start", "auto")
    if body_start not in _VALID_BODY_STARTS:
        logger.warning("Invalid body start side %r; falling back to 'auto' (spec §13)", body_start)
        return "auto"
    return body_start  # type: ignore[return-value]


def _layout_faces(
    page_ids: Sequence[str],
    body_start: BodyStartSide,
    attributes: Mapping[str, PageBookAttributes] | None,
) -> list[str | None]:
    """Assign pages to alternating recto/verso faces (``None`` = empty).

    Face parity: even face indexes are rectos, odd are versos.  At most
    one virtual empty face is inserted, immediately before the first
    body-role page, when ``body_start`` demands the other parity.
    """
    faces: list[str | None] = []
    body_adjusted = body_start == "auto"
    for page_id in page_ids:
        if not body_adjusted:
            role = (attributes or {}).get(page_id, _DEFAULT_ATTRIBUTES).role
            if role == BODY_ROLE:
                wanted_parity = 0 if body_start == "recto" else 1
                if len(faces) % 2 != wanted_parity:
                    faces.append(None)
                body_adjusted = True
        faces.append(page_id)
    return faces


def resolve_spreads(
    page_ids: Sequence[str],
    layout: BookLayout | None = None,
    attributes: Mapping[str, PageBookAttributes] | None = None,
) -> tuple[SpreadDescriptor, ...]:
    """Derive the spread list from the logical page order (pure).

    ``page_ids`` is the ``PageService.list_pages()`` id sequence and is
    never mutated or reordered.  Missing ``layout``/``attributes`` use
    the spec defaults (left binding, auto body start, role=body), and
    invalid values fall back with a warning (spec §13).
    """
    layout = layout if layout is not None else BookLayout()
    binding = _normalized_binding(layout)
    body_start = _normalized_body_start(layout)
    faces = _layout_faces(page_ids, body_start, attributes)

    recto_side: Literal["left", "right"] = "right" if binding == "left" else "left"
    verso_side: Literal["left", "right"] = "left" if binding == "left" else "right"

    def _spread_from(
        verso_page: str | None, recto_page: str | None
    ) -> tuple[SpreadSlot, SpreadSlot]:
        verso_slot = SpreadSlot(physical_side=verso_side, page_id=verso_page)
        recto_slot = SpreadSlot(physical_side=recto_side, page_id=recto_page)
        left, right = (verso_slot, recto_slot) if verso_side == "left" else (recto_slot, verso_slot)
        return (left, right)

    spreads: list[SpreadDescriptor] = []

    def _emit(verso_page: str | None, recto_page: str | None) -> None:
        if verso_page is None and recto_page is None:
            return  # spreads without a real page are not emitted
        spreads.append(
            SpreadDescriptor(index=len(spreads), slots=_spread_from(verso_page, recto_page))
        )

    # Spread 0: the first recto (cover face) alone, empty verso slot.
    if faces:
        _emit(None, faces[0])
    # Spread k: verso face (2k-1) faces recto face (2k).
    position = 1
    while position < len(faces):
        verso_page = faces[position]
        recto_page = faces[position + 1] if position + 1 < len(faces) else None
        _emit(verso_page, recto_page)
        position += 2

    return tuple(spreads)


def find_spread_for_page(
    spreads: Sequence[SpreadDescriptor], page_id: str
) -> SpreadDescriptor | None:
    """Return the spread containing ``page_id``, or ``None``."""
    for spread in spreads:
        if page_id in spread.page_ids():
            return spread
    return None


__all__ = [
    "BODY_ROLE",
    "BindingDirection",
    "BodyStartSide",
    "BookLayout",
    "DefaultView",
    "PageBookAttributes",
    "SpreadDescriptor",
    "SpreadSlot",
    "find_spread_for_page",
    "resolve_spreads",
]
