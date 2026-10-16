"""Translate detached page-part snapshots to a core preflight guard."""

from loomstroke.core.comic_panels.source_guard_command import PanelSourceGuardCommand


def source_guard(state, *, page_size=None):
    expected = {
        layer_id: (part.parent_id, part.texture_version, part.values)
        for layer_id, part in state.panel_parts.items()
    }
    return PanelSourceGuardCommand(
        expected, page_size=tuple(map(int, page_size)) if page_size else None
    )
