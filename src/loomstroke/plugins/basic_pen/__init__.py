from __future__ import annotations

import contextlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ...core.commands import BrushSettings, PaintCommand
from ...core.commands import StrokePoint as CommandStrokePoint
from ...host_api import StrokePoint as HostStrokePoint
from ...plugins.sdk.base import PluginMetadata, PluginType
from ..sdk import ToolDescriptor
from .pen_tool import create_plugin


@dataclass
class PenSettings:
    size: float = 5.0
    opacity: float = 1.0
    hardness: float = 0.8
    spacing: float = 0.1
    flow: float = 1.0
    tone_range: float = 1.0
    tone_gamma: float = 0.6
    color: tuple[int, int, int] = (0, 0, 0)


class BasicPenPlugin:
    """Legacy-compatible basic pen plugin."""

    def __init__(self) -> None:
        self.metadata = PluginMetadata(
            id="basic_pen",
            name="Basic Pen Tool",
            version="1.0.0",
            description="Legacy compatible basic pen tool",
            author="LoomStroke Team",
            types=[PluginType.TOOL],
        )
        self._descriptor = ToolDescriptor(
            id="basic_pen",
            display_name="Pen",
            cursor="crosshair",
            category="brush",
        )
        self.host_api = None
        self.settings = PenSettings()
        self.stroke_points: list[HostStrokePoint] = []
        self._pending_commands: list[PaintCommand] = []
        self.is_drawing = False

    # --- Descriptor and lifecycle ---------------------------------------------
    def descriptor(self) -> ToolDescriptor:
        return self._descriptor

    def activate(self, host_api) -> None:
        self.host_api = host_api

    def enable(self) -> None:
        self.is_drawing = False
        if self.host_api and getattr(self.host_api, "events", None):
            emitter = getattr(self.host_api.events, "emit", None)
            if callable(emitter):
                emitter("tool.changed", {"tool": "basic_pen"})

    def disable(self) -> None:
        self.is_drawing = False
        self.stroke_points.clear()

    def deactivate(self) -> None:
        if self.host_api and getattr(self.host_api, "settings", None):
            setter = getattr(self.host_api.settings, "set", None)
            if callable(setter):
                setter("tools.basic_pen", self._settings_payload())
        self.host_api = None
        self.is_drawing = False
        self.stroke_points.clear()

    # --- Settings helpers ------------------------------------------------------
    def get_settings(self) -> PenSettings:
        return PenSettings(**vars(self.settings))

    def set_settings(self, settings: PenSettings) -> None:
        self.settings = PenSettings(**vars(settings))

    def _settings_payload(self) -> dict[str, float | tuple[int, int, int]]:
        return {
            "size": self.settings.size,
            "opacity": self.settings.opacity,
            "hardness": self.settings.hardness,
            "spacing": self.settings.spacing,
            "flow": self.settings.flow,
            "tone_range": self.settings.tone_range,
            "tone_gamma": self.settings.tone_gamma,
            "color": tuple(self.settings.color),
        }

    # --- Pointer events --------------------------------------------------------
    def on_pointer_down(
        self,
        pt: HostStrokePoint,
        *,
        alt: bool = False,
        shift: bool = False,
        ctrl: bool = False,
        meta: bool = False,
    ) -> None:
        del alt, shift, ctrl, meta
        self.is_drawing = True
        self.stroke_points = [pt]
        canvas = self._canvas()
        if canvas and hasattr(canvas, "begin_transaction"):
            with contextlib.suppress(Exception):
                canvas.begin_transaction("Pen Stroke")

    def on_pointer_move(self, points: Iterable[HostStrokePoint]) -> None:
        if not self.is_drawing:
            return
        self.stroke_points.extend(points)

    def on_pointer_up(self, pt: HostStrokePoint) -> None:
        if not self.is_drawing:
            return
        self.stroke_points.append(pt)
        command = self._build_paint_command(self.stroke_points)
        self._pending_commands.append(command)
        canvas = self._canvas()
        if canvas:
            with contextlib.suppress(Exception):
                canvas.submit(command, merge_key="basic_pen_stroke")
            if hasattr(canvas, "end_transaction"):
                with contextlib.suppress(Exception):
                    canvas.end_transaction()
        self.stroke_points.clear()
        self.is_drawing = False

    def on_key(self, key: str, pressed: bool) -> bool:
        if not pressed:
            return False
        if key == "[":
            self.settings.size = max(1.0, self.settings.size - 1.0)
            return True
        if key == "]":
            self.settings.size = min(512.0, self.settings.size + 1.0)
            return True
        return False

    # --- Command handling ------------------------------------------------------
    def build_commands(self) -> list[PaintCommand]:
        commands = list(self._pending_commands)
        if not commands and self.stroke_points:
            commands.append(self._build_paint_command(self.stroke_points))
        self._pending_commands.clear()
        self.stroke_points.clear()
        return commands

    # --- Utility ----------------------------------------------------------------
    def _canvas(self):
        if self.host_api and getattr(self.host_api, "canvas", None):
            return self.host_api.canvas
        return None

    def _build_paint_command(self, points: Sequence[HostStrokePoint]) -> PaintCommand:
        cmd_points = [
            CommandStrokePoint(
                x=p.pos[0],
                y=p.pos[1],
                pressure=p.pressure if p.pressure is not None else 1.0,
            )
            for p in points
        ]
        active_color = self._resolve_active_color()
        brush = BrushSettings(
            size=float(self.settings.size),
            opacity=float(self.settings.opacity),
            hardness=float(self.settings.hardness),
            spacing=float(self.settings.spacing),
            flow=float(self.settings.flow),
            tone_range=float(self.settings.tone_range),
            tone_gamma=float(self.settings.tone_gamma),
            color=active_color,
        )
        return PaintCommand(cmd_points, brush)

    def create_options_widget(self):
        return None


__all__ = [
    "BasicPenPlugin",
    "PenSettings",
    "create_plugin",
]
