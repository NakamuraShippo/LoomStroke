"""Integer native-ink painting; K is colour data, alpha is coverage."""

from __future__ import annotations

import numpy as np

from .pixels import NativeCmykPixels


class NativeCmykPixelOperations:
    @staticmethod
    def paint(
        before: NativeCmykPixels,
        color: tuple[int, int, int, int] | np.ndarray,
        coverage: np.ndarray,
        selected: tuple[int, ...] = (0, 1, 2, 3),
        *,
        erase: bool = False,
        alpha_lock: bool = False,
    ) -> NativeCmykPixels:
        maximum = np.iinfo(before.inks.dtype).max
        if coverage.shape != before.alpha.shape or coverage.dtype != np.uint8:
            raise ValueError("Brush coverage must be an unsigned 8-bit plane matching the patch")
        if not selected or any(c not in (0, 1, 2, 3) for c in selected):
            raise ValueError("Select one or more CMYK plates")
        selected = tuple(sorted(set(selected)))
        values = np.asarray(color)
        if (
            values.shape not in ((4,), before.inks.shape)
            or not np.issubdtype(values.dtype, np.integer)
            or np.any(values < 0)
            or np.any(values > maximum)
        ):
            raise ValueError("Ink colour is outside the native sample range")
        values = values.astype(np.uint64)
        opacity = coverage.astype(np.uint64) * (257 if maximum == 65535 else 1)
        inks = before.inks.astype(np.uint64)
        alpha = before.alpha.astype(np.uint64)
        remainder = maximum - opacity
        if erase:
            if selected == (0, 1, 2, 3) and not alpha_lock:
                alpha = (alpha * remainder + maximum // 2) // maximum
            elif not alpha_lock:
                for c in selected:
                    inks[:, :, c] = (inks[:, :, c] * remainder + maximum // 2) // maximum
        elif selected != (0, 1, 2, 3) or alpha_lock:
            for c in selected:
                source = values[c] if values.ndim == 1 else values[:, :, c]
                inks[:, :, c] = (
                    inks[:, :, c] * remainder + source * opacity + maximum // 2
                ) // maximum
        else:
            denominator = opacity * maximum + alpha * remainder
            for c in selected:
                source = values[c] if values.ndim == 1 else values[:, :, c]
                numerator = source * opacity * maximum + inks[:, :, c] * alpha * remainder
                mixed = (numerator + denominator // 2) // np.maximum(denominator, 1)
                inks[:, :, c] = np.where(opacity > 0, mixed, inks[:, :, c])
            alpha = (denominator + maximum // 2) // maximum
        return NativeCmykPixels(
            inks.astype(before.inks.dtype), alpha.astype(before.alpha.dtype), before.profile
        )
