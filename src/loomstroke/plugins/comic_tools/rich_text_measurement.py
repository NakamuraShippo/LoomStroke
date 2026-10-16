"""Measure advanced balloon text with the same glyph layout as the canvas."""

from dataclasses import fields

from loomstroke.core.japanese_text.typography import has_typography
from loomstroke.core.japanese_text.typography_layout import build_japanese_layout
from loomstroke.plugins.text_tool.common import TextToolSettings, ensure_text_vector_object
from loomstroke.plugins.text_tool.editing_metadata import replace_edited_text


def rich_text_block(previous, text, props=None):
    if previous is None or not has_typography(previous.meta.get("text_tool", {})):
        return None
    edited = replace_edited_text(previous, text)
    if props is not None:
        settings = TextToolSettings(
            **{
                field.name: getattr(props, field.name)
                for field in fields(TextToolSettings)
                if hasattr(props, field.name)
            }
        )
        edited = ensure_text_vector_object(
            object_id=previous.id,
            content=text,
            rect=(0, 0, 100, 100),
            settings=settings,
            word_wrap=settings.word_wrap,
            previous_meta=edited.meta,
        )
    payload = edited.meta["text_tool"]
    from .text_frames import TextBlockSize  # noqa: PLC0415

    initial = build_japanese_layout(payload)
    limit = max(28.0, initial.em * 14)
    vertical = bool(payload.get("vertical", False))
    rect = (0, 0, 1000000.0, limit) if vertical else (0, 0, limit, 1000000.0)
    layout = build_japanese_layout(payload, rect=rect)
    bounds = layout.logical_bounds.united(layout.ink_bounds)
    return TextBlockSize(bounds.width(), bounds.height(), len(layout.lines))
