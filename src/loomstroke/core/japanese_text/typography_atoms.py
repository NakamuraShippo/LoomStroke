"""Grapheme atoms, annotation grouping and Japanese punctuation spacing classes."""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import replace
from itertools import groupby
from typing import Any

from PySide6.QtGui import QFont, QFontMetricsF

from ..vector_text_layout import TextLayoutCalculator as Layout
from .annotations import normalize_annotations
from .character_fonts import CharacterFonts, font_em
from .character_geometry import GeometryCursor, adjusted_advance
from .custom_rules import custom_policy
from .grapheme_clusters import grapheme_spans
from .special_layout import special_advance, warichu_split
from .typography_annotations_layout import ruby_required_advance
from .typography_layout_types import LayoutAtom
from .typography_range_values import RangeValueCursor
from .typography_shaping import run_cursor_offsets

OPENING = frozenset("（［｛〔〈《「『【〘〖〝‘“｟«")  # noqa: RUF001
CLOSING = frozenset("）］｝〕〉》」』】〙〗〟’”｠»")  # noqa: RUF001
STOPS = frozenset("、。，．")  # noqa: RUF001
MIDDLE = frozenset("・：；")  # noqa: RUF001


def category(text: str) -> str:
    char = text[0] if text else ""
    for characters, label in (
        (OPENING, "opening"),
        (CLOSING, "closing"),
        (STOPS, "stop"),
        (MIDDLE, "middle"),
    ):
        if char in characters:
            return label
    if char and char.isspace():
        return "space"
    if char and unicodedata.east_asian_width(char) in {"W", "F"}:
        return "japanese"
    if char and (char.isascii() or "LATIN" in unicodedata.name(char, "")):
        return "latin"
    return "other"


def build_atoms(  # noqa: PLR0912 - atom and annotation categories share one pass
    text: str,
    payload: Mapping[str, Any],
    *,
    font: QFont,
    em: float,
    vertical: bool,
    options: Mapping[str, Any],
    diagnostics: list[str] | None = None,
    options_at=None,
) -> list[LayoutAtom]:
    atoms = _base_atoms(text, payload, font, em, vertical, options, options_at)

    document = payload.get("document", {})
    if not isinstance(document, Mapping):
        document = {}
    tcy = normalize_annotations(document.get("tcy_runs"), text, kind="tcy_runs")
    for special in ("warichu", "jidori"):
        runs = normalize_annotations(document.get(special + "_runs"), text, kind=special + "_runs")
        atoms = _group_ranges(
            atoms, runs, special, em=em, options_at=options_at or (lambda _index: options)
        )
    ruby = normalize_annotations(document.get("ruby_runs"), text, kind="ruby_runs")
    tcy_index = 0
    for reading in ruby:
        while tcy_index < len(tcy) and tcy[tcy_index]["end"] <= reading["start"]:
            tcy_index += 1
        cursor = tcy_index
        while cursor < len(tcy) and tcy[cursor]["start"] < reading["end"]:
            if not (
                reading["start"] <= tcy[cursor]["start"] and reading["end"] >= tcy[cursor]["end"]
            ):
                if diagnostics is not None:
                    diagnostics.append("annotation_cross_range")
                break
            cursor += 1
    if vertical:
        atoms = _group_ranges(atoms, tcy, "tcy", em=em)
    atoms = _group_ranges(atoms, ruby, "ruby", em=em)
    atoms = _paragraph_latin_runs(atoms, font, vertical, options, options_at)
    for index, atom in enumerate(atoms):
        if atom.ruby is not None:
            ruby_font = atom.font or font
            ruby_em = font_em(atom.font) if atom.font is not None else em
            before = index > 0 and _ruby_overhang_neighbor(atoms[index - 1])
            after = index + 1 < len(atoms) and _ruby_overhang_neighbor(atoms[index + 1])
            reserved = ruby_required_advance(
                atom.ruby,
                font=ruby_font,
                em=ruby_em,
                vertical=vertical,
                base_advance=atom.advance,
                allow_before=before,
                allow_after=after,
            )
            at_start = ruby_required_advance(
                atom.ruby,
                font=ruby_font,
                em=ruby_em,
                vertical=vertical,
                base_advance=atom.advance,
                allow_before=False,
                allow_after=after,
            )
            at_end = ruby_required_advance(
                atom.ruby,
                font=ruby_font,
                em=ruby_em,
                vertical=vertical,
                base_advance=atom.advance,
                allow_before=before,
                allow_after=False,
            )
            atoms[index] = replace(
                atom, advance=reserved, line_start_advance=at_start, line_end_advance=at_end
            )
    return atoms


