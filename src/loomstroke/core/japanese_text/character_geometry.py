"""Character-level scale, baseline and side bearings before GPU composition."""

from collections.abc import Mapping
from dataclasses import replace

from PySide6.QtCore import QRectF
from PySide6.QtGui import QTransform

from .metadata_values import finite_number

GEOMETRY_LIMITS = {
    "horizontal_scale": (1.0, 0.1, 4.0),
    "vertical_scale": (1.0, 0.1, 4.0),
    "baseline_shift_em": (0.0, -4.0, 4.0),
    "before_space_em": (0.0, 0.0, 4.0),
    "after_space_em": (0.0, 0.0, 4.0),
}


def geometry_values(raw):
    source = raw if isinstance(raw, Mapping) else {}
    return {key: finite_number(source.get(key), *limits) for key, limits in GEOMETRY_LIMITS.items()}


def geometry_runs(payload):
    document = payload.get("document", {}) if isinstance(payload, Mapping) else {}
    records = document.get("runs", []) if isinstance(document, Mapping) else []
    result = []
    defaults = geometry_values({})
    for run in records if isinstance(records, (list, tuple)) else []:
        if not isinstance(run, Mapping):
            continue
        start, end = run.get("start"), run.get("end")
        values = geometry_values(run)
        if (
            isinstance(start, int)
            and isinstance(end, int)
            and 0 <= start < end
            and values != defaults
        ):
            result.append((start, end, values))
    return sorted(result)


class GeometryCursor:
    def __init__(self, payload):
        self.runs = geometry_runs(payload)
        self.index = 0

    def at(self, index):
        while self.index < len(self.runs) and self.runs[self.index][1] <= index:
            self.index += 1
        if self.index < len(self.runs) and self.runs[self.index][0] <= index:
            return self.runs[self.index][2]
        return {}


def adjusted_advance(advance, values, em, vertical):
    scale = values.get("vertical_scale" if vertical else "horizontal_scale", 1.0)
    return advance * scale + em * (
        values.get("before_space_em", 0) + values.get("after_space_em", 0)
    )


def place_geometry(placer, atom, cell, line_index):
    """Apply a physical-axis transform to glyphs and caret/selection cells together."""
    values = geometry_values(atom.geometry)
    sx, sy = values["horizontal_scale"], values["vertical_scale"]
    em = atom.em or placer.em
    before, after = values["before_space_em"] * em, values["after_space_em"] * em
    width, height = cell.width(), cell.height()
    if placer.vertical:
        height = max(0.01, height - before - after) / sy
    else:
        width = max(0.01, width - before - after) / sx
    local = QRectF(0, 0, width, height)
    clean = replace(atom, geometry={})
    glyphs, carets = placer.place(clean, local, line_index)
    shift = values["baseline_shift_em"] * em
    x = cell.left() + (shift if placer.vertical else before)
    y = cell.top() + (before if placer.vertical else -shift)
    transform = QTransform().translate(x, y).scale(sx, sy)
    placed = [
        replace(
            g,
            transform=g.transform * transform,
            cell=transform.mapRect(g.cell),
            group_cell=transform.mapRect(g.group_cell) if g.group_cell is not None else None,
        )
        for g in glyphs
    ]
    return placed, {i: transform.mapRect(rect) for i, rect in carets.items()}
