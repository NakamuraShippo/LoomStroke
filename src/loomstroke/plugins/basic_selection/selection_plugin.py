"""Selection plugin lifecycle and legacy facade."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ...core.commands import Command
from ...host_api import HostAPI, StrokePoint
from ..sdk.base import PluginBase
from ..sdk.tool import ToolDescriptor
from .rect_tool import PLUGIN_METADATA, BasicSelectionRectTool


class BasicSelectionPlugin(PluginBase):
    """Plugin wrapper for the basic rectangle selection tool."""

    def __init__(self) -> None:
        super().__init__(PLUGIN_METADATA)
        self.rect_tool = BasicSelectionRectTool()

    def descriptor(self) -> ToolDescriptor:
        return self.rect_tool.descriptor()

    def activate(self, host_api: HostAPI) -> None:
        super().activate(host_api)
        self.rect_tool.activate(host_api)
        register_tool = getattr(host_api, "register_tool", None)
        if callable(register_tool):
            register_tool(self.rect_tool)

    def enable(self) -> None:
        self.rect_tool.enable()

    def disable(self) -> None:
        self.rect_tool.disable()

    def deactivate(self) -> None:
        self.rect_tool.deactivate()
        super().deactivate()

    async def initialize(self, host_api: HostAPI) -> None:
        self.activate(host_api)

    async def cleanup(self) -> None:
        if self.host_api and hasattr(self.host_api, "unregister_tool"):
            self.host_api.unregister_tool(self.rect_tool.descriptor().id)
        self.deactivate()

    def get_tools(self) -> list[BasicSelectionRectTool]:
        return [self.rect_tool]

    # --- Legacy compatibility surface ---
    def on_pointer_down(
        self,
        pt: StrokePoint,
        *,
        alt: bool = False,
        shift: bool = False,
        ctrl: bool = False,
        meta: bool = False,
    ) -> None:
        self.rect_tool.on_pointer_down(pt, alt=alt, shift=shift, ctrl=ctrl, meta=meta)

    def on_pointer_move(self, pts: Iterable[StrokePoint]) -> None:
        self.rect_tool.on_pointer_move(pts)

    def on_pointer_up(self, pt: StrokePoint) -> None:
        self.rect_tool.on_pointer_up(pt)

    def on_key(self, key: str, pressed: bool) -> bool:
        return self.rect_tool.on_key(key, pressed)

    def on_action(self, action_key: str, payload: dict[str, Any] | None = None) -> list[Command]:
        return self.rect_tool.on_action(action_key, payload)

    def build_commands(self) -> list[Command]:
        return self.rect_tool.build_commands()
