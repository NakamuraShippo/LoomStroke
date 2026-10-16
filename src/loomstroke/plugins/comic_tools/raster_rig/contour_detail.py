"""Morph a unified ink outline toward measured body shape/width, never RGB blends."""

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QPainterPath, QPainterPathStroker


def normalized_fidelity(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.0
    return float(np.clip(value, 0, 1)) if np.isfinite(value) else 0.0


class ContourDetail:
    def __init__(self, geometry):
        self.geometry = geometry
        guide = geometry.guide
        # Registration-only correspondence; interaction keeps the stable guide.
        indices = [
            np.argmin(np.sum((geometry.source_centres - p) ** 2, axis=1)) for p in guide.outline
        ]
        self.displacement = geometry.source_centres[indices] - guide.outline
        self.widths = geometry.source_widths[indices]
        self.cell_size = max(8.0, geometry.width * 2)
        self.cells = {}
        self._offset_cache = {True: {}, False: {}}
        for i, a in enumerate(guide.outline):
            b = guide.outline[(i + 1) % len(guide.outline)]
            low = np.floor((np.minimum(a, b) - geometry.width * 2) / self.cell_size).astype(int)
            high = np.floor((np.maximum(a, b) + geometry.width * 2) / self.cell_size).astype(int)
            for x in range(low[0], high[0] + 1):
                for y in range(low[1], high[1] + 1):
                    self.cells.setdefault((x, y), []).append(i)

    def _nearby_batches(self, points):
        groups = {}
        for row, cell in enumerate(np.floor(points / self.cell_size).astype(int)):
            key = tuple(cell)
            if key in self.cells:
                groups.setdefault(key, []).append(row)
        for key, rows in groups.items():
            for start in range(0, len(rows), 128):
                yield np.asarray(rows[start : start + 128]), np.asarray(self.cells[key])

    def paths(self, path, root_u, fidelity, *, fill=True):
        stroker = QPainterPathStroker()
        stroker.setWidth(self.geometry.width)
        stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        ink = stroker.createStroke(path)
        if fidelity <= 0:
            return ink, path
        return (
            self._morph(ink, root_u, fidelity, ink=True),
            self._morph(path, root_u, fidelity, ink=False) if fill else path,
        )

    def _morph(self, path, root_u, fidelity, *, ink):
        g = self.geometry.guide
        a = g.outline
        edges = np.roll(a, -1, axis=0) - a
        lengths = np.linalg.norm(edges, axis=1)
        result = QPainterPath()
        result.setFillRule(path.fillRule())
        cache = self._offset_cache[ink]
        if len(cache) > 16384:
            cache.clear()
        for polygon in path.toSubpathPolygons():
            points = np.array([(p.x(), p.y()) for p in polygon])
            offsets = np.zeros_like(points)
            positions = np.full(len(points), root_u)
            missing = []
            for row, point in enumerate(points):
                cached = cache.get(tuple(point))
                if cached is None:
                    missing.append(row)
                else:
                    offsets[row], positions[row] = cached
            missing = np.asarray(missing, dtype=int)
            # Spatial lookup avoids all-points x all-segments work per frame.
            for local_rows, candidates in self._nearby_batches(points[missing]):
                rows = missing[local_rows]
                p = points[rows]
                local_edges = edges[candidates]
                fraction = np.clip(
                    np.sum((p[:, None] - a[candidates]) * local_edges, axis=2)
                    / np.maximum(lengths[candidates] ** 2, 1e-9),
                    0,
                    1,
                )
                feet = a[candidates] + fraction[:, :, None] * local_edges
                distance2 = np.sum((p[:, None] - feet) ** 2, axis=2)
                local_i = np.argmin(distance2, axis=1)
                i = candidates[local_i]
                row = np.arange(len(p))
                t = fraction[row, local_i]
                j = (i + 1) % len(a)
                foot = feet[row, local_i]
                u = (g.cum[i] + t * lengths[i]) / g.perimeter
                normal = g.normals[i] * (1 - t[:, None]) + g.normals[j] * t[:, None]
                normal /= np.maximum(np.linalg.norm(normal, axis=1)[:, None], 1e-9)
                delta = self.displacement[i] * (1 - t[:, None]) + self.displacement[j] * t[:, None]
                if ink:
                    width = self.widths[i] * (1 - t) + self.widths[j] * t
                    side = np.sign(np.sum((p - foot) * normal, axis=1))
                    delta += normal * (side * (width - self.geometry.width) / 2)[:, None]
                distance = np.sqrt(distance2[row, local_i])
                near = np.clip((self.geometry.width * 2 - distance) / self.geometry.width, 0, 1)
                offsets[rows] = delta * near[:, None]
                positions[rows] = u
                for index in rows:
                    cache[tuple(points[index])] = (offsets[index].copy(), positions[index])
            # Keep the joint unchanged; only this fade depends on root pose.
            fade = self._joint_fade(positions, root_u)
            moved = points + offsets * (fidelity * fade)[:, None]
            if len(moved):
                result.moveTo(QPointF(*moved[0]))
                for point in moved[1:]:
                    result.lineTo(QPointF(*point))
                result.closeSubpath()
        return result

    def _joint_fade(self, positions, root_u):
        perimeter = self.geometry.guide.perimeter
        half = min(0.09, self.geometry.gap / (2 * perimeter))
        distance = np.abs((positions - root_u + 0.5) % 1 - 0.5)
        fade = np.clip((distance - half) * perimeter / (self.geometry.width * 2), 0, 1)
        return fade * fade * (3 - 2 * fade)
