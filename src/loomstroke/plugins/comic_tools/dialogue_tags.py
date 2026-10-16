"""Display speaker registry tags without changing text colors."""

from loomstroke.core.japanese_text.speaker_styles import resolve_style

from .speaker_tag_widget import tag_icon


def update_dialogue_tags(tool, table, rows):
    service = getattr(tool.host_api.pages, "speaker_styles", None)
    registry = service.read() if service else {}
    for index, row in enumerate(rows):
        style = resolve_style(registry, row.speaker_id, row.speaker_name)
        table.item(index, 2).setIcon(tag_icon(style.get("color_tag", "") if style else ""))
