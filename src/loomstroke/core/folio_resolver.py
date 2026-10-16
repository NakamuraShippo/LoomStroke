"""Pure folio (page number) resolver for Comic Tools (CT-06 spec §7).

Derives the display folio for every page from the logical page order and
the resolved book metadata.  Two rules carry the whole design:

- **Every real page consumes a number** (2026-08-02, user decision).
  ``folio_mode`` and ``folio_hidden_roles`` only control *display*, so
  hiding the cover never renumbers the body; shifting the count is what
  ``BookLayout.folio_start`` is for.  This matches the doujinshi print
  convention where the cover is page 1.
- The resolver never reorders anything: numbers follow the logical order
  exactly, and binding plays no part (spec §4).

Display resolution per page:

- ``folio_mode="hidden"``  -> ``None``
- ``folio_mode="custom"``  -> ``custom_folio`` verbatim (user string, no
  prefix; ``None`` when the custom text is unset)
- ``folio_mode="auto"``    -> ``None`` if the page's role is in
  ``BookLayout.folio_hidden_roles``, else ``folio_prefix + number``

No Qt, no I/O, no service dependencies -- callers resolve metadata with
``BookLayoutService`` first, exactly like ``resolve_spreads()``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .book_spread_layout import BookLayout, PageBookAttributes

__all__ = ["resolve_folios"]


def resolve_folios(
    page_ids: Sequence[str],
    layout: BookLayout,
    attributes_by_id: Mapping[str, PageBookAttributes] | None = None,
) -> dict[str, str | None]:
    """Map every page id to its display folio (``None`` = show nothing)."""
    attributes_by_id = attributes_by_id or {}
    default_attributes = PageBookAttributes()
    folios: dict[str, str | None] = {}
    for offset, page_id in enumerate(page_ids):
        attributes = attributes_by_id.get(page_id, default_attributes)
        folios[page_id] = _display_folio(layout.folio_start + offset, layout, attributes)
    return folios


def _display_folio(number: int, layout: BookLayout, attributes: PageBookAttributes) -> str | None:
    if attributes.folio_mode == "hidden":
        return None
    if attributes.folio_mode == "custom":
        return attributes.custom_folio
    if attributes.role in layout.folio_hidden_roles:
        return None
    return f"{layout.folio_prefix}{number}"
