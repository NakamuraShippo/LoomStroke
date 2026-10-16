"""Character overrides on existing document runs, without replacing style references."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Mapping
from copy import deepcopy
from math import isfinite

from .annotations import _grapheme_boundaries, _selection_from_boundaries


class ColorRuns(list):
    """Nonoverlapping read view with logarithmic lookup, retaining list comparison."""

    def __init__(self, values=()):
        super().__init__(values)
        self.ends = [run[1] for run in self]
        self.starts = [run[0] for run in self]

    def overlapping(self, start, end):
        return self[bisect_right(self.ends, start) : bisect_left(self.starts, end)]


def valid_color(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        numbers = tuple(float(part) for part in value)
        if not all(isfinite(part) for part in numbers):
            return None
        return tuple(max(0, min(255, round(part))) for part in numbers)
    except (TypeError, ValueError, OverflowError):
        return None


def color_runs(payload):
    if not isinstance(payload, Mapping):
        return []
    document = payload.get("document", {})
    if not isinstance(document, Mapping):
        return []
    text = document.get("text", payload.get("content", ""))
    raw = document.get("runs", [])
    if not isinstance(text, str) or not isinstance(raw, (list, tuple)):
        return []
    boundaries = _grapheme_boundaries(text)
    result = []
    for run in raw:
        if not isinstance(run, Mapping):
            continue
        color = valid_color(run.get("color_rgba"))
        bounds = _selection_from_boundaries(len(text), run.get("start"), run.get("end"), boundaries)
        if color is not None and bounds is not None:
            result.append((*bounds, color))
    ordered = []
    for run in sorted(result, key=lambda run: (run[0], run[1])):
        if not ordered or ordered[-1][1] <= run[0]:
            ordered.append(run)
    return ColorRuns(ordered)


def apply_character_color(payload, start, end, color):
    rgba = valid_color(color)
    return apply_character_override(payload, start, end, "color_rgba", list(rgba) if rgba else None)


def apply_character_override(payload, start, end, key, value):
    """Split selected runs, retain their attributes, and cover previously unstyled gaps."""
    result = deepcopy(payload)
    document = result.setdefault("document", {})
    text = document.get("text", result.get("content", ""))
    boundaries = _grapheme_boundaries(text)
    bounds = _selection_from_boundaries(len(text), start, end, boundaries)
    if bounds is None or value is None:
        return result
    left, right = bounds
    raw = document.get("runs", [])
    runs, covered = [], []
    for run in raw if isinstance(raw, (list, tuple)) else []:
        span = (
            _selection_from_boundaries(len(text), run.get("start"), run.get("end"), boundaries)
            if isinstance(run, Mapping)
            else None
        )
        if span is None or span[1] <= left or span[0] >= right:
            runs.append(run)
            continue
        a, b = span
        if a < left:
            runs.append(dict(run, start=a, end=left))
        lo, hi = max(a, left), min(b, right)
        runs.append({**run, "start": lo, "end": hi, key: deepcopy(value)})
        covered.append((lo, hi))
        if b > right:
            runs.append(dict(run, start=right, end=b))
    cursor = left
    for lo, hi in [*sorted(covered), (right, right)]:
        if cursor < lo:
            runs.append({"start": cursor, "end": lo, "style_ref": "default", key: deepcopy(value)})
        cursor = max(cursor, hi)
    document["runs"] = runs
    return result


def color_at(runs, index, default):
    if isinstance(runs, ColorRuns):
        candidate = bisect_right(runs.ends, index)
        return (
            runs[candidate][2] if candidate < len(runs) and runs[candidate][0] <= index else default
        )
    return next((color for start, end, color in runs if start <= index < end), default)
