"""Closest-point refinement for cubic path picking and anchor insertion."""

from __future__ import annotations

from typing import Any


class CubicHitTest:
    @classmethod
    def hits_vector(cls, px: float, py: float, vec: Any, radius: float) -> bool:
        """Pick anchors, direction endpoints and actual open or closed curve segments."""
        limit = radius * radius
        for path in getattr(vec, "paths", ()) or ():
            anchors = getattr(path, "anchors", ()) or ()
            for anchor in anchors:
                ax, ay = anchor.pos
                if (px - ax) ** 2 + (py - ay) ** 2 <= limit:
                    return True
                for handle in (anchor.h_in, anchor.h_out):
                    if handle and (px - ax - handle[0]) ** 2 + (py - ay - handle[1]) ** 2 <= limit:
                        return True
            count = len(anchors)
            segments = count if getattr(path, "closed", False) else count - 1
            for i in range(max(0, segments)):
                if (
                    cls.distance_squared(
                        px, py, anchors[i], anchors[(i + 1) % count], max_distance=radius
                    )[0]
                    <= limit
                ):
                    return True
        return False

    @staticmethod
    def distance_squared(
        px: float,
        py: float,
        a: Any,
        b: Any,
        *,
        samples: int = 16,
        max_distance: float | None = None,
    ) -> tuple[float, float]:
        """Refine sampled local minima instead of picking only sampled vertices.

        Work is bounded by the sample count and 36 refinement steps per minimum;
        returned t stays on the original cubic for accurate de Casteljau insertion.
        """
        p0, p3 = a.pos, b.pos
        h_out, h_in = a.h_out, b.h_in
        p1 = (p0[0] + (h_out[0] if h_out else 0), p0[1] + (h_out[1] if h_out else 0))
        p2 = (p3[0] + (h_in[0] if h_in else 0), p3[1] + (h_in[1] if h_in else 0))
        if max_distance is not None:
            xs, ys = zip(p0, p1, p2, p3, strict=True)
            if (
                px < min(xs) - max_distance
                or px > max(xs) + max_distance
                or py < min(ys) - max_distance
                or py > max(ys) + max_distance
            ):
                return (float("inf"), 0.0)
        if not any(h and (abs(h[0]) > 1e-3 or abs(h[1]) > 1e-3) for h in (h_out, h_in)):
            dx, dy = p3[0] - p0[0], p3[1] - p0[1]
            length_sq = dx * dx + dy * dy
            t = (
                max(0.0, min(1.0, ((px - p0[0]) * dx + (py - p0[1]) * dy) / length_sq))
                if length_sq
                else 0.0
            )
            return ((px - p0[0] - t * dx) ** 2 + (py - p0[1] - t * dy) ** 2, t)

        def distance(t: float) -> float:
            u = 1.0 - t
            x = u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0]
            y = u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1]
            return (px - x) ** 2 + (py - y) ** 2

        count = max(2, samples)
        distances = [distance(i / count) for i in range(count + 1)]
        best_index = min(range(count + 1), key=distances.__getitem__)
        best = (distances[best_index], best_index / count)
        for i, value in enumerate(distances):
            if (i > 0 and value > distances[i - 1]) or (i < count and value > distances[i + 1]):
                continue
            low, high = max(0, i - 1) / count, min(count, i + 1) / count
            for _ in range(36):
                left = low + (high - low) / 3
                right = high - (high - low) / 3
                if distance(left) <= distance(right):
                    high = right
                else:
                    low = left
            t = (low + high) * 0.5
            candidate = (distance(t), t)
            if candidate[0] < best[0]:
                best = candidate
        return best
