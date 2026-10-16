"""Color management tool plugin."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from loomstroke.core.color_state import ColorStateSnapshot
from loomstroke.core.commands import Command
from loomstroke.core.events import Events
from loomstroke.host_api import HostAPI, StrokePoint
from loomstroke.plugins.sdk import (
    PropertySpec,
    PropertyType,
    ToolDescriptor,
    ToolMetadata,
    ToolPreset,
)
from loomstroke.plugins.sdk.base import PluginMetadata, PluginType  # noqa: F811
from loomstroke.plugins.sdk.tool import ToolPlugin, ToolSettings

logger = logging.getLogger(__name__)


def _ts(text: str) -> str:
    from loomstroke.ui.translations import translated_literal_domain  # noqa: PLC0415

    return translated_literal_domain("ColorToolPlugin", text, use_fallback=True)


@dataclass
class ColorToolSettings(ToolSettings):
    """Settings placeholder for the color tool."""

    eyedropper_enabled: bool = False


class ColorToolPlugin(ToolPlugin):
    """Tool that coordinates shared color state interactions."""

    metadata = PluginMetadata(
        id="color_tool",
        name=_ts("Eyedropper"),
        version="1.0.0",
        description=_ts("Pick color from canvas"),
        author="LoomStroke Team",
        types=[PluginType.TOOL],
    )

    def __init__(self) -> None:
        self._descriptor = ToolDescriptor(
            id="color_tool",
            display_name=_ts("Color"),
            cursor="crosshair",
            category="utility",
            shortcut="I",
            secondary_shortcut="Shift+I",
            secondary_shortcut_description=_ts("Alternate color binding"),
            order=90,
        )
        self._host: HostAPI | None = None
        self._settings = ColorToolSettings()
        self._subscriptions: list[object] = []
        self._snapshot: ColorStateSnapshot | None = None

    # --- ToolPlugin interface -------------------------------------------------

    def descriptor(self) -> ToolDescriptor:
        return self._descriptor

    def tool_metadata(self) -> ToolMetadata:
        return ToolMetadata(
            tool_id=self._descriptor.id,
            display_name=self._descriptor.display_name,
            category=self._descriptor.category or "utility",
            tags=["color", "picker", "utility"],
            icon_svg=self._descriptor.icon_svg,
            group_id="utility.color",
            group_label=_ts("Color"),
            group_order=90,
            properties=[
                PropertySpec(
                    key="eyedropper_enabled",
                    display_name=_ts("Enable Eyedropper"),
                    type=PropertyType.BOOL,
                    default_value=self._settings.eyedropper_enabled,
                )
            ],
            presets=[
                ToolPreset(
                    name=_ts("Match Reference"),
                    values={"eyedropper_enabled": True},
                ),
                ToolPreset(
                    name=_ts("Palette Only"),
                    values={"eyedropper_enabled": False},
                ),
            ],
        )

    def activate(self, host: HostAPI) -> None:
        self._host = host
        if not self._tool_registered(host):
            register_tool = getattr(host, "register_tool", None)
            if callable(register_tool):
                register_tool(self)
        self._subscribe_color_state()

    def enable(self) -> None:
        self._subscribe_color_state()

    def disable(self) -> None:
        self._dispose_subscriptions()

    def deactivate(self) -> None:
        self._dispose_subscriptions()
        if self._host:
            unregister = getattr(self._host, "unregister_tool", None)
            if callable(unregister):
                unregister(self._descriptor.id)
        self._host = None

    def _tool_registered(self, host: HostAPI) -> bool:
        descriptor_id = self._descriptor.id
        manager = getattr(host, "_tool_manager", None)
        if manager and hasattr(manager, "get_registered_tools"):
            try:
                for descriptor in manager.get_registered_tools():
                    if descriptor.id == descriptor_id:
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
                        if descriptor_fn().id == descriptor_id:
                            return True
                    except Exception:
                        continue
        return False

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
        self._pick_color_at(pt)

    def on_pointer_move(self, pts: Iterable[StrokePoint]) -> None:
        for pt in pts:
            self._pick_color_at(pt)

    def on_pointer_up(self, pt: StrokePoint) -> None:
        self._pick_color_at(pt)

    def on_pointer_hover(self, pt: StrokePoint) -> None:
        del pt

    def on_key(self, key: str, pressed: bool) -> bool:
        del key, pressed
        return False

    def on_wheel(self, delta: float, pt: StrokePoint) -> bool:
        del delta, pt
        return False

    def build_commands(self) -> list[Command]:
        return []

    def get_settings(self) -> ToolSettings:
        return self._settings

    def set_settings(self, settings: ToolSettings) -> None:
        if isinstance(settings, ColorToolSettings):
            self._settings = settings

    def set_setting(self, key: str, value: Any) -> None:
        if not hasattr(self._settings, key):
            return
        current = getattr(self._settings, key)
        if isinstance(current, bool):
            setattr(self._settings, key, bool(value))
        else:
            setattr(self._settings, key, value)

    def create_options_widget(self):
        return None

    # --- Internal helpers -----------------------------------------------------

    def _pick_color_at(self, pt: StrokePoint) -> None:
        """Sample the composited canvas at *pt* and set the active color."""
        if not self._host:
            return
        get_pixel = getattr(self._host, "get_pixel_color", None)
        if not callable(get_pixel):
            return
        x, y = int(pt.pos[0]), int(pt.pos[1])
        descriptor = get_pixel(x, y)
        if descriptor is None:
            return
        try:
            self._host.set_active_color(descriptor, source="color_tool")
        except Exception:
            logger.warning("color_tool: set_active_color failed at (%s, %s)", x, y, exc_info=True)

    def _subscribe_color_state(self) -> None:
        if not self._host:
            return
        subscribe = getattr(self._host, "subscribe_color_changed", None)
        if callable(subscribe):
            token = subscribe(self._handle_color_snapshot)
            self._subscriptions.append(token)
        if hasattr(self._host, "get_color_snapshot"):
            try:
                self._snapshot = self._host.get_color_snapshot()  # type: ignore[attr-defined]  # host_api duck-typed
            except Exception:
                self._snapshot = None

    def _dispose_subscriptions(self) -> None:
        if not self._host or not self._subscriptions:
            self._subscriptions.clear()
            return
        unsubscribe = getattr(self._host.events, "off", None)
        if callable(unsubscribe):
            for token in self._subscriptions:
                try:
                    unsubscribe(Events.COLOR_CHANGED, token)
                except Exception:
                    continue
        self._subscriptions.clear()

    def _handle_color_snapshot(self, snapshot: ColorStateSnapshot) -> None:
        self._snapshot = snapshot


def create_plugin() -> ColorToolPlugin:
    """Factory for plugin registry."""
    return ColorToolPlugin()


__all__ = ["ColorToolPlugin", "ColorToolSettings", "create_plugin"]
