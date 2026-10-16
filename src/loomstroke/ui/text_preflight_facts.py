"""Shared typography facts for manga export preflight, without changing text."""

from loomstroke.core.japanese_text.character_fonts import font_runs
from loomstroke.core.japanese_text.typography import has_typography
from loomstroke.core.japanese_text.typography_layout import build_japanese_layout


def character_font_facts(payload, family, size_pt, available):
    families, sizes, cursor = set(), [], 0
    default_used = False
    for start, end, values in font_runs(payload):
        default_used |= start > cursor
        selected_family = values.get("font_family", family)
        if selected_family:
            families.add(selected_family)
        sizes.append(values.get("font_size_pt", size_pt))
        cursor = max(cursor, end)
    text = payload.get("document", {}).get("text", payload.get("content", ""))
    if default_used or cursor < len(text) or not sizes:
        if family:
            families.add(family)
        sizes.append(size_pt)
    statuses = {name: available(name) for name in sorted(families)}
    missing = tuple(name for name, present in statuses.items() if present is False)
    status = False if missing else None if None in statuses.values() else True
    return min(sizes), status, missing, tuple(sorted(families))


def rich_text_overflow(payload):
    if not has_typography(payload):
        return None
    layout = build_japanese_layout(payload)
    return any(
        code.startswith(("line_overflow", "shape_overflow", "ink_overflow"))
        for code in layout.diagnostics
    )
