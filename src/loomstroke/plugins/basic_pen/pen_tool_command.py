"""
Command-based pen tool implementation.

This properly submits paint commands to the Host instead of
directly manipulating pixels, following Master.md principles.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor

from loomstroke.core.commands import BrushSettings, PaintCommand
from loomstroke.host_api import StrokePoint

from ..sdk import PluginBase, ToolDescriptor
from ..sdk.tool import ToolBase

logger = logging.getLogger(__name__)


@dataclass
class PenSettings:
    """Pen tool settings."""

    size: float = 5.0
    opacity: float = 1.0
    hardness: float = 1.0
    spacing: float = 0.1
    color: QColor = QColor(0, 0, 0)  # Black  # noqa: RUF009
    flow: float = 1.0
    tone_range: float = 1.0
    tone_gamma: float = 0.6


class CommandPenTool(ToolBase):
    """
    Command-based pen tool.

    Generates PaintCommands and submits them to Host,
    following Master.md architecture.
    """

    # Tool signals
    settings_changed = Signal(dict)

    def __init__(self, host_api=None):
        """Initialize command pen tool."""
        super().__init__(host_api)

        # Tool settings
        self.settings = PenSettings()

        # Stroke state
        self.stroke_points: list[StrokePoint] = []
        self.is_stroking = False
        self._stroke_id: str | None = None

        logger.info("CommandPenTool initialized")

    def descriptor(self) -> ToolDescriptor:
        """Get tool descriptor."""
        return ToolDescriptor(
            id="command_pen",
            display_name="Pen",
            category="painting",
            icon_svg=None,
            shortcut="B",
            cursor="crosshair",
        )

    def get_settings(self) -> dict[str, Any]:
        """Get current tool settings."""
        return {
            "size": self.settings.size,
            "opacity": self.settings.opacity,
            "hardness": self.settings.hardness,
            "spacing": self.settings.spacing,
            "color": self.settings.color.name(),
            "flow": self.settings.flow,
            "tone_range": self.settings.tone_range,
            "tone_gamma": self.settings.tone_gamma,
        }

    def set_settings(self, settings: dict[str, Any]) -> None:
        """Update tool settings."""
        if "size" in settings:
            self.settings.size = max(0.1, min(1000.0, float(settings["size"])))

        if "opacity" in settings:
            self.settings.opacity = max(0.0, min(1.0, float(settings["opacity"])))

        if "hardness" in settings:
            self.settings.hardness = max(0.0, min(1.0, float(settings["hardness"])))

        if "spacing" in settings:
            self.settings.spacing = max(0.01, min(1.0, float(settings["spacing"])))

        if "color" in settings:
            color_value = settings["color"]
            if isinstance(color_value, str):
                self.settings.color = QColor(color_value)
            elif isinstance(color_value, (list, tuple)) and len(color_value) >= 3:
                self.settings.color = QColor(*color_value[:4])  # Support RGBA

        if "flow" in settings:
            self.settings.flow = max(0.0, min(1.0, float(settings["flow"])))

        if "tone_range" in settings:
            self.settings.tone_range = max(0.0, min(1.0, float(settings["tone_range"])))

        if "tone_gamma" in settings:
            self.settings.tone_gamma = max(0.1, min(2.0, float(settings["tone_gamma"])))

        # Emit settings changed signal
        self.settings_changed.emit(self.get_settings())

        logger.debug(f"Pen settings updated: {settings}")

    def on_press(self, point: StrokePoint) -> bool:
        """Handle pointer press event - start stroke."""
        logger.debug(f"Pen press at {point.pos}")

        self.is_stroking = True
        self.stroke_points = [point]
        self._stroke_id = f"pen:{time.time_ns()}"

        # Start transaction in Host
        if self.host_api and self.host_api.canvas:
            self.host_api.canvas.begin_transaction("Pen Stroke")

        return True

    def on_move(self, points: list[StrokePoint]) -> bool:
        """Handle pointer move event - continue stroke."""
        if not self.is_stroking or not points:
            return False

        # Add points to stroke
        self.stroke_points.extend(points)

        # Submit incremental paint command
        # Send small chunks to reduce latency
        chunk_size = min(16, len(points))  # Balance between performance and responsiveness
        chunk = points[-chunk_size:]

        cmd = PaintCommand(
            points=chunk,
            brush=self._create_brush_settings(),
            layer_id=None,  # Use active layer
            merge_key=self._stroke_id,
        )

        # Submit command to Host
        if self.host_api and self.host_api.canvas:
            self.host_api.canvas.submit(cmd, merge_key=self._stroke_id)

        return True

    def on_release(self, point: StrokePoint) -> bool:
        """Handle pointer release event - end stroke."""
        if not self.is_stroking:
            return False

        # Add final point
        self.stroke_points.append(point)

        # Submit final segment
        if len(self.stroke_points) >= 2:
            final_segment = [self.stroke_points[-2], point]
            cmd = PaintCommand(
                points=final_segment,
                brush=self._create_brush_settings(),
                layer_id=None,
                merge_key=self._stroke_id,
            )

            if self.host_api and self.host_api.canvas:
                self.host_api.canvas.submit(cmd, merge_key=self._stroke_id)

        # End transaction
        if self.host_api and self.host_api.canvas:
            self.host_api.canvas.end_transaction()

        # Reset state
        self.is_stroking = False
        self.stroke_points.clear()
        self._stroke_id = None

        logger.debug("Pen stroke ended")
        return True

    def _create_brush_settings(self) -> BrushSettings:
        """Create brush settings from current tool settings."""
        return BrushSettings(
            size=self.settings.size,
            opacity=self.settings.opacity,
            hardness=self.settings.hardness,
            spacing=self.settings.spacing,
            flow=self.settings.flow,
            tone_range=self.settings.tone_range,
            tone_gamma=self.settings.tone_gamma,
            color=(
                self.settings.color.red(),
                self.settings.color.green(),
                self.settings.color.blue(),
            ),
        )


class CommandPenPlugin(PluginBase):
    """Command-based pen tool plugin."""

    def __init__(self):
        super().__init__()
        self.pen_tool: CommandPenTool | None = None
        logger.info("CommandPenPlugin initialized")

    def get_plugin_info(self) -> dict[str, Any]:
        """Get plugin information."""
        return {
            "name": "Command Pen Tool",
            "version": "1.0.0",
            "description": "Command-based pen tool following Master.md architecture",
            "author": "LoomStroke Team",
            "category": "tools",
            "dependencies": [],
        }

    async def initialize(self, host_api) -> None:
        """Initialize the plugin."""
        await super().initialize(host_api)

        # Create pen tool instance
        self.pen_tool = CommandPenTool(host_api)

        # Register tool with host
        if hasattr(host_api, "register_tool"):
            host_api.register_tool(self.pen_tool)

        logger.info("CommandPenPlugin initialized and tool registered")

    async def cleanup(self) -> None:
        """Clean up plugin resources."""
        if self.pen_tool and self.host_api and hasattr(self.host_api, "unregister_tool"):
            self.host_api.unregister_tool(self.pen_tool.descriptor().id)

        await super().cleanup()
        logger.info("CommandPenPlugin cleaned up")

    def get_tools(self) -> list[CommandPenTool]:
        """Get available tools from this plugin."""
        return [self.pen_tool] if self.pen_tool else []


# Plugin entry point
def create_plugin() -> CommandPenPlugin:
    """Create and return the plugin instance."""
    return CommandPenPlugin()
