"""Pattern fill tool plugin.

Click to flood fill a contiguous region with a tiled pattern.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from PIL import Image

from ...core.fill.commands_fill import PatternFillCommand
from ...host_api import HostAPI, StrokePoint
from ...plugins.sdk.base import PluginMetadata, PluginType
from ...plugins.sdk.tool import (
    PropertySpec,
    PropertyType,
    ToolDescriptor,
    ToolMetadata,
    ToolSettings,
)
from ..common.tool_icons import resolve_tool_icon_path

if TYPE_CHECKING:
    from ...core.commands import Command

logger = logging.getLogger(__name__)

# Built-in pattern directory
_BUILTIN_PATTERN_DIR = Path(__file__).resolve().parent / "patterns"

# Default built-in patterns (generated procedurally if files missing)
_BUILTIN_PATTERN_NAMES = [
    "Checkerboard",
    "Horizontal Stripes",
    "Vertical Stripes",
    "Diagonal Stripes",
    "Dots",
    "Crosshatch",
]


def _ts(text: str) -> str:
    from loomstroke.ui.translations import translated_literal_domain  # noqa: PLC0415

    return translated_literal_domain("PatternFill", text, use_fallback=True)


_PATTERN_GENERATORS: dict[str, Any] = {}


def _register_pattern(name: str):  # noqa: ANN202
    def decorator(func):  # noqa: ANN001, ANN202
        _PATTERN_GENERATORS[name] = func
        return func

    return decorator


@_register_pattern("Checkerboard")
def _pat_checkerboard(size: int) -> np.ndarray:
    ys, xs = np.mgrid[:size, :size]
    half = size // 2
    mask = (ys < half) == (xs < half)
    arr = np.full((size, size, 4), 255, dtype=np.uint8)
    arr[mask, :3] = 200
    return arr


@_register_pattern("Horizontal Stripes")
def _pat_hstripes(size: int) -> np.ndarray:
    arr = np.full((size, size, 4), 255, dtype=np.uint8)
    rows = (np.arange(size) // 4) % 2 == 0
    arr[rows, :, :3] = 180
    return arr


@_register_pattern("Vertical Stripes")
def _pat_vstripes(size: int) -> np.ndarray:
    arr = np.full((size, size, 4), 255, dtype=np.uint8)
    cols = (np.arange(size) // 4) % 2 == 0
    arr[:, cols, :3] = 180
    return arr


@_register_pattern("Diagonal Stripes")
def _pat_diagonal(size: int) -> np.ndarray:
    ys, xs = np.mgrid[:size, :size]
    mask = ((xs + ys) // 4) % 2 == 0
    arr = np.full((size, size, 4), 255, dtype=np.uint8)
    arr[mask, :3] = 180
    return arr


@_register_pattern("Dots")
def _pat_dots(size: int) -> np.ndarray:
    ys, xs = np.mgrid[:size, :size]
    cx, cy = size // 2, size // 2
    mask = (xs - cx) ** 2 + (ys - cy) ** 2 <= (size // 4) ** 2
    arr = np.full((size, size, 4), 255, dtype=np.uint8)
    arr[mask, :3] = 150
    return arr


@_register_pattern("Crosshatch")
def _pat_crosshatch(size: int) -> np.ndarray:
    ys, xs = np.mgrid[:size, :size]
    mask = ((xs // 4) % 2 == 0) | ((ys // 4) % 2 == 0)
    arr = np.full((size, size, 4), 255, dtype=np.uint8)
    arr[mask, :3] = 190
    return arr


def _generate_builtin_pattern(name: str, size: int = 32) -> np.ndarray:
    """Generate a procedural pattern as RGBA uint8 array."""
    generator = _PATTERN_GENERATORS.get(name)
    if generator is not None:
        return generator(size)
    return np.full((size, size, 4), 255, dtype=np.uint8)


class PatternFillPlugin:
    """Pattern bucket fill tool."""

    def __init__(self) -> None:
        self.host_api: HostAPI | None = None
        self._tolerance: int = 32
        self._gap_close_px: int = 0
        self._pattern_index: int = 0
        self._pattern_scale: float = 1.0
        self._pending_commands: list[Command] = []
        self._patterns: dict[str, np.ndarray] = {}
        self._pattern_names: list[str] = []
        self._load_patterns()

        self.metadata = PluginMetadata(
            id="loomstroke.pattern_fill",
            name=_ts("Pattern Fill"),
            version="1.0.0",
            description=_ts("Fill regions with tiled patterns"),
            author="LoomStroke Team",
            types=[PluginType.TOOL],
        )

    def _load_patterns(self) -> None:
        """Load built-in patterns."""
        for name in _BUILTIN_PATTERN_NAMES:
            # Try file first
            safe_name = name.lower().replace(" ", "_")
            path = _BUILTIN_PATTERN_DIR / f"{safe_name}.png"
            if path.exists():
                try:
                    img = Image.open(path).convert("RGBA")
                    self._patterns[name] = np.array(img, dtype=np.uint8)
                    self._pattern_names.append(name)
                    continue
                except Exception:
                    logger.debug("pattern image load failed", exc_info=True)
            # Fall back to procedural
            self._patterns[name] = _generate_builtin_pattern(name)
            self._pattern_names.append(name)

    @property
    def _current_pattern(self) -> np.ndarray:
        idx = max(0, min(self._pattern_index, len(self._pattern_names) - 1))
        name = self._pattern_names[idx]
        return self._patterns[name]

    def descriptor(self) -> ToolDescriptor:
        return ToolDescriptor(
            id="pattern_fill",
            display_name=_ts("Pattern Fill"),
            cursor="crosshair",
            category="fill",
            order=17,
        )

    def tool_metadata(self) -> ToolMetadata:
        descriptor = self.descriptor()
        icon_path = resolve_tool_icon_path(descriptor.id)
        icon_svg = str(icon_path) if icon_path else descriptor.icon_svg
        return ToolMetadata(
            tool_id=descriptor.id,
            display_name=descriptor.display_name,
            category=descriptor.category or "fill",
            tags=["fill", "pattern"],
            icon_svg=icon_svg,
            group_id="fill.basic",
            group_label=_ts("Fill"),
            group_order=30,
            properties=[
                PropertySpec(
                    key="pattern",
                    display_name=_ts("Pattern"),
                    type=PropertyType.ENUM,
                    enum_items=self._pattern_names,
                    default_value=self._pattern_names[0] if self._pattern_names else "",
                ),
                PropertySpec(
                    key="pattern_scale",
                    display_name=_ts("Scale"),
                    type=PropertyType.FLOAT,
                    min_value=0.1,
                    max_value=10.0,
                    default_value=1.0,
                    step=0.1,
                ),
                PropertySpec(
                    key="color_tolerance",
                    display_name=_ts("Tolerance"),
                    type=PropertyType.INT,
                    min_value=0,
                    max_value=255,
                    default_value=self._tolerance,
                    step=1,
                    is_common=True,
                ),
                PropertySpec(
                    key="gap_close_px",
                    display_name=_ts("Gap Close (px)"),
                    type=PropertyType.INT,
                    min_value=0,
                    max_value=10,
                    default_value=self._gap_close_px,
                    step=1,
                ),
            ],
        )

    def activate(self, host: HostAPI) -> None:
        self.host_api = host
        if not self._tool_registered(host):
            register_tool = getattr(host, "register_tool", None)
            if callable(register_tool):
                register_tool(self)

    def enable(self) -> None:
        if self.host_api:
            self.host_api.events.emit("tool.changed", {"tool": "pattern_fill"})

    def disable(self) -> None:
        self._pending_commands.clear()

    def deactivate(self) -> None:
        if self.host_api:
            unregister = getattr(self.host_api, "unregister_tool", None)
            if callable(unregister):
                unregister(self.descriptor().id)
        self._pending_commands.clear()
        self.host_api = None

    def on_pointer_down(
        self,
        pt: StrokePoint,
        *,
        alt: bool = False,
        shift: bool = False,
        ctrl: bool = False,
        meta: bool = False,
    ) -> None:
        del alt, shift, ctrl, meta
        if not self.host_api:
            return

        seed_x = round(pt.pos[0])
        seed_y = round(pt.pos[1])
        layer_id = self._active_layer_id()

        command = PatternFillCommand(
            reference_layer_id=layer_id,
            seed_x=seed_x,
            seed_y=seed_y,
            pattern=self._current_pattern,
            color_tolerance=self._tolerance,
            gap_close_px=self._gap_close_px,
            pattern_scale=self._pattern_scale,
        )
        self._pending_commands.append(command)

    def on_pointer_move(self, pts: Iterable[StrokePoint]) -> None:
        del pts

    def on_pointer_up(self, pt: StrokePoint) -> None:
        del pt

    def on_pointer_hover(self, pt: StrokePoint) -> None:
        del pt

    def on_key(self, key: str, pressed: bool) -> bool:
        del key, pressed
        return False

    def on_wheel(self, delta: float, pt: StrokePoint) -> bool:
        del delta, pt
        return False

    def build_commands(self) -> list[Command]:
        if not self._pending_commands:
            return []
        commands = list(self._pending_commands)
        self._pending_commands.clear()
        return commands

    def get_settings(self) -> dict[str, Any]:
        return {
            "pattern": self._pattern_names[self._pattern_index] if self._pattern_names else "",
            "pattern_scale": self._pattern_scale,
            "color_tolerance": self._tolerance,
            "gap_close_px": self._gap_close_px,
        }

    def set_settings(self, settings: ToolSettings | dict[str, Any]) -> None:
        if isinstance(settings, dict):
            self._apply_setting_dict(settings)

    def set_setting(self, key: str, value: Any) -> None:
        if key == "color_tolerance":
            self._tolerance = _clamp_int(value, 0, 255)
        elif key == "gap_close_px":
            self._gap_close_px = _clamp_int(value, 0, 10)
        elif key == "pattern_scale":
            self._pattern_scale = max(0.1, min(10.0, float(value)))
        elif key == "pattern":
            self._set_pattern(value)

    def create_options_widget(self) -> Any:
        return None

    # --- helpers ---

    def _apply_setting_dict(self, payload: dict[str, Any]) -> None:
        if "color_tolerance" in payload:
            self._tolerance = _clamp_int(payload["color_tolerance"], 0, 255)
        if "gap_close_px" in payload:
            self._gap_close_px = _clamp_int(payload["gap_close_px"], 0, 10)
        if "pattern_scale" in payload:
            self._pattern_scale = max(0.1, min(10.0, float(payload["pattern_scale"])))
        if "pattern" in payload:
            self._set_pattern(payload["pattern"])

    def _set_pattern(self, value: Any) -> None:
        if isinstance(value, str) and value in self._pattern_names:
            self._pattern_index = self._pattern_names.index(value)
        elif isinstance(value, int):
            self._pattern_index = max(0, min(value, len(self._pattern_names) - 1))

    def _active_layer_id(self) -> str | None:
        if not self.host_api:
            return None
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return None
        getter = getattr(canvas, "current_layer", None)
        if not callable(getter):
            return None
        try:
            layer = getter()
        except Exception:
            return None
        return getattr(layer, "id", None)

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


def _clamp_int(value: Any, minimum: int, maximum: int) -> int:
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return minimum
    return max(minimum, min(maximum, numeric))


def create_plugin() -> PatternFillPlugin:
    return PatternFillPlugin()
