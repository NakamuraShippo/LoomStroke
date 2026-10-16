"""
Brush tool implementation for LoomStroke
"""

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray


@dataclass
class BrushSettings:
    """Brush tool settings"""

    size: float = 10.0
    hardness: float = 0.8  # 0.0 (soft) to 1.0 (hard)
    opacity: float = 1.0  # 0.0 to 1.0
    flow: float = 1.0  # 0.0 to 1.0
    spacing: float = 0.25  # As fraction of brush size
    color: tuple[int, int, int, int] = (0, 0, 0, 255)  # RGBA
    smoothing: float = 0.0  # 0.0 to 1.0
    pressure_size: bool = True
    pressure_opacity: bool = True


class BrushTool:
    """Basic brush tool implementation"""

    def __init__(self, settings: BrushSettings | None = None):
        """Initialize brush tool"""
        self.settings = settings or BrushSettings()
        # Cache brush stamps keyed by (size*10, hardness*100).
        self.brush_cache: dict[tuple[int, int], NDArray[Any]] = {}
        self.last_point: tuple[int, int, float] | None = None
        self.distance_accumulator = 0.0

    def create_brush_stamp(self, size: float, hardness: float) -> NDArray[Any]:
        """
        Create a circular brush stamp

        Args:
            size: Brush diameter
            hardness: Brush hardness (0-1)

        Returns:
            Grayscale brush stamp (0-255)
        """
        # Check cache
        cache_key = (int(size * 10), int(hardness * 100))
        if cache_key in self.brush_cache:
            return self.brush_cache[cache_key]

        # Create brush stamp
        diameter = int(np.ceil(size))
        if diameter % 2 == 0:
            diameter += 1  # Ensure odd size for center

        radius = diameter / 2.0
        center = diameter // 2

        # Create grid
        y, x = np.ogrid[:diameter, :diameter]
        distance = np.sqrt((x - center) ** 2 + (y - center) ** 2)

        # Create falloff
        stamp = np.zeros((diameter, diameter), dtype=np.float32)

        if hardness >= 1.0:
            # Hard brush
            stamp[distance <= radius] = 1.0
        else:
            # Soft brush with falloff
            inner_radius = radius * hardness

            # Inner area (full opacity)
            stamp[distance <= inner_radius] = 1.0

            # Falloff area
            falloff_mask = (distance > inner_radius) & (distance <= radius)
            if falloff_mask.any():
                # Smooth falloff using cosine
                t = (distance[falloff_mask] - inner_radius) / (radius - inner_radius)
                stamp[falloff_mask] = 0.5 * (1.0 + np.cos(t * np.pi))

        # Convert to uint8
        stamp = (stamp * 255).astype(np.uint8)

        # Cache the stamp
        if len(self.brush_cache) < 100:  # Limit cache size
            self.brush_cache[cache_key] = stamp

        return stamp

    def stamp_at(self, canvas_data: NDArray[Any], x: int, y: int, pressure: float = 1.0) -> None:
        """
        Apply brush stamp at position

        Args:
            canvas_data: Canvas pixel data (RGBA)
            x, y: Position
            pressure: Pressure value (0-1)
        """
        # Calculate actual size and opacity with pressure
        size = self.settings.size
        opacity = self.settings.opacity * self.settings.flow

        if self.settings.pressure_size:
            size *= pressure
        if self.settings.pressure_opacity:
            opacity *= pressure

        # Get brush stamp
        stamp = self.create_brush_stamp(size, self.settings.hardness)
        stamp_h, stamp_w = stamp.shape

        # Calculate bounds
        x_start = x - stamp_w // 2
        y_start = y - stamp_h // 2
        x_end = x_start + stamp_w
        y_end = y_start + stamp_h

        # Clip to canvas bounds
        canvas_h, canvas_w = canvas_data.shape[:2]

        # Source bounds (stamp)
        src_x_start = max(0, -x_start)
        src_y_start = max(0, -y_start)
        src_x_end = stamp_w - max(0, x_end - canvas_w)
        src_y_end = stamp_h - max(0, y_end - canvas_h)

        # Destination bounds (canvas)
        dst_x_start = max(0, x_start)
        dst_y_start = max(0, y_start)
        dst_x_end = min(canvas_w, x_end)
        dst_y_end = min(canvas_h, y_end)

        # Check if stamp is within bounds
        if (
            dst_x_end <= dst_x_start
            or dst_y_end <= dst_y_start
            or src_x_end <= src_x_start
            or src_y_end <= src_y_start
        ):
            return

        # Get regions
        stamp_region = stamp[src_y_start:src_y_end, src_x_start:src_x_end]
        canvas_region = canvas_data[dst_y_start:dst_y_end, dst_x_start:dst_x_end]

        # Apply brush
        stamp_alpha = (stamp_region / 255.0 * opacity).astype(np.float32)

        # Expand dimensions for broadcasting
        stamp_alpha = stamp_alpha[:, :, np.newaxis]

        # Blend with canvas
        color = np.array(self.settings.color[:3], dtype=np.float32)

        # Alpha blending
        canvas_region[:, :, :3] = canvas_region[:, :, :3] * (1 - stamp_alpha) + color * stamp_alpha

        # Update alpha channel
        new_alpha = canvas_region[:, :, 3] + stamp_alpha[:, :, 0] * self.settings.color[3]
        canvas_region[:, :, 3] = np.minimum(new_alpha, 255)

    def stroke_to(self, canvas_data: NDArray[Any], x: int, y: int, pressure: float = 1.0) -> None:
        """
        Draw stroke from last point to current point

        Args:
            canvas_data: Canvas pixel data
            x, y: Current position
            pressure: Current pressure
        """
        if self.last_point is None:
            # First point
            self.stamp_at(canvas_data, x, y, pressure)
            self.last_point = (x, y, pressure)
            self.distance_accumulator = 0.0
            return

        # Calculate distance
        last_x, last_y, last_pressure = self.last_point
        dx = x - last_x
        dy = y - last_y
        distance = math.sqrt(dx * dx + dy * dy)

        if distance < 0.001:  # Too close
            return

        # Calculate spacing
        spacing = self.settings.size * self.settings.spacing

        # Add to accumulator
        self.distance_accumulator += distance

        # Apply stamps along the line
        if self.distance_accumulator >= spacing:
            steps = int(self.distance_accumulator / spacing)

            for i in range(steps):
                t = (i + 1) / (steps + 1)

                # Interpolate position
                stamp_x = int(last_x + dx * t)
                stamp_y = int(last_y + dy * t)

                # Interpolate pressure
                stamp_pressure = last_pressure + (pressure - last_pressure) * t

                # Apply stamp
                self.stamp_at(canvas_data, stamp_x, stamp_y, stamp_pressure)

            # Update accumulator
            self.distance_accumulator = self.distance_accumulator % spacing

        # Update last point
        self.last_point = (x, y, pressure)

    def start_stroke(
        self, canvas_data: NDArray[Any], x: int, y: int, pressure: float = 1.0
    ) -> None:
        """Start a new stroke"""
        self.last_point = None
        self.distance_accumulator = 0.0
        self.stroke_to(canvas_data, x, y, pressure)

    def end_stroke(self) -> None:
        """End current stroke"""
        self.last_point = None
        self.distance_accumulator = 0.0

    def set_color(self, color: tuple[int, int, int, int]) -> None:
        """Set brush color"""
        self.settings.color = color

    def set_size(self, size: float) -> None:
        """Set brush size"""
        self.settings.size = max(1.0, min(500.0, size))

    def set_opacity(self, opacity: float) -> None:
        """Set brush opacity"""
        self.settings.opacity = max(0.0, min(1.0, opacity))

    def set_hardness(self, hardness: float) -> None:
        """Set brush hardness"""
        self.settings.hardness = max(0.0, min(1.0, hardness))


