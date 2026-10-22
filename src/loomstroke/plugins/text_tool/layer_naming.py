"""Name committed text layers without replacing a user's custom label."""

import re

from loomstroke.core.commands_layer_rename import RenameLayerCommand
from loomstroke.core.japanese_text.grapheme_clusters import grapheme_spans

from .common import extract_text_content, extract_text_payload


def text_layer_name(content: str) -> str:
    text = " ".join(content.split())
    spans = grapheme_spans(text)
    return text[: spans[min(10, len(spans)) - 1][1]] if spans else ""


def sync_text_layer_name(tool, layer_id, object_id, content, *, previous_content=None):
    """Submit a rename within the existing text edit transaction, after commit.

    Public canvas views identify the first text object. Matching the old body
    prefix preserves custom labels without introducing persisted metadata.
    """
    if tool.host_api is None or layer_id is None:
        return
    canvas = tool.host_api.canvas
    getter = getattr(canvas, "get_vector_objects", None)
    get_layers = getattr(canvas, "get_layers", None)
    if not callable(getter) or not callable(get_layers):
        return
    first = next(
        (
            obj
            for lid, obj in getter(include_hidden=True)
            if lid == layer_id and extract_text_payload(obj) is not None
        ),
        None,
    )
    # A rejected text command must not change the layer's name either.
    if (
        first is None
        or first.id != object_id
        or extract_text_content(extract_text_payload(first)) != content
    ):
        return
    layer = next((ref for ref in get_layers() if ref.id == layer_id), None)
    desired = text_layer_name(content)
    if layer is None or not desired or layer.name == desired:
        return
    if layer.name != text_layer_name(previous_content or "") and not _is_default_name(layer.name):
        return
    tool._submit_command(RenameLayerCommand(layer_id, desired))


def _is_default_name(name):
    from loomstroke.ui.translations import translated_literal_domain  # noqa: PLC0415

    source = "Text Layer {index}"
    translated = translated_literal_domain("TextTool", source, use_fallback=True)
    return any(
        re.fullmatch(re.escape(template).replace(re.escape("{index}"), r"\d+"), name)
        for template in (source, translated)
    )
