"""Two-line inline annotations and fixed-width character distribution."""

from dataclasses import replace

from PySide6.QtCore import QRectF
from PySide6.QtGui import QTransform

from .kinsoku_policy import KinsokuPolicy


def warichu_split(children, policy=None):
    if len(children) < 2:
        return len(children)
    total = sum(child.advance + child.tracking for child in children)
    cursor, best, error = 0.0, len(children), float("inf")
    policy = policy or KinsokuPolicy.standard()
    for index, child in enumerate(children[:-1], 1):
        cursor += child.advance + child.tracking
        if not policy.can_end_line(child.text[-1]) or not policy.can_start_line(
            children[index].text[0]
        ):
            continue
        if abs(total - 2 * cursor) < error:
            best, error = index, abs(total - 2 * cursor)
    return best


def special_advance(kind, children, options, em):
    if kind == "jidori":
        return float(options["width_em"]) * em
    split = options.get("_split", warichu_split(children))
    widths = [
        sum(child.advance + child.tracking for child in row)
        for row in (children[:split], children[split:])
    ]
    return max(widths, default=0.0) * float(options["size_ratio"])


def place_special(placer, atom, cell, line_index):
    vertical = placer.vertical
    extent = cell.height() if vertical else cell.width()
    if atom.kind == "jidori":
        rows = (atom.children,)
        ratio = 1.0
    else:
        split = atom.special.get("_split", warichu_split(atom.children))
        rows = (atom.children[:split], atom.children[split:])
        ratio = float(atom.special["size_ratio"])
    glyphs, carets = [], {}
    for row_index, children in enumerate(rows):
        if not children:
            continue
        natural = sum(child.advance + child.tracking for child in children)
        scale = min(ratio, extent / max(0.01, natural))
        extra = max(0.0, extent / scale - natural) / max(1, len(children) - 1)
        cursor = (extent / scale - natural) * 0.5 if len(children) == 1 else 0.0
        cross = 0.0
        if atom.kind == "warichu":
            gap = float(atom.special["gap_em"]) * placer.em
            cross = row_index * (placer.em * scale + gap)
            if vertical:
                cross = cell.width() - placer.em * scale - cross
        for child in children:
            local = (
                QRectF(0, cursor, placer.em, child.advance)
                if vertical
                else QRectF(cursor, 0, child.advance, placer.em)
            )
            placed, insertion = placer.place(child, local, line_index)
            transform = (
                QTransform()
                .translate(
                    cell.left() + (cross if vertical else 0),
                    cell.top() + (0 if vertical else cross),
                )
                .scale(scale, scale)
            )
            glyphs.extend(
                replace(
                    g,
                    transform=g.transform * transform,
                    cell=transform.mapRect(g.cell),
                    group_range=(atom.start, atom.end),
                    group_cell=QRectF(cell),
                )
                for g in placed
            )
            carets.update({index: transform.mapRect(rect) for index, rect in insertion.items()})
            cursor += child.advance + child.tracking + extra
    return glyphs, carets
