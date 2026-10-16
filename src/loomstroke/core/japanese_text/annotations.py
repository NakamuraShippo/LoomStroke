"""Ruby and explicit tate-chu-yoko ranges in Python string coordinates."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from .grapheme_clusters import grapheme_spans
from .metadata_values import finite_number

ANNOTATION_KEYS = ("ruby_runs", "tcy_runs", "emphasis_runs", "warichu_runs", "jidori_runs")


def annotation_selection(text: str, start: Any, end: Any) -> tuple[int, int] | None:
    """Clamp a nonempty selection and include complete graphemes at its edges."""
    return _selection_from_boundaries(len(text), start, end, _grapheme_boundaries(text))


def _grapheme_boundaries(text: str) -> tuple[int, ...]:
    return (0, *(end for _start, end in grapheme_spans(text)))


def _selection_from_boundaries(
    text_length: int, start: Any, end: Any, boundaries: tuple[int, ...]
) -> tuple[int, int] | None:
    if isinstance(start, bool) or isinstance(end, bool):
        return None
    try:
        left = max(0, min(text_length, int(start)))
        right = max(0, min(text_length, int(end)))
    except (TypeError, ValueError, OverflowError):
        return None
    if left >= right:
        return None
    return (
        boundaries[bisect_right(boundaries, left) - 1],
        boundaries[bisect_left(boundaries, right)],
    )


def normalize_annotations(raw: Any, text: str, *, kind: str) -> list[dict[str, Any]]:
    """Read valid, nonoverlapping ranges without mutating persisted input.

    A single grapheme parent is mono ruby; a longer parent is group ruby.
    Several explicit runs can express a jukugo reading without guessing it.
    Unknown per-run fields are retained. Invalid records are ignored for use,
    rather than rewriting the source metadata during load or rendering.
    """
    _check_kind(kind)
    if not isinstance(raw, (list, tuple)) or not raw:
        return []
    boundaries = _grapheme_boundaries(text)
    line_breaks = tuple(index for index, char in enumerate(text) if char in "\r\n\u2028\u2029")
    candidates = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        bounds = _selection_from_boundaries(
            len(text), entry.get("start"), entry.get("end"), boundaries
        )
        if bounds is None or bisect_left(line_breaks, bounds[0]) != bisect_left(
            line_breaks, bounds[1]
        ):
            continue
        normalized = deepcopy(dict(entry))
        normalized.update(start=bounds[0], end=bounds[1])
        if kind == "ruby_runs":
            ruby = entry.get("ruby")
            if (
                not isinstance(ruby, str)
                or not ruby.strip()
                or any(char in ruby for char in "\r\n\u2028\u2029")
            ):
                continue
            normalized["position"] = _choice(entry.get("position"), {"before", "after"}, "before")
            normalized["align"] = _choice(
                entry.get("align"),
                {"center", "start", "end", "space-around", "space-between"},
                "center",
            )
            normalized["size_ratio"] = finite_number(entry.get("size_ratio"), 0.5, 0.1, 2.0)
            normalized["gap_em"] = finite_number(entry.get("gap_em"), 0.05, 0.0, 2.0)
            normalized["overhang_em"] = finite_number(entry.get("overhang_em"), 0.5, 0.0, 2.0)
        elif kind == "warichu_runs":
            normalized["size_ratio"] = finite_number(entry.get("size_ratio"), 0.5, 0.1, 0.5)
            normalized["gap_em"] = finite_number(entry.get("gap_em"), 0.0, 0.0, 0.5)
        elif kind == "jidori_runs":
            normalized["width_em"] = finite_number(entry.get("width_em"), 4.0, 0.5, 100.0)
        elif kind == "emphasis_runs":
            normalized["mark"] = _choice(
                entry.get("mark"), {"auto", "sesame", "dot", "circle"}, "auto"
            )
            normalized["position"] = _choice(entry.get("position"), {"before", "after"}, "before")
            normalized["size_ratio"] = finite_number(entry.get("size_ratio"), 0.5, 0.1, 2.0)
            normalized["gap_em"] = finite_number(entry.get("gap_em"), 0.0, 0.0, 2.0)
        else:
            normalized["mode"] = "explicit"
        candidates.append(normalized)
    result: list[dict[str, Any]] = []
    for entry in sorted(candidates, key=lambda item: (item["start"], item["end"])):
        if not result or entry["start"] >= result[-1]["end"]:
            result.append(entry)
    return result


def replace_annotation_range(
    payload: Mapping[str, Any],
    kind: str,
    start: int,
    end: int,
    options: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Copy a payload and add/clear one selected annotation, keeping extensions.

    ``None`` clears overlapping runs. A mapping adds one run and replaces
    overlapping runs of the same kind. Empty tcy options select explicit mode.
    Invalid additions leave the payload untouched, including existing runs.
    """
    _check_kind(kind)
    result = deepcopy(dict(payload))
    error, bounds, additions = _prepare_annotation_change(payload, kind, start, end, options)
    if error is not None or bounds is None:
        return result
    raw_document = result.get("document")
    document = dict(raw_document) if isinstance(raw_document, Mapping) else {}
    text = document.get("text", result.get("content", ""))
    left, right = bounds
    entries = document.get(kind)
    # Preserve invalid/unknown records untouched; normalization is a read view.
    retained = []
    if isinstance(entries, (list, tuple)):
        for entry in entries:
            span = _raw_range(entry)
            if span is None or span[1] <= left or span[0] >= right:
                retained.append(entry)
            elif kind == "emphasis_runs":
                # Unlike a reading, emphasis on unselected characters remains
                # valid when the middle of a range is changed or cleared.
                if span[0] < left:
                    retained.append(dict(deepcopy(entry), end=left))
                if span[1] > right:
                    retained.append(dict(deepcopy(entry), start=right))
    document["text"] = text
    document[kind] = retained + additions
    result["document"] = document
    return result


