"""Detached inputs for fill coverage calculation on a worker."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class FillRegionRequest:
    reference: np.ndarray
    seed: tuple[int, int]
    tolerance: int = 8
    gap_close: int = 0
    expansion: int = 0
    mode: str = "Bucket"
    enclosure: np.ndarray | None = None
    allowed: np.ndarray | None = None
