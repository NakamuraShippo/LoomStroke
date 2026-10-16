"""Allocate text to each linked frame using its contour and paragraph bands."""

from dataclasses import replace

from PySide6.QtCore import QRectF

from .frame_flow import flow_values, payload_flow
from .linked_story import slice_payload
from .paragraph_columns import has_paragraph_columns
from .paragraph_styles import apply_paragraph_style, paragraph_style
from .story_metadata import validated_story
from .story_shapes import configure_frame, member_shape
from .typography import typography_from_payload


def compose_extended_story(payload, text, font, frame, compose):
    story = validated_story(payload.get("linked_story"))
    if story is None or not (
        has_paragraph_columns(payload)
        or payload.get("shape_frame")
        or any(entry.get("shape_frame") for entry in story["frames"])
        or payload_flow(payload)["balance"]
        or any(flow_values(entry.get("flow"))["balance"] for entry in story["frames"])
    ):
        return None
    consumed = 0
    for index, entry in enumerate(story["frames"]):
        own = entry["id"] == story["member"]
        region = frame if own else QRectF(*entry["rect"])
        options = payload_flow(payload) if own else flow_values(entry.get("flow"))
        local = _local(payload, text, consumed, len(text), options)
        if not own:
            configure_frame(local, entry)
        shape = member_shape(payload, entry, region, own)
        local["layout"]["text_flow"]["balance"] = False
        trial = compose(local, font=font, rect=region, shape_path=shape)
        count = len(text) - consumed
        if index + 1 < len(story["frames"]):
            count = _fitting_prefix(trial, region, text[consumed:])
        if own:
            local = _local(payload, text, consumed, consumed + count, options)
            result = compose(local, font=font, rect=region, shape_path=shape)
            if count == 0 and consumed < len(text):
                result = replace(
                    result,
                    lines=(),
                    glyphs=(),
                    carets={},
                    ink_bounds=QRectF(),
                    logical_bounds=QRectF(),
                )
            return _original_indices(result, consumed)
        consumed += count
    return None


def _local(payload, text, start, end, options):
    local = slice_payload(payload, start, end)
    local.pop("linked_story", None)
    # Slicing removes linked_story, but member layout must retain the shared composer.
    local["_composing_story_member"] = True
    local["content"] = local["document"]["text"] = text[start:end]
    local.setdefault("layout", {})["text_flow"] = dict(options)
    if start and text[start - 1] not in "\r\n" and end > start:
        typography = paragraph_style(payload, start).get(
            "typography", typography_from_payload(payload)
        )
        local = apply_paragraph_style(
            local, 0, 1, {"japanese": {**typography, "first_line_indent_em": 0}}
        )
    return local


def _fitting_prefix(layout, frame, text):
    cells = {}
    for glyph in layout.glyphs:
        if glyph.role == "base":
            cells[glyph.line_index] = cells.get(glyph.line_index, QRectF()).united(glyph.cell)
    end = 0
    for index, line in enumerate(layout.lines):
        bounds = cells.get(index)
        if bounds is not None and not frame.adjusted(-0.001, -0.001, 0.001, 0.001).contains(bounds):
            break
        end = max(end, line.next_start)
    while end < len(text) and text[end] in "\r\n":
        end += 1
    for diagnostic in layout.diagnostics:
        if diagnostic.startswith("shape_overflow:"):
            end = min(end, int(diagnostic.split(":")[1]))
    return min(end, len(text))


def _original_indices(result, offset):
    return replace(
        result,
        lines=tuple(
            replace(line, start=line.start + offset, next_start=line.next_start + offset)
            for line in result.lines
        ),
        glyphs=tuple(
            replace(
                g,
                start=g.start + offset,
                end=g.end + offset,
                group_range=(
                    (g.group_range[0] + offset, g.group_range[1] + offset)
                    if g.group_range
                    else None
                ),
            )
            for g in result.glyphs
        ),
        carets={position + offset: rect for position, rect in result.carets.items()},
        diagnostics=tuple(
            (
                f"shape_overflow:{int(item.split(':')[1]) + offset}"
                if item.startswith("shape_overflow:")
                else item
            )
            for item in result.diagnostics
        ),
    )
