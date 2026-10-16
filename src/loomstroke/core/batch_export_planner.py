"""Batch-export scope resolution (CT-10 Phase X1).

Turns a user-facing scope choice (All / Range / Selected / Current / Odd /
Even / Role) into a frozen tuple of page ids in **logical reading order**.
The rules that carry the design (spec 10 §6):

- Ranges and odd/even use the 1-based *logical* page number — never the
  folio string and never the physical left/right side.
- Right binding never reverses the output order (the shared CT-06
  provider owns that invariant).
- An empty resolution is returned as ``()`` — the caller must refuse to
  start, not export nothing.

Pure and Qt-free like the CT-06 resolvers; the GUI (Phase X2, once the
CT-09 export coordinator exists) and the MCP adapter (Phase X5) are both
expected to call this instead of re-deriving page sets.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Any, Literal

from .book_export_order import export_page_ids

__all__ = ["ExportScopeError", "resolve_scope_page_ids"]

ScopeKind = Literal["all", "range", "selected", "current", "odd", "even", "role"]


class ExportScopeError(ValueError):
    """A scope that cannot be resolved (bad range text, unknown scope)."""


def resolve_scope_page_ids(
    page_ids: Sequence[str],
    *,
    scope: ScopeKind,
    range_text: str = "",
    selected_ids: Collection[str] = (),
    current_page_id: str | None = None,
    include_roles: Collection[str] | None = None,
    exclude_roles: Collection[str] | None = None,
    page_metadata_by_id: Mapping[str, Mapping[str, Any] | None] | None = None,
) -> tuple[str, ...]:
    """Freeze the scope into logical-order page ids."""
    ordered = tuple(page_ids)
    if scope == "range":
        numbers = _parse_range(range_text, len(ordered))
        return tuple(ordered[number - 1] for number in numbers)
    if scope == "role":
        return export_page_ids(
            ordered,
            page_metadata_by_id,
            include_roles=include_roles,
            exclude_roles=exclude_roles,
        )
    wanted = set(selected_ids)
    simple = {
        "all": lambda: ordered,
        "current": lambda: (current_page_id,) if current_page_id in ordered else (),
        "selected": lambda: tuple(page_id for page_id in ordered if page_id in wanted),
        "odd": lambda: ordered[::2],
        "even": lambda: ordered[1::2],
    }
    if scope not in simple:
        raise ExportScopeError(f"unknown export scope: {scope!r}")
    return simple[scope]()


def _parse_range(text: str, page_count: int) -> tuple[int, ...]:
    """1-based logical numbers from "1-4,7"; ordered, deduplicated.

    Out-of-range parts fail loudly instead of being clamped: a typo like
    "1-40" on a 4-page book means the user is not exporting what they
    think they are.
    """
    numbers: list[int] = []
    seen: set[int] = set()
    for raw_part in text.split(","):
        part = raw_part.strip()
        if not part:
            raise ExportScopeError("empty range part")
        start_text, dash, end_text = part.partition("-")
        try:
            start = int(start_text)
            end = int(end_text) if dash else start
        except ValueError as error:
            raise ExportScopeError(f"invalid range part: {part!r}") from error
        if start > end:
            raise ExportScopeError(f"reversed range part: {part!r}")
        if start < 1 or end > page_count:
            raise ExportScopeError(f"range part out of bounds: {part!r} (1-{page_count})")
        for number in range(start, end + 1):
            if number not in seen:
                seen.add(number)
                numbers.append(number)
    return tuple(sorted(numbers))