def annotation_edit_error(
    payload: Mapping[str, Any], kind: str, start: int, end: int, options: Mapping[str, Any] | None
) -> str | None:
    """Return a stable reason for an invalid addition; clearing is always allowed.

    Ruby can contain an entire TCY group. A partial intersection or a ruby
    range inside only part of a TCY group has no supported layout, so new
    additions reject it without changing either annotation. Existing payloads
    are read unchanged; layout diagnostics handle old conflicting metadata.
    """
    _check_kind(kind)
    return _prepare_annotation_change(payload, kind, start, end, options)[0]


def _prepare_annotation_change(  # noqa: PLR0911 - validation failures
    payload, kind, start, end, options
):
    raw_document = payload.get("document")
    document = raw_document if isinstance(raw_document, Mapping) else {}
    text = document.get("text", payload.get("content", ""))
    if not isinstance(text, str):
        return (None if options is None else "annotation_invalid"), None, []
    bounds = annotation_selection(text, start, end)
    if options is None:
        return None, bounds, []
    if bounds is None or not isinstance(options, Mapping):
        return "annotation_invalid", None, []
    entries = document.get(kind)
    candidate = {}
    if isinstance(entries, (list, tuple)):
        candidate = next((dict(entry) for entry in entries if _raw_range(entry) == bounds), {})
    candidate.update(options)
    candidate.update(start=bounds[0], end=bounds[1])
    candidates = _ruby_candidates(candidate, text, bounds) if kind == "ruby_runs" else [candidate]
    additions = normalize_annotations(candidates, text, kind=kind)
    if not additions:
        return "annotation_invalid", None, []
    if _special_conflict(document, text, kind, bounds):
        return "annotation_cross_range", None, []
    other_kind = "tcy_runs" if kind == "ruby_runs" else "ruby_runs"
    others = (
        normalize_annotations(document.get(other_kind), text, kind=other_kind)
        if kind != "emphasis_runs"
        else []
    )
    for other in others:
        ruby, tcy = (additions[0], other) if kind == "ruby_runs" else (other, additions[0])
        overlaps = ruby["start"] < tcy["end"] and tcy["start"] < ruby["end"]
        contains = ruby["start"] <= tcy["start"] and tcy["end"] <= ruby["end"]
        if overlaps and not contains:
            return "annotation_cross_range", None, []
    return None, bounds, additions


def _special_conflict(document, text, kind, bounds):
    if kind == "emphasis_runs":
        return False
    special = ("warichu_runs", "jidori_runs")
    check = ANNOTATION_KEYS if kind in special else special
    return any(
        bounds[0] < other["end"] and bounds[1] > other["start"]
        for key in check
        if key not in {kind, "emphasis_runs"}
        for other in normalize_annotations(document.get(key), text, kind=key)
    )


def _ruby_candidates(candidate, text, bounds):
    if not candidate.get("per_character"):
        return [candidate]
    readings = str(candidate.get("ruby", "")).split("|")
    clusters = grapheme_spans(text[bounds[0] : bounds[1]])
    if len(readings) != len(clusters) or any(not reading.strip() for reading in readings):
        return []
    return [
        dict(
            candidate,
            start=bounds[0] + start,
            end=bounds[0] + end,
            ruby=reading.strip(),
            per_character=False,
        )
        for (start, end), reading in zip(clusters, readings, strict=True)
    ]


def map_annotation_range(
    kind: str, left: int, right: int, start: int, old_end: int, new_end: int
) -> tuple[int, int] | None:
    """Follow one edit; ruby is removed when its parent text is modified."""
    _check_kind(kind)
    delta = new_end - old_end
    if old_end <= left:
        return left + delta, right + delta
    if start >= right:
        return left, right
    if kind == "ruby_runs" or (start <= left and old_end >= right):
        return None
    # TCY retains surviving parent characters and includes interior insertions.
    new_left = left if left <= start else new_end
    new_right = right + delta if right >= old_end else new_end
    return (new_left, new_right) if new_left < new_right else None


def shift_annotation_ranges(
    raw: Any, *, kind: str, start: int, old_end: int, new_end: int, text: str
) -> Any:
    """Patch stored ranges on a detached document while retaining unknown fields."""
    _check_kind(kind)
    if not isinstance(raw, (list, tuple)):
        return raw
    if not raw:
        return []
    boundaries = _grapheme_boundaries(text)
    result = []
    for entry in raw:
        bounds = _raw_range(entry)
        if bounds is None:
            result.append(deepcopy(entry))
            continue
        mapped = map_annotation_range(kind, *bounds, start, old_end, new_end)
        if mapped is None:
            continue
        snapped = _selection_from_boundaries(len(text), *mapped, boundaries)
        if snapped is None:
            continue
        # A combining mark inserted at a boundary can alter the parent even
        # though the code-point edit sits outside its original range.
        if kind == "ruby_runs" and snapped != mapped:
            continue
        result.append(dict(deepcopy(entry), start=snapped[0], end=snapped[1]))
    return result


def _raw_range(entry: Any) -> tuple[int, int] | None:
    if not isinstance(entry, Mapping):
        return None
    start, end = entry.get("start"), entry.get("end")
    if isinstance(start, bool) or isinstance(end, bool):
        return None
    try:
        left, right = int(start), int(end)
    except (TypeError, ValueError, OverflowError):
        return None
    return (left, right) if left < right else None


def _check_kind(kind: str) -> None:
    if kind not in ANNOTATION_KEYS:
        raise ValueError(f"Unsupported text annotation: {kind}")


def _choice(value: Any, choices: set[str], default: str) -> str:
    return value if isinstance(value, str) and value in choices else default
