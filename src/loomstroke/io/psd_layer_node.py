"""Detached PSD hierarchy; no live Qt or canvas objects cross the I/O boundary."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class PsdLayerNode:
    name: str
    image: np.ndarray | None = None
    offset: tuple[int, int] = (0, 0)
    blend_mode: str = "normal"
    opacity: float = 1.0
    visible: bool = True
    group: bool = False
    children: tuple[PsdLayerNode, ...] = ()
    mask: np.ndarray | None = None
    mask_enabled: bool = True
    clipping: bool = False
    warnings: tuple[str, ...] = field(default_factory=tuple)
