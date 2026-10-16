"""Exact float64 ownership classification, fused into a bounded tile kernel."""

import math

import numpy as np


def numpy_labels(width, top, bottom, points, owners, corner_radius):
    xs = np.arange(width, dtype=np.float64)[None, :] + 0.5
    ys = np.arange(top, bottom, dtype=np.float64)[:, None] + 0.5
    best = np.full((bottom - top, width), np.inf)
    labels = np.zeros((bottom - top, width), dtype=np.int16)
    for index in range(len(points)):
        a, b = points[index], points[(index + 1) % len(points)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        t = np.clip(((xs - a[0]) * dx + (ys - a[1]) * dy) / (dx * dx + dy * dy), 0, 1)
        distance = (xs - a[0] - t * dx) ** 2 + (ys - a[1] - t * dy) ** 2
        labels[distance < best] = index
        np.minimum(best, distance, out=best)
    for vertex, owner in zip(points, owners, strict=True):
        near = (xs - vertex[0]) ** 2 + (ys - vertex[1]) ** 2 <= corner_radius**2
        labels[near] = owner
    return labels


def _build_kernel():
    try:
        from numba import njit  # noqa: PLC0415 - same optional dependency as flood fill
    except ImportError:
        return None

    @njit(cache=True, nogil=True, fastmath=False)
    def kernel(width, top, bottom, points, owners, corner_radius):
        labels = np.zeros((bottom - top, width), dtype=np.int16)
        count = len(points)
        directions = np.empty((count, 3), dtype=np.float64)
        for edge in range(count):
            next_edge = (edge + 1) % count
            dx = points[next_edge, 0] - points[edge, 0]
            dy = points[next_edge, 1] - points[edge, 1]
            directions[edge, 0], directions[edge, 1] = dx, dy
            directions[edge, 2] = dx * dx + dy * dy
        radius2 = corner_radius**2
        for row in range(bottom - top):
            y = top + row + 0.5
            for column in range(width):
                x = column + 0.5
                best, label = np.inf, 0
                for edge in range(count):
                    ax, ay = points[edge, 0], points[edge, 1]
                    dx, dy = directions[edge, 0], directions[edge, 1]
                    t = ((x - ax) * dx + (y - ay) * dy) / directions[edge, 2]
                    if t < 0:
                        t = 0.0
                    elif t > 1:
                        t = 1.0
                    distance = (x - ax - t * dx) ** 2 + (y - ay - t * dy) ** 2
                    if distance < best:
                        best, label = distance, edge
                    elif math.isnan(distance):
                        best = distance
                for vertex in range(count):
                    if (x - points[vertex, 0]) ** 2 + (y - points[vertex, 1]) ** 2 <= radius2:
                        label = owners[vertex]
                labels[row, column] = label
        return labels

    return kernel


compiled_labels = _build_kernel()


def ownership_labels(width, top, bottom, points, owners, corner_radius):
    source = np.asarray(points, dtype=np.float64)
    owners = np.asarray(owners, dtype=np.int16)
    calculate = compiled_labels if compiled_labels is not None else numpy_labels
    return calculate(width, top, bottom, source, owners, float(corner_radius))
