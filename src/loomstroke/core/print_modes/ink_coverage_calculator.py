"""CPU separation / ink coverage oracle (CT-24 D4 preview math, profile-less approximation).

``rgba8`` sources are separated with the naive RGB→CMYK relation (documented as an
estimate, not a proofing transform); ``cmyk8`` planes are used as-is.  Produces
per-plate coverage, total area coverage (TAC) statistics and a plate-on/off composite.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar

import numpy as np

from .separation_snapshot import SeparationSnapshot

__all__ = ["InkCoverageCalculator"]

PROCESS_PLATES: tuple[str, ...] = ("c", "m", "y", "k")


class InkCoverageCalculator:
    DEFAULT_TAC_LIMIT: ClassVar[float] = 300.0

    def separate_rgba(self, rgba: np.ndarray) -> np.ndarray:
        """``(h, w, 4)`` uint8 straight RGBA -> float32 CMYK in [0, 1], alpha-weighted."""
        array = np.asarray(rgba)
        if array.ndim != 3 or array.shape[2] != 4:
            raise ValueError("rgba must be (h, w, 4)")
        rgb = array[..., :3].astype(np.float32) / 255.0
        alpha = array[..., 3].astype(np.float32) / 255.0
        k = 1.0 - np.max(rgb, axis=2)
        denominator = np.where(k < 1.0, 1.0 - k, 1.0)
        c = (1.0 - rgb[..., 0] - k) / denominator
        m = (1.0 - rgb[..., 1] - k) / denominator
        y = (1.0 - rgb[..., 2] - k) / denominator
        cmyk = np.stack([c, m, y, k], axis=2)
        cmyk = np.clip(cmyk, 0.0, 1.0) * alpha[..., None]
        return cmyk.astype(np.float32)

    def snapshot(
        self,
        cmyk: np.ndarray,
        *,
        page_id: str,
        plates_on: Sequence[str] | None = None,
        tac_limit: float = DEFAULT_TAC_LIMIT,
        source_mode: str = "rgba8",
    ) -> SeparationSnapshot:
        planes = np.asarray(cmyk, dtype=np.float32)
        if planes.ndim != 3 or planes.shape[2] != 4:
            raise ValueError("cmyk must be (h, w, 4)")
        enabled = tuple(p for p in PROCESS_PLATES if plates_on is None or p in plates_on)
        coverage = {
            plate: float(planes[..., index].mean() * 100.0)
            for index, plate in enumerate(PROCESS_PLATES)
        }
        tac = planes.sum(axis=2) * 100.0
        over = float((tac > tac_limit).mean() * 100.0)
        return SeparationSnapshot(
            page_id=page_id,
            source_mode=source_mode,
            plates=PROCESS_PLATES,
            plates_on=enabled,
            coverage_percent=coverage,
            max_tac_percent=float(tac.max()) if tac.size else 0.0,
            mean_tac_percent=float(tac.mean()) if tac.size else 0.0,
            over_limit_percent=over,
            tac_limit_percent=float(tac_limit),
            width=int(planes.shape[1]),
            height=int(planes.shape[0]),
        )

    def composite(self, cmyk: np.ndarray, plates_on: Sequence[str]) -> np.ndarray:
        """Plate on/off preview: ``(h, w, 3)`` uint8 RGB from the enabled plates only."""
        planes = np.asarray(cmyk, dtype=np.float32)
        mask = np.array([1.0 if p in plates_on else 0.0 for p in PROCESS_PLATES], dtype=np.float32)
        used = planes * mask[None, None, :]
        k = used[..., 3]
        rgb = (1.0 - np.minimum(1.0, used[..., :3] + k[..., None])) * 255.0
        return np.clip(np.rint(rgb), 0, 255).astype(np.uint8)