class EraserTool(BrushTool):
    """Eraser tool (removes pixels)"""

    def stamp_at(self, canvas_data: NDArray[Any], x: int, y: int, pressure: float = 1.0) -> None:
        """Erase at position"""
        # Calculate actual size with pressure
        size = self.settings.size
        if self.settings.pressure_size:
            size *= pressure

        opacity = self.settings.opacity
        if self.settings.pressure_opacity:
            opacity *= pressure

        # Get brush stamp
        stamp = self.create_brush_stamp(size, self.settings.hardness)
        stamp_h, stamp_w = stamp.shape

        # Calculate bounds
        x_start = x - stamp_w // 2
        y_start = y - stamp_h // 2
        x_end = x_start + stamp_w
        y_end = y_start + stamp_h

        # Clip to canvas bounds
        canvas_h, canvas_w = canvas_data.shape[:2]

        # Source bounds (stamp)
        src_x_start = max(0, -x_start)
        src_y_start = max(0, -y_start)
        src_x_end = stamp_w - max(0, x_end - canvas_w)
        src_y_end = stamp_h - max(0, y_end - canvas_h)

        # Destination bounds (canvas)
        dst_x_start = max(0, x_start)
        dst_y_start = max(0, y_start)
        dst_x_end = min(canvas_w, x_end)
        dst_y_end = min(canvas_h, y_end)

        # Check if stamp is within bounds
        if (
            dst_x_end <= dst_x_start
            or dst_y_end <= dst_y_start
            or src_x_end <= src_x_start
            or src_y_end <= src_y_start
        ):
            return

        # Get regions
        stamp_region = stamp[src_y_start:src_y_end, src_x_start:src_x_end]
        canvas_region = canvas_data[dst_y_start:dst_y_end, dst_x_start:dst_x_end]

        # Apply eraser (reduce alpha)
        erase_amount = (stamp_region / 255.0 * opacity).astype(np.float32)

        # Reduce alpha channel
        new_alpha = canvas_region[:, :, 3] * (1 - erase_amount)
        canvas_region[:, :, 3] = new_alpha.astype(np.uint8)
