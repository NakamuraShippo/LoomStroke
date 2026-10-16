"""Map outputs from 3D preview rendering."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QImage


@dataclass(frozen=True, slots=True)
class MapOutputBundle:
    """Depth, normal, segmentation, and optional outline map images."""

    depth: QImage
    normal: QImage
    segmentation: QImage
    outline: QImage | None = None
