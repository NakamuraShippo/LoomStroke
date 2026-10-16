"""Explicit paragraph alignment on existing document paragraph records."""

from collections.abc import Mapping
from copy import deepcopy

from .line_breaker import JapaneseLineBreaker


def paragraph_style(payload, index):
    document = payload.get("document", {}) if isinstance(payload, Mapping) else {}
    records = document.get("paragraphs", []) if isinstance(document, Mapping) else []
    for record in records if isinstance(records, (list, tuple)) else []:
        if not isinstance(record, Mapping):
            continue
        try:
            start, end = int(record["start"]), int(record["end"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        overrides = record.get("style_overrides", {})
        if start <= index < end and isinstance(overrides, Mapping):
            direction = overrides.get("vertical_flow")
            extra = {"vertical_flow": direction} if direction in ("left", "right") else {}
            from .paragraph_columns import paragraph_column_count  # noqa: PLC0415

            count = paragraph_column_count(overrides.get("paragraph_columns"))
            if count:
                extra["paragraph_columns"] = count
            align = overrides.get("align")
            if isinstance(align, str) and align in {"left", "center", "right", "justify"}:
                return {"align": align, **_japanese(overrides), **extra}
            return {**_japanese(overrides), **extra}
    return {}


def has_paragraph_styles(payload):
    from ..vector_text_layout import TextLayoutCalculator as Layout  # noqa: PLC0415
    from .typography import typography_from_payload  # noqa: PLC0415

    if not isinstance(payload, Mapping):
        return False
    defaults = typography_from_payload(payload)
    from .paragraph_columns import has_paragraph_columns  # noqa: PLC0415

    if has_paragraph_columns(payload):
        return True
    document = payload.get("document", {}) if isinstance(payload, Mapping) else {}
    records = document.get("paragraphs", []) if isinstance(document, Mapping) else []
    return isinstance(records, (list, tuple)) and any(
        isinstance(record, Mapping)
        and isinstance(record.get("style_overrides"), Mapping)
        and (
            (
                bool(_japanese(record["style_overrides"]))
                and _japanese(record["style_overrides"])["typography"] != defaults
            )
            or (
                record["style_overrides"].get("vertical_flow") in ("left", "right")
                and record["style_overrides"]["vertical_flow"]
                != Layout.text_vertical_flow_from_payload(payload)
            )
            or (
                isinstance(record["style_overrides"].get("align"), str)
                and record["style_overrides"].get("align") in {"left", "center", "right", "justify"}
                and record["style_overrides"]["align"] != Layout.text_align_from_payload(payload)
            )
        )
        for record in records
    )


def apply_paragraph_alignment(payload, start, end, align):
    if not isinstance(align, str) or align not in {"left", "center", "right", "justify"}:
        return deepcopy(payload)
    return apply_paragraph_style(payload, start, end, {"align": align})


def _japanese(overrides):
    raw = overrides.get("japanese")
    if not isinstance(raw, Mapping):
        return {}
    from .typography import normalize_typography  # noqa: PLC0415

    return {"typography": normalize_typography(raw)}


def apply_paragraph_style(payload, start, end, changes):
    result = deepcopy(payload)
    document = result.setdefault("document", {})
    text = document.get("text", result.get("content", ""))
    if not isinstance(text, str) or not 0 <= start < end <= len(text):
        return result
    records = document.get("paragraphs", [])
    if not isinstance(records, (list, tuple)):
        records = []
    updated = []
    for left, _right, following in JapaneseLineBreaker.paragraphs(text):
        previous = next(
            (
                record
                for record in records
                if isinstance(record, Mapping)
                and isinstance(record.get("start"), int)
                and isinstance(record.get("end"), int)
                and record["start"] <= left < record["end"]
            ),
            {},
        )
        record = dict(previous, start=left, end=following)
        if start < following and end > left:
            existing = record.get("style_overrides")
            overrides = dict(existing) if isinstance(existing, Mapping) else {}
            overrides.update(deepcopy(changes))
            record["style_overrides"] = overrides
        updated.append(record)
    document["paragraphs"] = updated
    return result
