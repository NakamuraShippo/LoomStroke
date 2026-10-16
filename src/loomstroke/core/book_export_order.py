"""Export page order for comic books (CT-06 Phase B5).

Every multi-page output — batch PNG, and later PDF and webtoon — must pick
its pages the same way, or "export the book" means something different per
exporter.  This module is that one shared answer.

Two rules, both from the spec:

**The order is the logical page order, always.**  Binding direction decides
where a page sits *inside a spread*, never the sequence the book is read
or exported in (§4 explicitly forbids reversing the stored order for right
binding).  So the provider filters; it never reorders.

**Roles select, empty slots do not exist here.**  A cover or colophon is
excluded from a body-only export by its role in the page metadata.  The
virtual empty slots the spread view shows are display artifacts and were
never pages, so there is nothing to exclude (§5).

Pure and Qt-free, like the rest of the book-layout family.  Role resolution
goes through :class:`BookLayoutService`, so malformed metadata degrades to
``body`` here exactly as it does everywhere else — a page with a broken
namespace is exported with the body pages rather than silently dropped.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Any

from .book_layout_service import BookLayoutService

__all__ = ["export_page_ids"]


def export_page_ids(
    page_ids: Sequence[str],
    page_metadata_by_id: Mapping[str, Mapping[str, Any] | None] | None = None,
    *,
    include_roles: Collection[str] | None = None,
    exclude_roles: Collection[str] | None = None,
) -> tuple[str, ...]:
    """Pages to export, in logical order.

    Args:
        page_ids: the document's pages in logical order
            (``PageService.list_pages()`` order).
        page_metadata_by_id: each page's metadata root; missing or ``None``
            entries resolve to the default attributes (role ``body``).
        include_roles: when given, only pages whose role is in this set
            survive.  ``None`` means every role.
        exclude_roles: roles to drop.  Applied after ``include_roles``, so
            listing a role in both excludes it — the explicit "do not
            export" wins over the broad "these are wanted".
    """
    metadata_by_id = page_metadata_by_id or {}
    attributes = BookLayoutService().resolve_page_attributes_by_id(
        {page_id: metadata_by_id.get(page_id) for page_id in page_ids}
    )
    include = None if include_roles is None else {str(role) for role in include_roles}
    exclude = set() if exclude_roles is None else {str(role) for role in exclude_roles}

    selected: list[str] = []
    for page_id in page_ids:
        role = attributes[page_id].role
        if include is not None and role not in include:
            continue
        if role in exclude:
            continue
        selected.append(page_id)
    return tuple(selected)
