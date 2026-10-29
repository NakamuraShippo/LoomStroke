"""Guide-related data models for rulers and guides."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from PySide6.QtCore import QPointF

GuideOrientation = Literal["horizontal", "vertical"]
RulerUnit = Literal["px", "mm", "inch"]


@dataclass(slots=True)
class Guide:
    """Represents a single guide line in canvas coordinates."""

    id: str
    orientation: GuideOrientation
    position: float  # Canvas coordinate (x for vertical, y for horizontal)
    rotation: float = 0.0
    pivot: tuple[float, float] | None = None


@dataclass(slots=True)
class RulerState:
    """Ruler state stored per page."""

    unit: RulerUnit
    origin: QPointF
