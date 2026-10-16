"""
Basic fill bucket tool plugin implementation.

Click to flood fill a contiguous region using the active layer as reference.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from ...core.fill.commands_fill import FloodFillCommand, FloodFillToNewLayerCommand
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
from .fill_gesture_controller import FillGestureController
from .fill_preparation_controller import FillPreparationController
from .settings import (
    FILL_TARGET_ACTIVE,
    FILL_TARGET_LABELS,
    FILL_TARGET_NEW_LAYER,
    FillBucketSettings,
    load_settings,
    normalize_fill_target,
    save_settings,
    to_display_fill_target,
)

if TYPE_CHECKING:
    from ...core.commands import Command


logger = logging.getLogger(__name__)


def _ts(text: str) -> str:
    from loomstroke.ui.translations import translated_literal_domain  # noqa: PLC0415

    return translated_literal_domain("BasicFillBucket", text, use_fallback=True)


class BasicFillBucketPlugin:
    """Basic bucket fill tool that generates flood fill commands."""

    def __init__(self) -> None:
        self.host_api: HostAPI | None = None
        self.settings = FillBucketSettings()
        self._pending_commands: list[Command] = []
        self._preparation = FillPreparationController(self)
        self._gesture = FillGestureController(self)

        self.metadata = PluginMetadata(
            id="loomstroke.basic_fill_bucket",
            name=_ts("Basic Fill Bucket"),
            version="1.0.0",
            description=_ts("Flood fill bucket tool"),
            author="LoomStroke Team",
            types=[PluginType.TOOL],
        )

    def descriptor(self) -> ToolDescriptor:
        return ToolDescriptor(
            id="basic_fill_bucket",
            display_name=_ts("Fill"),
            cursor="crosshair",
            category="fill",
            order=15,
        )

    def tool_metadata(self) -> ToolMetadata:
        descriptor = self.descriptor()
        enum_items = [
            FILL_TARGET_LABELS[FILL_TARGET_ACTIVE],
            FILL_TARGET_LABELS[FILL_TARGET_NEW_LAYER],
        ]
        icon_path = resolve_tool_icon_path(descriptor.id)
        icon_svg = str(icon_path) if icon_path else descriptor.icon_svg
        return ToolMetadata(
            tool_id=descriptor.id,
            display_name=descriptor.display_name,
            category=descriptor.category or "fill",
            tags=["fill", "bucket"],
            icon_svg=icon_svg,
            group_id="fill.basic",
            group_label=_ts("Fill"),
            group_order=30,
            properties=[
                PropertySpec(
                    key="fill_mode",
                    display_name=_ts("Fill Mode"),
                    type=PropertyType.ENUM,
                    enum_items=["Bucket", "Enclose and Fill", "Gap Fill"],
                    default_value=self.settings.fill_mode,
                ),
                PropertySpec(
                    key="reference_mode",
                    display_name=_ts("Reference"),
                    type=PropertyType.ENUM,
                    enum_items=["Active Layer", "Visible Layers"],
                    default_value=self.settings.reference_mode,
                ),
                PropertySpec(
                    key="area_expansion",
                    display_name=_ts("Area Scaling"),
                    type=PropertyType.INT,
                    min_value=-16,
                    max_value=16,
                    default_value=self.settings.area_expansion,
                    step=1,
                ),
                PropertySpec(
                    key="gap_fill_size",
                    display_name=_ts("Gap Fill Size"),
                    type=PropertyType.INT,
                    min_value=1,
                    max_value=256,
                    default_value=self.settings.gap_fill_size,
                    step=1,
                ),
                PropertySpec(
                    key="color_tolerance",
                    display_name=_ts("Color Tolerance"),
                    type=PropertyType.INT,
                    min_value=0,
                    max_value=255,
                    default_value=self.settings.color_tolerance,
                    step=1,
                    is_common=True,
                ),
                PropertySpec(
                    key="gap_close_px",
                    display_name=_ts("Gap Close (px)"),
                    type=PropertyType.INT,
                    min_value=0,
                    max_value=10,
                    default_value=self.settings.gap_close_px,
                    step=1,
                ),
                PropertySpec(
                    key="fill_target",
                    display_name=_ts("Fill Target"),
                    type=PropertyType.ENUM,
                    enum_items=enum_items,
                    default_value=to_display_fill_target(self.settings.fill_target),
                ),
            ],
        )

    def activate(self, host: HostAPI) -> None:
        self.host_api = host
        self.settings = load_settings(host.settings)
        if not self._tool_registered(host):
            register_tool = getattr(host, "register_tool", None)
            if callable(register_tool):
                register_tool(self)

    def enable(self) -> None:
        if self.host_api:
            self.host_api.events.emit("tool.changed", {"tool": "basic_fill_bucket"})

    def disable(self) -> None:
        self._preparation.cancel()
        self._gesture.cancel()
        self._pending_commands.clear()

    def deactivate(self) -> None:
        self._preparation.cancel()
        self._gesture.cancel()
        if self.host_api:
            save_settings(self.host_api.settings, self.settings)
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
        if self.settings.fill_mode != "Bucket":
            self._gesture.down(pt)
            return
        if self.settings.reference_mode != "Active Layer" or self.settings.area_expansion:
            self._preparation.prepare(pt)
            return
        seed_x = round(pt.pos[0])
        seed_y = round(pt.pos[1])
        fill_rgba = self._active_color_rgba()
        reference_layer_id = self._active_layer_id()
        if fill_rgba is None:
            return
        target_mode = normalize_fill_target(self.settings.fill_target)
        command: Command
        if target_mode == FILL_TARGET_NEW_LAYER:
            command = FloodFillToNewLayerCommand(
                reference_layer_id,
                seed_x,
                seed_y,
                fill_rgba,
                self.settings.color_tolerance,
                self.settings.gap_close_px,
                layer_name="Fill",
            )
        else:
            command = FloodFillCommand(
                reference_layer_id,
                seed_x,
                seed_y,
                fill_rgba,
                self.settings.color_tolerance,
                self.settings.gap_close_px,
            )
        self._pending_commands.append(command)

    def on_pointer_move(self, pts: Iterable[StrokePoint]) -> None:
        self._gesture.move(list(pts))

    def on_pointer_up(self, pt: StrokePoint) -> None:
        self._gesture.up(pt)

    def on_pointer_hover(self, pt: StrokePoint) -> None:
        del pt

    def on_key(self, key: str, pressed: bool) -> bool:
        if pressed and key.lower() in {"escape", "esc"}:
            self._gesture.cancel()
            self._preparation.cancel()
            return True
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

    def get_settings(self) -> FillBucketSettings:
        return FillBucketSettings(
            color_tolerance=self.settings.color_tolerance,
            gap_close_px=self.settings.gap_close_px,
            fill_target=to_display_fill_target(self.settings.fill_target),
            reference_mode=self.settings.reference_mode,
            area_expansion=self.settings.area_expansion,
            fill_mode=self.settings.fill_mode,
            gap_fill_size=self.settings.gap_fill_size,
        )

    def set_settings(self, settings: ToolSettings | dict[str, Any]) -> None:
        self._preparation.cancel()
        self._gesture.cancel()
        if isinstance(settings, dict):
            self._apply_setting_dict(settings)
            return
        if isinstance(settings, FillBucketSettings):
            self._apply_setting_dict(vars(settings))

    def set_setting(self, key: str, value: Any) -> None:
        if key in {"reference_mode", "area_expansion", "fill_mode", "gap_fill_size"}:
            self._gesture.cancel()
            self._preparation.cancel()
            self._apply_setting_dict({key: value})
            return
        if key == "color_tolerance":
            self.settings.color_tolerance = _clamp_int(value, 0, 255)
        elif key == "gap_close_px":
            self.settings.gap_close_px = _clamp_int(value, 0, 10)
        elif key == "fill_target":
            self.settings.fill_target = normalize_fill_target(value)

    def create_options_widget(self) -> Any:
        return None

    def _apply_setting_dict(self, payload: dict[str, Any]) -> None:
        for key, values in (
            ("reference_mode", {"Active Layer", "Visible Layers"}),
            ("fill_mode", {"Bucket", "Enclose and Fill", "Gap Fill"}),
        ):
            if key in payload and payload[key] in values:
                setattr(self.settings, key, payload[key])
        for key, minimum, maximum in (("area_expansion", -16, 16), ("gap_fill_size", 1, 256)):
            if key in payload:
                setattr(self.settings, key, _clamp_int(payload[key], minimum, maximum))
        if "color_tolerance" in payload:
            self.settings.color_tolerance = _clamp_int(payload["color_tolerance"], 0, 255)
        if "gap_close_px" in payload:
            self.settings.gap_close_px = _clamp_int(payload["gap_close_px"], 0, 10)
        if "fill_target" in payload:
            self.settings.fill_target = normalize_fill_target(payload["fill_target"])

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

    def _active_color_rgba(self) -> tuple[float, float, float, float] | None:  # noqa: PLR0911
        if not self.host_api:
            return None
        getter = getattr(self.host_api, "get_active_color", None)
        if callable(getter):
            try:
                descriptor = getter()
                return tuple(float(c) for c in descriptor.rgba)
            except Exception:
                return None
        state = getattr(self.host_api, "color_state", None)
        if state is None:
            return None
        current = getattr(state, "current", None)
        rgba = getattr(current, "rgba", None)
        if rgba is None:
            return None
        try:
            return tuple(float(c) for c in rgba)
        except Exception:
            return None

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


def create_plugin() -> BasicFillBucketPlugin:
    return BasicFillBucketPlugin()
