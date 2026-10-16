"""
Basic Eraser tool plugin implementation.

Provides a pressure-sensitive eraser tool that generates EraseCommands.
Per Master.md, this plugin does not directly manipulate pixels.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...core.commands import Command
    from ...host_api import HostAPI, StrokePoint

import contextlib

from ...core.commands import BrushSettings as CommandBrushSettings
from ...core.commands import EraseCommand
from ...core.commands import StrokePoint as CommandStrokePoint
from ...platform import get_settings_manager
from ...plugins.sdk.base import PluginMetadata, PluginType
from ...plugins.sdk.drawing_assist import ToolAssistBridge
from ...plugins.sdk.tool import (
    ContextToolbarSpec,
    PropertySpec,
    PropertyType,
    ToolDescriptor,
    ToolMetadata,
    ToolPreset,
    ToolSettings,
)
from .eraser_options_panel import EraserOptionsPanel
from .eraser_settings import EraserSettings as EraserSettings  # noqa: PLC0414
from .vector_eraser_controller import VectorEraserController

logger = logging.getLogger(__name__)


def _ts(text: str) -> str:
    from loomstroke.ui.translations import translated_literal_domain  # noqa: PLC0415

    return translated_literal_domain("BasicEraser", text, use_fallback=True)


class BasicEraserPlugin:
    """
    Basic eraser tool plugin implementation.

    This plugin handles eraser input and generates EraseCommands
    for the canvas. It does not directly manipulate pixels.
    """

    def __init__(self):
        """Initialize the eraser plugin."""
        self.host_api: HostAPI | None = None
        self.settings = EraserSettings()
        self.is_erasing = False
        self.stroke_points: list[StrokePoint] = []
        self.last_point: StrokePoint | None = None
        self.stroke_id = 0
        self._assist = ToolAssistBridge("basic_eraser")
        self._last_submitted_index = 0
        self._last_submit_time = 0.0
        self._pending_commands: list[Command] = []
        self._transaction_active = False
        self._end_generation: int = 0
        self._vector_eraser = VectorEraserController(self)

        # Metadata for registry
        self.metadata = PluginMetadata(
            id="loomstroke.basic_eraser",
            name=_ts("Basic Eraser"),
            version="1.0.0",
            description=_ts("Basic pressure-sensitive eraser tool"),
            author="LoomStroke Team",
            types=[PluginType.TOOL],
        )

    def descriptor(self) -> ToolDescriptor:
        """Get the tool descriptor."""
        return ToolDescriptor(
            id="basic_eraser",
            display_name=_ts("Eraser"),
            cursor="eraser",
            category="draw",
            icon_svg=self._get_icon_svg(),
            shortcut="E",
            secondary_shortcut="Shift+E",
            secondary_shortcut_description=_ts("Secondary eraser binding"),
            order=2,
        )

    def tool_metadata(self) -> ToolMetadata:
        """Return ToolMetadata consumed by the dockable panels."""

        descriptor = self.descriptor()
        return ToolMetadata(
            tool_id=descriptor.id,
            display_name=descriptor.display_name,
            category=descriptor.category or "draw",
            tags=["eraser", "cleanup", "brush"],
            icon_svg=descriptor.icon_svg,
            group_id="eraser.basic",
            group_label=_ts("Erasers"),
            group_order=20,
            properties=[
                PropertySpec(
                    key="vector_erase_mode",
                    display_name=_ts("Vector Erase Mode"),
                    type=PropertyType.ENUM,
                    enum_items=["Touched Area", "Whole Stroke", "To Intersection"],
                    default_value=self.settings.vector_erase_mode,
                ),
                PropertySpec(
                    key="size",
                    display_name=_ts("Size"),
                    type=PropertyType.FLOAT,
                    min_value=1.0,
                    max_value=200.0,
                    default_value=self.settings.size,
                    step=1.0,
                    is_common=True,
                ),
                PropertySpec(
                    key="opacity",
                    display_name=_ts("Strength"),
                    type=PropertyType.FLOAT,
                    min_value=0.0,
                    max_value=1.0,
                    default_value=self.settings.opacity,
                    step=0.05,
                    is_common=True,
                ),
                PropertySpec(
                    key="hardness",
                    display_name=_ts("Hardness"),
                    type=PropertyType.FLOAT,
                    min_value=0.0,
                    max_value=1.0,
                    default_value=self.settings.hardness,
                    step=0.05,
                    is_common=True,
                ),
                PropertySpec(
                    key="spacing",
                    display_name=_ts("Spacing"),
                    type=PropertyType.FLOAT,
                    min_value=0.01,
                    max_value=1.0,
                    default_value=self.settings.spacing,
                    step=0.01,
                    is_common=True,
                ),
                PropertySpec(
                    key="anti_alias",
                    display_name=_ts("Anti-alias"),
                    type=PropertyType.BOOL,
                    default_value=self.settings.anti_alias,
                ),
            ],
            presets=[
                ToolPreset(
                    name=_ts("Soft Erase"),
                    values={
                        "size": 40.0,
                        "opacity": 0.5,
                        "hardness": 0.2,
                        "spacing": 0.2,
                        "anti_alias": True,
                    },
                ),
                ToolPreset(
                    name=_ts("Edge Cleanup"),
                    values={
                        "size": 14.0,
                        "opacity": 1.0,
                        "hardness": 0.85,
                        "spacing": 0.05,
                        "anti_alias": True,
                    },
                ),
            ],
        )

    def context_toolbar_spec(self) -> ContextToolbarSpec | None:
        return ContextToolbarSpec(
            show_trigger="pointer_up",
            property_keys=["size", "opacity", "hardness"],
            anchor_mode="stroke_bbox",
            mode="settings",
        )

    def activate(self, host: HostAPI) -> None:
        """
        Activate the plugin with host API.

        Args:
            host: The host API interface
        """
        self.host_api = host
        if not self._tool_registered(host):
            register_tool = getattr(host, "register_tool", None)
            if callable(register_tool):
                register_tool(self)

        # Load saved settings (migrating legacy per-key format if present)
        saved_settings = host.settings.get("tools.basic_eraser", {})
        if not isinstance(saved_settings, dict):
            logger.debug(
                "tools.basic_eraser has non-dict value (%s); ignoring and using defaults",
                type(saved_settings).__name__,
            )
            saved_settings = {}
        if not saved_settings:
            migrated = self._migrate_legacy_settings(host)
            if migrated:
                saved_settings = migrated
                host.settings.set("tools.basic_eraser", migrated)
        if saved_settings:
            self._load_settings(saved_settings)

    def enable(self) -> None:
        """Enable the eraser tool (when selected)."""
        if self.host_api:
            self.host_api.events.emit("tool.changed", {"tool": "basic_eraser"})
            self.host_api.ui.show_message("Eraser Tool", 1000)

    def disable(self) -> None:
        """Disable the eraser tool (when switching away)."""
        self._vector_eraser.cancel()
        try:
            if self.is_erasing:
                self._finish_stroke(flush_direct=True)
                self._end_transaction()
                if self.host_api:
                    self.host_api.input.release_capture("basic_eraser")
        finally:
            self.stroke_points.clear()
            self.is_erasing = False
            self.last_point = None

    def deactivate(self) -> None:
        """Deactivate the plugin (unload)."""
        if self.host_api:
            self.host_api.settings.set("tools.basic_eraser", self._save_settings())
            unregister = getattr(self.host_api, "unregister_tool", None)
            if callable(unregister):
                unregister(self.descriptor().id)

        self.host_api = None

    def _tool_registered(self, host: HostAPI) -> bool:
        tool_id = self.descriptor().id
        manager = getattr(host, "_tool_manager", None)
        if manager and hasattr(manager, "get_registered_tools"):
            try:
                for descriptor in manager.get_registered_tools():
                    if descriptor.id == tool_id:
                        return True
            except Exception:
                logger.debug("get_registered_tools failed", exc_info=True)
        pending = getattr(host, "_pending_tools", None)
        if isinstance(pending, list):
            for tool in pending:
                if tool is self:
                    return True
                descriptor_fn = getattr(tool, "descriptor", None)
                if callable(descriptor_fn):
                    try:
                        if descriptor_fn().id == tool_id:
                            return True
                    except Exception:
                        continue
        return False

    def _begin_transaction(self) -> None:
        if not self.host_api or self._transaction_active:
            return
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None or not hasattr(canvas, "begin_transaction"):
            return
        try:
            canvas.begin_transaction("Eraser Stroke")
        except Exception:
            return
        self._end_generation += 1
        self._transaction_active = True

    def _end_transaction(self) -> None:
        if not self.host_api or not self._transaction_active:
            return
        self._end_generation += 1
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None or not hasattr(canvas, "end_transaction"):
            self._transaction_active = False
            return
        with contextlib.suppress(Exception):
            canvas.end_transaction()
        self._transaction_active = False

    def _schedule_end_transaction(self) -> None:
        if not self._transaction_active or not self.host_api:
            return
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None or not hasattr(canvas, "end_transaction"):
            self._transaction_active = False
            return
        try:
            from PySide6.QtCore import QCoreApplication, QTimer  # noqa: PLC0415
        except Exception:
            self._end_transaction()
            return

        if QCoreApplication.instance() is None:
            self._end_transaction()
            return

        gen = self._end_generation

        def _end() -> None:
            if gen != self._end_generation:
                return
            if not self._transaction_active:
                return
            with contextlib.suppress(Exception):
                canvas.end_transaction()
            self._transaction_active = False

        QTimer.singleShot(0, _end)

    def on_pointer_down(
        self,
        pt: StrokePoint,
        *,
        alt: bool = False,
        shift: bool = False,
        ctrl: bool = False,
        meta: bool = False,
    ) -> None:
        """
        Handle pointer down event.

        Args:
            pt: Stroke point with position and pressure
            alt: Alt/Option key pressed
            shift: Shift key pressed
            ctrl: Control key pressed
            meta: Meta/Windows/Command key pressed
        """
        if not self.host_api:
            return

        if self._vector_eraser.down(pt):
            return
        if self.is_erasing:
            self._finish_stroke(flush_direct=True)
        if self._transaction_active:
            self._end_transaction()
        if self._pending_commands:
            # Flush any commands left over from a prior stroke whose build_commands
            # was never consumed by ToolManager before this new stroke begins.
            self._submit_commands_direct(list(self._pending_commands))
        self._pending_commands.clear()

        pt = self._assist.begin(self.host_api, pt)
        self.is_erasing = True
        self.stroke_points = [pt]
        self.last_point = pt
        self._last_submitted_index = len(self.stroke_points)
        self._last_submit_time = time.monotonic()
        self.stroke_id += 1

        self._begin_transaction()
        self.host_api.input.pointer_capture("basic_eraser")

    def on_pointer_move(self, pts: Iterable[StrokePoint]) -> None:
        """
        Handle pointer move events.

        Args:
            pts: Sequence of stroke points since last move
        """
        if self._vector_eraser.active:
            self._vector_eraser.move(pts)
            return
        if not self.is_erasing or not self.host_api:
            return

        pts = self._assist.points(pts)
        for pt in pts:
            if self._should_add_point(pt):
                self.stroke_points.append(pt)
                self.last_point = pt
        self._trim_stroke_points()

        # Submit incremental command for real-time feedback
        if len(self.stroke_points) > 1:
            self._submit_incremental_stroke()

    def on_pointer_up(self, pt: StrokePoint) -> None:
        """
        Handle pointer up event.

        Args:
            pt: Final stroke point
        """
        if self._vector_eraser.active:
            self._vector_eraser.up(pt)
            return
        if not self.is_erasing or not self.host_api:
            return

        pt = self._assist.point(pt)
        if self._should_add_point(pt):
            self.stroke_points.append(pt)

        self._finish_stroke()
        self._assist.end()

        self.host_api.input.release_capture("basic_eraser")
        self._schedule_end_transaction()

        self.is_erasing = False
        self.last_point = pt

    def on_pointer_hover(self, pt: StrokePoint) -> None:
        """Handle pointer hover."""
        pass

    def on_key(self, key: str, pressed: bool) -> bool:
        """
        Handle keyboard input.

        Args:
            key: Key name
            pressed: True for key down, False for key up

        Returns:
            True if handled
        """
        if not pressed:
            return False
        if key.lower() in {"escape", "esc"} and (
            self._vector_eraser.active or self._vector_eraser._worker is not None
        ):
            self._vector_eraser.cancel()
            return True

        if key == "[":
            self.settings.size = max(1.0, self.settings.size - 2.0)
            return True
        elif key == "]":
            self.settings.size = min(200.0, self.settings.size + 2.0)
            return True

        return False

    def on_wheel(self, delta: float, pt: StrokePoint) -> bool:
        """
        Handle mouse wheel events.

        Args:
            delta: Wheel delta
            pt: Pointer position

        Returns:
            True if handled
        """
        self.settings.size = max(1.0, min(200.0, self.settings.size + delta * 2))
        return True

    def build_commands(self) -> list[Command]:
        """Return queued commands for submission via the tool manager."""
        if self._pending_commands:
            commands = list(self._pending_commands)
            self._pending_commands.clear()
            return commands

        if self.stroke_points and not self.is_erasing:
            command = self._build_command(
                self.stroke_points,
                merge_key=f"eraser_stroke_{self.stroke_id}",
                max_points=512,
            )
            if command:
                return [command]

        return []

    def get_settings(self) -> EraserSettings:
        """Get current tool settings."""
        return self.settings

    def set_settings(self, settings: ToolSettings | dict[str, Any]) -> None:
        """Update tool settings."""
        if isinstance(settings, dict):
            if "vector_erase_mode" in settings:
                self.set_setting("vector_erase_mode", settings["vector_erase_mode"])
            if "size" in settings:
                self.settings.size = float(settings["size"])
            if "opacity" in settings:
                self.settings.opacity = float(settings["opacity"])
            if "hardness" in settings:
                self.settings.hardness = float(settings["hardness"])
            if "spacing" in settings:
                self.settings.spacing = float(settings["spacing"])
            if "anti_alias" in settings:
                self.settings.anti_alias = bool(settings["anti_alias"])
            return
        if isinstance(settings, EraserSettings):
            self.settings = settings

    def set_setting(self, key: str, value: Any) -> None:
        """Update a single tool setting."""
        if key == "vector_erase_mode":
            if value in {"Touched Area", "Whole Stroke", "To Intersection"}:
                self._vector_eraser.cancel()
                self.settings.vector_erase_mode = value
            return
        if key == "size":
            self.settings.size = float(value)
        elif key == "opacity":
            self.settings.opacity = float(value)
        elif key == "hardness":
            self.settings.hardness = float(value)
        elif key == "spacing":
            self.settings.spacing = float(value)
        elif key == "anti_alias":
            self.settings.anti_alias = bool(value)

    def _trim_stroke_points(self) -> None:
        max_points = 2048
        total = len(self.stroke_points)
        if total <= max_points:
            return

        overlap = 32
        if self._last_submitted_index > overlap:
            drop = self._last_submitted_index - overlap
        else:
            drop = total - max_points

        drop = max(0, min(drop, total - 2))
        if drop <= 0:
            return

        self.stroke_points = self.stroke_points[drop:]
        self._last_submitted_index = max(0, self._last_submitted_index - drop)
        self._last_submitted_index = min(self._last_submitted_index, len(self.stroke_points))

    def create_options_widget(self) -> Any:
        """Create options widget for the eraser tool."""

        settings_manager = get_settings_manager()
        panel = EraserOptionsPanel(settings_manager)

        # Connect panel changes to update our settings
        def update_settings(data: dict) -> None:
            if "size" in data:
                self.settings.size = float(data["size"])
            if "opacity" in data:
                self.settings.opacity = float(data["opacity"])
            if "hardness" in data:
                self.settings.hardness = float(data["hardness"])

        panel.changed.connect(update_settings)
        return panel

    # ---- Private methods ----

    def _queue_command(self, command: Command) -> None:
        """Store a command to be flushed by the tool manager."""
        self._pending_commands.append(command)

    def _submit_commands_direct(self, commands: list[Command]) -> bool:
        """Fallback submission when the manager cannot flush pending commands."""
        if not commands:
            return True
        if not self.host_api or not hasattr(self.host_api, "canvas"):
            return False

        merge_key = "basic_eraser_stroke"
        canvas = self.host_api.canvas
        all_ok = True
        for command in commands:
            try:
                ok = canvas.submit(command, merge_key=merge_key)
            except Exception:
                logger.debug("canvas.submit raised for EraseCommand", exc_info=True)
                all_ok = False
                continue
            if ok is False:
                all_ok = False

        self._pending_commands.clear()
        return all_ok

    def _downsample_points(
        self,
        points: list[StrokePoint],
        *,
        max_points: int = 2048,
    ) -> list[StrokePoint]:
        if len(points) <= max_points:
            return list(points)
        step = max(1, len(points) // max_points)
        sampled = list(points[::step])
        if sampled[-1] is not points[-1]:
            sampled.append(points[-1])
        return sampled

    def _build_command(
        self,
        points: list[StrokePoint],
        *,
        merge_key: str | None = None,
        max_points: int = 2048,
    ) -> Command | None:
        if len(points) < 2:
            return None
        sampled = self._downsample_points(points, max_points=max_points)
        cmd_points = [
            CommandStrokePoint(
                x=pt.pos[0],
                y=pt.pos[1],
                pressure=pt.pressure,
                tilt_x=pt.tilt[0] if pt.tilt else 0.0,
                tilt_y=pt.tilt[1] if pt.tilt else 0.0,
                timestamp=pt.time or 0.0,
            )
            for pt in sampled
        ]
        brush = CommandBrushSettings(
            size=self.settings.size,
            opacity=self.settings.opacity,
            hardness=self.settings.hardness,
            spacing=self.settings.spacing,
            flow=1.0,
            color=(255, 255, 255),
            anti_alias=self.settings.anti_alias,
        )
        layer_id = None
        try:
            if self.host_api and hasattr(self.host_api, "canvas"):
                canvas = self.host_api.canvas
                if hasattr(canvas, "current_layer"):
                    layer = canvas.current_layer()
                    layer_id = getattr(layer, "id", None)
        except Exception:
            layer_id = None
        return EraseCommand(cmd_points, brush, layer_id=layer_id, merge_key=merge_key)

    def _should_add_point(self, pt: StrokePoint) -> bool:
        """Check if point should be added based on spacing."""
        if not self.last_point:
            return True

        dx = pt.pos[0] - self.last_point.pos[0]
        dy = pt.pos[1] - self.last_point.pos[1]
        distance = (dx * dx + dy * dy) ** 0.5

        min_distance = self.settings.size * self.settings.spacing
        return distance >= min_distance

    def _submit_incremental_stroke(self) -> None:
        """Queue incremental stroke segments for flushing."""
        if len(self.stroke_points) <= 1:
            return

        now = time.monotonic()
        if now - self._last_submit_time < 0.01:
            return

        start_index = max(self._last_submitted_index - 1, 0)
        if start_index >= len(self.stroke_points) - 1:
            return

        segment = self.stroke_points[start_index:]
        commands = self._assist.commands(
            segment,
            lambda pts: self._build_command(
                pts, merge_key=f"eraser_stroke_{self.stroke_id}", max_points=128
            ),
        )
        if not commands:
            return

        for command in commands:
            self._queue_command(command)
        self._last_submitted_index = len(self.stroke_points)
        self._last_submit_time = now

    def _finish_stroke(self, *, flush_direct: bool = False) -> None:
        """Finalize the current stroke, queuing any remaining segments."""
        if not self.stroke_points:
            return

        commands: list[Command] = []
        if self._last_submitted_index < len(self.stroke_points):
            start_index = max(self._last_submitted_index - 1, 0)
            segment = self.stroke_points[start_index:]
            commands.extend(
                self._assist.commands(
                    segment,
                    lambda pts: self._build_command(
                        pts, merge_key=f"eraser_stroke_{self.stroke_id}", max_points=512
                    ),
                )
            )

        if flush_direct:
            submitted = self._submit_commands_direct(commands)
            if not submitted:
                for command in commands:
                    self._queue_command(command)
        else:
            for command in commands:
                self._queue_command(command)

        self.stroke_points.clear()
        self._last_submitted_index = 0
        self._last_submit_time = 0.0

    def _save_settings(self) -> dict[str, Any]:
        """Save settings to dictionary."""
        return {
            "vector_erase_mode": self.settings.vector_erase_mode,
            "size": self.settings.size,
            "opacity": self.settings.opacity,
            "hardness": self.settings.hardness,
            "spacing": self.settings.spacing,
            "anti_alias": self.settings.anti_alias,
        }

    def _load_settings(self, data: dict[str, Any]) -> None:
        """Load settings from dictionary."""
        self.settings.size = data.get("size", 20.0)
        self.settings.opacity = data.get("opacity", 1.0)
        self.settings.hardness = data.get("hardness", 0.5)
        self.settings.spacing = data.get("spacing", 0.1)
        self.settings.anti_alias = data.get("anti_alias", True)
        self.set_setting("vector_erase_mode", data.get("vector_erase_mode", "Touched Area"))

    def _migrate_legacy_settings(self, host: HostAPI) -> dict[str, Any] | None:
        """One-shot migration: eraser/size, eraser/strength, eraser/hardness → dict."""
        legacy_size = host.settings.get("eraser/size", None)
        legacy_strength = host.settings.get("eraser/strength", None)
        legacy_hardness = host.settings.get("eraser/hardness", None)
        if legacy_size is None and legacy_strength is None and legacy_hardness is None:
            return None
        migrated: dict[str, Any] = {}
        if legacy_size is not None:
            migrated["size"] = float(legacy_size)
        if legacy_strength is not None:
            migrated["opacity"] = float(legacy_strength) / 100.0
        if legacy_hardness is not None:
            migrated["hardness"] = float(legacy_hardness) / 100.0
        return migrated

    def _get_icon_svg(self) -> str:
        """Get SVG icon for the eraser tool."""
        return """
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor">
            <path d="M19 20H5.5L13 7l6 13z"/>
            <path d="M13 7l-1.5 3.5"/>
            <line x1="11" y1="13" x2="16" y2="13"/>
        </svg>
        """


def create_plugin() -> BasicEraserPlugin:
    """
    Factory function to create the plugin instance.

    Returns:
        New BasicEraserPlugin instance
    """
    return BasicEraserPlugin()
