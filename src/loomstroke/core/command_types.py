"""Core command base types and value objects."""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, TypeAlias

from .geometry import Affine2D

if TYPE_CHECKING:
    from .canvas import Canvas


class BlendMode(Enum):
    """Blend modes for paint operations."""

    PASS_THROUGH = "pass_through"
    NORMAL = "normal"
    DISSOLVE = "dissolve"
    # Darken group
    DARKEN = "darken"
    MULTIPLY = "multiply"
    COLOR_BURN = "color_burn"
    LINEAR_BURN = "linear_burn"
    DARKER_COLOR = "darker_color"
    # Lighten group
    LIGHTEN = "lighten"
    SCREEN = "screen"
    COLOR_DODGE = "color_dodge"
    LINEAR_DODGE = "linear_dodge"
    LIGHTER_COLOR = "lighter_color"
    # Contrast group
    OVERLAY = "overlay"
    SOFT_LIGHT = "soft_light"
    HARD_LIGHT = "hard_light"
    VIVID_LIGHT = "vivid_light"
    LINEAR_LIGHT = "linear_light"
    PIN_LIGHT = "pin_light"
    HARD_MIX = "hard_mix"
    # Inversion group
    DIFFERENCE = "difference"
    EXCLUSION = "exclusion"
    SUBTRACT = "subtract"
    DIVIDE = "divide"
    # Component (HSL) group
    HUE = "hue"
    SATURATION = "saturation"
    COLOR = "color"
    LUMINOSITY = "luminosity"


@dataclass(frozen=True, slots=True)
class LayerTransform:
    """Non-destructive transform applied when rendering a raster layer."""

    tx: float = 0.0
    ty: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    rotation: float = 0.0
    origin_x: float = 0.0
    origin_y: float = 0.0

    @staticmethod
    def identity() -> LayerTransform:
        return LayerTransform()

    @staticmethod
    def translation(tx: float, ty: float) -> LayerTransform:
        return LayerTransform(tx=float(tx), ty=float(ty))

    @staticmethod
    def from_affine(transform: Affine2D) -> LayerTransform:
        scale_x = math.hypot(transform.a, transform.c)
        scale_y = math.hypot(transform.b, transform.d)
        if transform.a * transform.d - transform.b * transform.c < 0.0:
            scale_y = -scale_y
        rotation = math.atan2(transform.c, transform.a) if scale_x > 1e-10 else 0.0
        return LayerTransform(
            tx=float(transform.tx),
            ty=float(transform.ty),
            scale_x=float(scale_x),
            scale_y=float(scale_y),
            rotation=float(rotation),
        )

    def with_translation(self, tx: float, ty: float) -> LayerTransform:
        return LayerTransform(
            tx=float(tx),
            ty=float(ty),
            scale_x=self.scale_x,
            scale_y=self.scale_y,
            rotation=self.rotation,
            origin_x=self.origin_x,
            origin_y=self.origin_y,
        )

    def to_affine(self) -> Affine2D:
        cos_r = math.cos(self.rotation)
        sin_r = math.sin(self.rotation)
        a = cos_r * self.scale_x
        b = -sin_r * self.scale_y
        c = sin_r * self.scale_x
        d = cos_r * self.scale_y
        tx = self.tx + self.origin_x - (a * self.origin_x + b * self.origin_y)
        ty = self.ty + self.origin_y - (c * self.origin_x + d * self.origin_y)
        return Affine2D(a, b, c, d, tx, ty)


@dataclass(frozen=True)
class StrokePoint:
    """A single point in a stroke with tablet data."""

    x: float
    y: float
    pressure: float = 1.0
    tilt_x: float = 0.0
    tilt_y: float = 0.0
    rotation: float = 0.0
    timestamp: float = 0.0  # milliseconds


