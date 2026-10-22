"""Bounded forward spacing of glyph groups using rotated ink boxes and a grid."""

import math
from bisect import bisect_right
from collections import defaultdict
from functools import lru_cache

from PySide6.QtGui import QFont, QFontMetricsF, QPainterPath


@lru_cache(maxsize=1024)
def _ink_box(font, text):
    path = QPainterPath()
    path.addRect(QFontMetricsF(font).tightBoundingRect(text))
    return path


class _InkGrid:
    def __init__(self, size):
        self.size = size
        self.tiles = defaultdict(list)
        self.paths = []

    def _keys(self, bounds):
        left, right = math.floor(bounds.left() / self.size), math.floor(bounds.right() / self.size)
        top, bottom = math.floor(bounds.top() / self.size), math.floor(bounds.bottom() / self.size)
        if (right - left + 1) * (bottom - top + 1) > 256:
            return None
        return [(x, y) for x in range(left, right + 1) for y in range(top, bottom + 1)]

    def collides(self, paths):
        for path in paths:
            keys = self._keys(path.boundingRect())
            if keys is None:
                return True
            nearby = set()
            for key in keys:
                nearby.update(self.tiles.get(key, ()))
                if len(nearby) > 128:
                    return True
            if any(path.intersects(self.paths[index]) for index in nearby):
                return True
        return False

    def add(self, paths):
        for path in paths:
            index = len(self.paths)
            self.paths.append(path)
            for key in self._keys(path.boundingRect()) or ():
                self.tiles[key].append(index)


def _groups(glyphs):
    # A ruby/annotation range stays with all its parents. Intentional overlaps
    # within a shaped group are left alone; compare only different groups.
    groups = []
    for glyph in sorted(glyphs, key=lambda glyph: glyph.start):
        start, end = glyph.group_range or (glyph.start, glyph.end)
        if groups and start < groups[-1][1]:
            groups[-1][1] = max(end, groups[-1][1])
            groups[-1][2].append(glyph)
        else:
            groups.append([start, end, [glyph]])
    return groups


def collision_spacing(track, straight, baseline, distance):
    """Return cumulative distance additions and the first unplaceable index.

    Never reorder or shrink the body. If the remaining contour or a bounded
    search cannot hold a group, leave that suffix as editable overflow.
    """
    grid = _InkGrid(max(8.0, straight.em * 2))
    offsets, lag, probes = {}, 0.0, 0
    space = track.interval_length
    for start, _end, glyphs in _groups(straight.glyphs):
        samples = []
        limit = space
        for glyph in glyphs:
            cell = glyph.group_cell if glyph.group_cell is not None else glyph.cell
            limit = min(limit, space - distance(cell.right(), glyph.start))
            ink = _ink_box(QFont(glyph.font), glyph.text)
            if not ink.isEmpty() and glyph.text.strip():
                samples.append((cell.center().x(), glyph, glyph.transform.map(ink)))
        # Eight probes per em resolve corners; the global cap bounds pathological
        # self-intersections, extreme offsets and unusually dense text.
        step = max(0.5, straight.em / 8, space / 4096)
        while lag <= limit + 1e-6:
            paths = []
            for x, glyph, ink in samples:
                transform = track.frame_at(max(0, min(space, distance(x, glyph.start) + lag)))
                transform.translate(-x, -baseline)
                paths.append(transform.map(ink))
            if not grid.collides(paths):
                grid.add(paths)
                offsets[start] = lag
                break
            lag += step
            probes += 1
            if probes >= 4096:
                return offsets, start
        else:
            return offsets, start
    return offsets, None


class PathSpacing:
    """Use one distance map for collision-adjusted glyphs and editing carets."""

    def __init__(self, track, straight, baseline, initial, shift, positions):
        self.initial, self.shift, self.positions = initial, shift, positions
        self.starts = sorted(positions)
        self.offsets, self.cutoff = (
            collision_spacing(track, straight, baseline, self.original_distance)
            if track.settings.get("avoid_overlap", False)
            else ({}, None)
        )
        self.offset_starts = sorted(self.offsets)

    def original_distance(self, x, index):
        preceding = self.starts[max(0, bisect_right(self.starts, index) - 1)] if self.starts else 0
        return x - self.initial + self.shift + self.positions.get(preceding, 0)

    def __call__(self, x, index):
        preceding = (
            self.offset_starts[max(0, bisect_right(self.offset_starts, index) - 1)]
            if self.offset_starts
            else 0
        )
        return self.original_distance(x, index) + self.offsets.get(preceding, 0)
