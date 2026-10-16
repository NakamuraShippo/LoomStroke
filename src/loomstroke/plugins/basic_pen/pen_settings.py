"""Persistent scalar pen settings."""

from dataclasses import dataclass, field

from PySide6.QtGui import QColor


@dataclass
class PenSettings:
    """Pen tool settings."""

    size: float = 5.0

    opacity: float = 1.0

    hardness: float = 1.0

    spacing: float = 0.1

    color: QColor = field(default_factory=lambda: QColor(0, 0, 0))

    # Tablet pressure is handled internally during command execution per CLAUDE.md

    pressure_sensitive: bool = True

    tilt_angle: bool = False

    smoothing: float = 0.0

    recontour: bool = False

    recontour_smoothness: float = 30.0

    flow: float = 1.0

    tone_range: float = 1.0

    tone_gamma: float = 0.6

    taper_start_length: float = 0.0

    taper_end_length: float = 0.0