@dataclass(frozen=True)
class BrushSettings:
    """Brush settings for paint operations."""

    size: float = 10.0
    opacity: float = 1.0
    hardness: float = 0.8
    spacing: float = 0.1  # As percentage of brush size
    flow: float = 1.0
    tone_range: float = 1.0
    tone_gamma: float = 0.6
    blend_mode: BlendMode = BlendMode.NORMAL
    color: tuple[int, int, int] = (0, 0, 0)  # RGB
    anti_alias: bool = True


@dataclass(frozen=True)
class DirtyRegion:
    """Region that needs redrawing."""

    x: int
    y: int
    width: int
    height: int

    def union(self, other: DirtyRegion) -> DirtyRegion:
        """Compute union of two dirty regions."""
        x1 = min(self.x, other.x)
        y1 = min(self.y, other.y)
        x2 = max(self.x + self.width, other.x + other.width)
        y2 = max(self.y + self.height, other.y + other.height)
        return DirtyRegion(x1, y1, x2 - x1, y2 - y1)


SelectionRect: TypeAlias = tuple[int, int, int, int]


class SelectionOp(str, Enum):
    """Selection mask composition operation."""

    REPLACE = "replace"
    ADD = "add"
    SUBTRACT = "subtract"
    INTERSECT = "intersect"


SELECTION_MASK_LAYER_ID = "__selection_mask__"


@dataclass(slots=True)
class SelectionSnapshot:
    """Snapshot of the selection mask for undo/redo."""

    mask: Any | None
    bounds: SelectionRect | None


class Command(ABC):
    """
    Base command interface per Master.md specifications.
    Commands must implement apply() and revert() for undo/redo support.
    """

    def __init__(self, merge_key: str | None = None) -> None:
        """
        Initialize command with optional merge key.

        Args:
            merge_key: Key for merging consecutive similar commands (e.g., stroke continuation)
        """
        self.merge_key = merge_key
        self._executed = False
        self._dirty_region: DirtyRegion | None = None

    @abstractmethod
    def apply(self, canvas: Canvas) -> bool:
        """
        Apply the command to the canvas.

        Args:
            canvas: The canvas to apply the command to

        Returns:
            True if successful, False otherwise
        """
        ...

    @abstractmethod
    def revert(self, canvas: Canvas) -> bool:
        """
        Revert the command (for undo).

        Args:
            canvas: The canvas to revert the command from

        Returns:
            True if successful, False otherwise
        """
        ...

    @property
    def dirty_region(self) -> DirtyRegion | None:
        """Get the region affected by this command."""
        return self._dirty_region

    def can_merge_with(self, other: Command) -> bool:
        """
        Check if this command can be merged with another.

        Args:
            other: The command to potentially merge with

        Returns:
            True if commands can be merged
        """
        if not self.merge_key or not other.merge_key:
            return False
        return self.merge_key == other.merge_key and isinstance(other, type(self))


class CompositeCommand(Command):
    """
    Composite command that groups multiple commands.
    Used for complex operations that need to be undone as a unit.
    """

    def __init__(self, commands: Sequence[Command], description: str = "") -> None:
        """
        Initialize composite command.

        Args:
            commands: Sequence of commands to execute
            description: Human-readable description
        """
        super().__init__()
        self.commands = list(commands)
        self.description = description

    def apply(self, canvas: Canvas) -> bool:
        """Apply all commands in sequence."""
        for i, command in enumerate(self.commands):
            if not command.apply(canvas):
                # Rollback on failure
                for j in range(i - 1, -1, -1):
                    self.commands[j].revert(canvas)
                return False

        # Compute combined dirty region
        if self.commands:
            regions = [cmd.dirty_region for cmd in self.commands if cmd.dirty_region]
            if regions:
                self._dirty_region = regions[0]
                for region in regions[1:]:
                    self._dirty_region = self._dirty_region.union(region)

        self._executed = True
        return True

    def revert(self, canvas: Canvas) -> bool:
        """Revert all commands in reverse order."""
        if not self._executed:
            return False

        for command in reversed(self.commands):
            if not command.revert(canvas):
                return False

        self._executed = False
        return True