def _base_atoms(text, payload, font, em, vertical, options, options_at):
    metrics = QFontMetricsF(font)
    tracking = Layout.text_tracking_from_payload(payload, Layout.text_style_defaults(payload))
    tracking_runs = Layout.text_range_runs_from_payload(payload, "tracking_runs")
    tracking_values = RangeValueCursor(tracking_runs, "tracking", tracking)
    atoms = []
    fonts = CharacterFonts(payload, font)
    geometries = GeometryCursor(payload)
    for start, end in grapheme_spans(text):
        if options_at is not None:
            options = options_at(start)
        selected_font = fonts.at(start)
        metrics = QFontMetricsF(selected_font)
        selected_em = font_em(selected_font) if fonts.runs else em
        value = text[start:end]
        kind = "newline" if value in {"\r", "\n", "\r\n"} else "base"
        group = category(value)
        if vertical:
            advance = selected_em if options["em_advance"] else metrics.lineSpacing()
        elif options["em_advance"] and group in {
            "japanese",
            "opening",
            "closing",
            "stop",
            "middle",
        }:
            advance = selected_em
        else:
            advance = metrics.horizontalAdvance(value)
        amount = tracking_values.at(start)
        geometry = geometries.at(start)
        advance = adjusted_advance(advance, geometry, selected_em, vertical)
        atoms.append(
            LayoutAtom(
                value,
                start,
                end,
                advance,
                kind,
                group,
                amount,
                font=selected_font if fonts.runs else None,
                em=selected_em,
                geometry=geometry,
            )
        )

    return atoms


def _ruby_overhang_neighbor(atom: LayoutAtom) -> bool:
    return atom.ruby is None and all(
        "HIRAGANA" in unicodedata.name(char, "") or "KATAKANA" in unicodedata.name(char, "")
        for char in atom.text
    )


def _paragraph_latin_runs(atoms, font, vertical, options, options_at):
    grouped = []
    for _key, iterator in groupby(
        atoms, key=lambda atom: id(options_at(atom.start)) if options_at else 0
    ):
        group_atoms = list(iterator)
        local = options_at(group_atoms[0].start) if options_at else options
        if local.get("enabled") and (not vertical or local["latin_orientation"] == "sideways"):
            group_atoms = _latin_runs(
                group_atoms, font, char_wrap=local.get("_wrap_mode") == "char"
            )
        grouped.extend(group_atoms)
    return grouped


def _group_ranges(
    atoms: list[LayoutAtom], runs: list[dict[str, Any]], kind: str, *, em: float, options_at=None
) -> list[LayoutAtom]:
    result = []
    cursor = 0
    for run in runs:
        start, end = int(run["start"]), int(run["end"])
        while cursor < len(atoms) and atoms[cursor].end <= start:
            result.append(atoms[cursor])
            cursor += 1
        stop = cursor
        while stop < len(atoms) and atoms[stop].end <= end:
            stop += 1
        children = tuple(atoms[cursor:stop])
        # Overlapping TCY/ruby boundaries cannot split an already atomic TCY.
        if not children or children[0].start != start or children[-1].end != end:
            continue
        group_em = max((a.em for a in children), default=em) or em
        advance = group_em if kind == "tcy" else sum(a.advance + a.tracking for a in children)
        special_options = run
        if kind in {"warichu", "jidori"}:
            if kind == "warichu" and options_at is not None:
                special_options = dict(
                    run, _split=warichu_split(children, custom_policy(options_at(start)))
                )
            advance = special_advance(kind, children, special_options, group_em)
        result.append(
            LayoutAtom(
                "".join(a.text for a in children),
                start,
                end,
                advance,
                kind,
                "japanese",
                children=children,
                ruby=run if kind == "ruby" else None,
                font=children[0].font,
                em=group_em,
                special=special_options if kind in {"warichu", "jidori"} else {},
            )
        )
        cursor = stop
    result.extend(atoms[cursor:])
    return result


def _latin_runs(atoms: list[LayoutAtom], font: QFont, *, char_wrap: bool) -> list[LayoutAtom]:
    result = []
    cursor = 0
    while cursor < len(atoms):
        atom = atoms[cursor]
        if atom.kind != "base" or atom.category != "latin" or atom.geometry:
            result.append(atom)
            cursor += 1
            continue
        end = cursor + 1
        while (
            end < len(atoms)
            and atoms[end].kind == "base"
            and atoms[end].category == "latin"
            and atoms[end].tracking == atom.tracking
            and atoms[end].font == atom.font
            and not atoms[end].geometry
        ):
            end += 1
        children = tuple(atoms[cursor:end])
        value = "".join(a.text for a in children)
        run_font = QFont(atom.font or font)
        run_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, atom.tracking)
        if char_wrap:
            offsets = run_cursor_offsets(value, run_font)
            for child in children:
                advance = offsets[child.end - atom.start] - offsets[child.start - atom.start]
                result.append(
                    replace(child, advance=advance, tracking=0.0, kind="latin", children=(child,))
                )
            cursor = end
            continue
        result.append(
            LayoutAtom(
                value,
                atom.start,
                children[-1].end,
                QFontMetricsF(run_font).horizontalAdvance(value),
                "latin",
                "latin",
                children=children,
                font=atom.font,
                em=atom.em,
            )
        )
        cursor = end
    return result
