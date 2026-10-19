"""Track changed coverage and safe CMYK paint-only projection transitions."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any

import numpy as np
from PySide6.QtCore import QRect
from PySide6.QtGui import QImage

from loomstroke.core.native_cmyk.affine import document_rect, placement


@dataclass(frozen=True)
class CoverageDamage:
    regions: tuple[QRect, ...]
    priority: tuple[QRect, ...]


def coverage_damage(
    before: QImage,
    old_origin: tuple[int, int],
    after: QImage,
    origin: tuple[int, int],
    *,
    before_alpha: np.ndarray | None = None,
    after_alpha: np.ndarray | None = None,
) -> CoverageDamage:
    old_rect = QRect(*old_origin, before.width(), before.height())
    new_rect = QRect(*origin, after.width(), after.height())
    overlap = old_rect.intersected(new_rect)
    arrays = [
        coverage_alpha(before) if before_alpha is None else before_alpha,
        coverage_alpha(after) if after_alpha is None else after_alpha,
    ]
    tiles: dict[tuple[int, int], tuple[QRect, int]] = {}

    def record(values: np.ndarray, rect: QRect) -> None:
        for y in range(rect.top() // 256, rect.bottom() // 256 + 1):
            for x in range(rect.left() // 256, rect.right() // 256 + 1):
                part = QRect(x * 256, y * 256, 256, 256).intersected(rect)
                patch = _patch(values, rect, part)
                occupied = _occupied(patch, part.x(), part.y())
                if occupied.isEmpty():
                    continue
                score = int(patch.max())
                previous = tiles.get((x, y))
                if previous is not None:
                    occupied = occupied.united(previous[0])
                    score = max(score, previous[1])
                tiles[(x, y)] = (occupied, score)

    if not overlap.isEmpty():
        patches = [
            _patch(values, rect, overlap)
            for values, rect in zip(arrays, (old_rect, new_rect), strict=True)
        ]
        changed = patches[0] != patches[1]
        for y in range(overlap.top() // 256, overlap.bottom() // 256 + 1):
            for x in range(overlap.left() // 256, overlap.right() // 256 + 1):
                part = QRect(x * 256, y * 256, 256, 256).intersected(overlap)
                occupied = _occupied(_patch(changed, overlap, part), part.x(), part.y())
                if occupied.isEmpty():
                    continue
                delta = np.abs(
                    np.subtract(
                        _patch(patches[0], overlap, occupied),
                        _patch(patches[1], overlap, occupied),
                        dtype=np.int16,
                    )
                )
                record(delta, occupied)
    for values, rect in zip(arrays, (old_rect, new_rect), strict=True):
        for strip in _outside(rect, overlap):
            record(_patch(values, rect, strip), strip)
    ordered = sorted(tiles.values(), key=lambda item: item[1], reverse=True)
    threshold = max(2, ordered[0][1] // 2) if ordered else 2
    return CoverageDamage(
        tuple(rect for rect, _score in ordered),
        tuple(rect for rect, score in ordered if score >= threshold),
    )


def coverage_alpha(image: QImage) -> np.ndarray:
    if image.format() == QImage.Format.Format_Alpha8:
        rows = np.frombuffer(image.constBits(), np.uint8).reshape(
            image.height(), image.bytesPerLine()
        )
        return rows[:, : image.width()].copy(order="C")
    converted = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    rows = np.frombuffer(converted.constBits(), np.uint8).reshape(
        converted.height(), converted.bytesPerLine()
    )
    offset = 3 if sys.byteorder == "little" else 0
    return rows[:, offset : converted.width() * 4 : 4].copy(order="C")


def _patch(values: np.ndarray, bounds: QRect, rect: QRect) -> np.ndarray:
    x, y = rect.x() - bounds.x(), rect.y() - bounds.y()
    return values[y : y + rect.height(), x : x + rect.width()]


def _occupied(values: np.ndarray, x: int, y: int) -> QRect:
    rows = np.flatnonzero(np.any(values, axis=1))
    if not len(rows):
        return QRect()
    columns = np.flatnonzero(np.any(values, axis=0))
    return QRect(
        x + int(columns[0]),
        y + int(rows[0]),
        int(columns[-1] - columns[0] + 1),
        int(rows[-1] - rows[0] + 1),
    )


def _outside(rect: QRect, overlap: QRect) -> list[QRect]:
    if overlap.isEmpty():
        return [rect]
    strips = [
        QRect(rect.x(), rect.y(), rect.width(), overlap.y() - rect.y()),
        QRect(rect.x(), overlap.bottom() + 1, rect.width(), rect.bottom() - overlap.bottom()),
        QRect(rect.x(), overlap.y(), overlap.x() - rect.x(), overlap.height()),
        QRect(overlap.right() + 1, overlap.y(), rect.right() - overlap.right(), overlap.height()),
    ]
    return [strip for strip in strips if not strip.isEmpty()]


class NativeCmykPaintDamage:
    def __init__(self) -> None:
        self._structure: Any = None
        self._groups: set[str] = set()
        self._rects: dict[str, QRect] = {}

    def update(self, structure: Any, scene: Any) -> tuple[QRect, ...] | None:
        groups = {
            node.id
            for node in scene.flat_layers
            if getattr(node, "vector_kind", "") == "native-cmyk-paints"
            or (
                node.layer_type in ("vector", "text")
                and (
                    # RGB-origin objects use derived ink sources too. Their
                    # paint changes do not replace the unchanged background.
                    hasattr(node.native_cmyk, "image")
                    or (
                        not node.visible
                        and node.native_cmyk is None
                        and not getattr(node, "vector_objects", ())
                    )
                )
            )
        }
        rects = {}
        for node in scene.flat_layers:
            source = node.native_cmyk
            if source is not None and hasattr(source, "image"):
                rects[node.id] = document_rect(
                    QRect(
                        source.image_origin[0],
                        source.image_origin[1],
                        source.image.width(),
                        source.image.height(),
                    ),
                    placement(node),
                )
        old, old_groups, old_rects = self._structure, self._groups, self._rects
        self._structure, self._groups, self._rects = structure, groups, rects
        if old is None or old[:2] != structure[:2]:
            return None
        scope = old_groups | groups

        def partition(
            rows: tuple[tuple[Any, ...], ...],
        ) -> tuple[dict[str, tuple[tuple[Any, ...], int]], list[tuple[Any, ...]]]:
            paints, normal, anchor = {}, [], 0
            for row in rows:
                if row[0] in scope or (row[2] in scope and ":cmyk-paint-" in row[0]):
                    paints[row[0]] = (row, anchor)
                else:
                    normal.append(row)
                    anchor += 1
            return paints, normal

        previous, previous_normal = partition(old[2])
        current, current_normal = partition(structure[2])
        if previous_normal != current_normal or any(row[7] for row in current_normal):
            return None
        common = previous.keys() & current.keys()
        reorder = [key for key in previous if key in common] != [
            key for key in current if key in common
        ]
        if reorder or any(row[0][7] for row in (*previous.values(), *current.values())):
            changed = previous.keys() | current.keys()
        else:
            changed = {
                key
                for key in previous.keys() | current.keys()
                if previous.get(key) != current.get(key)
            }
        changed = changed | {
            key for key, value in (*previous.items(), *current.items()) if value[0][2] in changed
        }
        return tuple(
            QRect(rect)
            for key in changed
            for rect in (old_rects.get(key), rects.get(key))
            if rect is not None
        )
