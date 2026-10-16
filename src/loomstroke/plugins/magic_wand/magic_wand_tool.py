"""Magic Wand selection tool plugin.

Click to select contiguous colour regions using flood fill.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from ...core.commands import SelectionOp
from ...host_api import HostAPI, StrokePoint
from ...plugins.sdk.base import PluginMetadata, PluginType
from ...plugins.sdk.tool import (
    ContextToolbarSpec,
    PropertySpec,
    PropertyType,
    ToolDescriptor,
    ToolMetadata,
    ToolSettings,
)
from ..common.tool_icons import resolve_tool_icon_path
from .magic_wand_command import MagicWandCommand

if TYPE_CHECKING:
    from ...core.commands import Command


logger = logging.getLogger(__name__)


def _ts(text: str) -> str:
    from loomstroke.ui.translations import translated_literal_domain  # noqa: PLC0415

    return translated_literal_domain("MagicWand", text, use_fallback=True)


_OP_LABELS: list[str] = [_ts("Replace"), _ts("Add"), _ts("Subtract"), _ts("Intersect")]
_OP_MAP: dict[str, SelectionOp] = {
    "Replace": SelectionOp.REPLACE,
    "Add": SelectionOp.ADD,
    "Subtract": SelectionOp.SUBTRACT,
    "Intersect": SelectionOp.INTERSECT,
}


class MagicWandPlugin:
    """Magic wand selection tool that generates selection mask commands."""

    def __init__(self) -> None:
        self.host_api: HostAPI | None = None
        self._tolerance: int = 32
        self._contiguous: bool = True
        self._operation: SelectionOp = SelectionOp.REPLACE
        self._pending_commands: list[Command] = []

        # Last click info for re-applying on parameter change
        self._last_seed: tuple[int, int] | None = None
        self._last_layer_id: str | None = None

        self.metadata = PluginMetadata(
            id="loomstroke.magic_wand",
            name=_ts("Magic Wand"),
            version="1.0.0",
            description=_ts("Select contiguous colour regions"),
            author="LoomStroke Team",
            types=[PluginType.TOOL],
        )

    def descriptor(self) -> ToolDescriptor:
        return ToolDescriptor(
            id="magic_wand",
            display_name=_ts("Magic Wand"),
            cursor="crosshair",
            category="selection",
            order=12,
        )

    def tool_metadata(self) -> ToolMetadata:
        descriptor = self.descriptor()
        icon_path = resolve_tool_icon_path(descriptor.id)
        icon_svg = str(icon_path) if icon_path else descriptor.icon_svg
        return ToolMetadata(
            tool_id=descriptor.id,
            display_name=descriptor.display_name,
            category=descriptor.category or "selection",
            tags=["selection", "magic wand", "flood fill"],
            icon_svg=icon_svg,
            group_id="selection.tools",
            group_label=_ts("Selection"),
            group_order=31,
            properties=[
                PropertySpec(
                    key="tolerance",
                    display_name=_ts("Tolerance"),
                    type=PropertyType.INT,
                    min_value=0,
                    max_value=255,
                    default_value=self._tolerance,
                    step=1,
                    is_common=True,
                ),
                PropertySpec(
                    key="contiguous",
                    display_name=_ts("Contiguous"),
                    type=PropertyType.BOOL,
                    default_value=self._contiguous,
                ),
                PropertySpec(
                    key="operation",
                    display_name=_ts("Operation"),
                    type=PropertyType.ENUM,
                    enum_items=_OP_LABELS,
                    default_value="Replace",
                ),
            ],
        )

    def context_toolbar_spec(self) -> ContextToolbarSpec | None:
        return ContextToolbarSpec(
            show_trigger="selection_created",
            property_keys=["tolerance", "contiguous", "operation"],
            anchor_mode="selection_bbox",
            placement="bottom_center",
            mode="settings",
        )

    def activate(self, host: HostAPI) -> None:
        self.host_api = host
        if not self._tool_registered(host):
            register_tool = getattr(host, "register_tool", None)
            if callable(register_tool):
                register_tool(self)

    def enable(self) -> None:
        if self.host_api:
            self.host_api.events.emit("tool.changed", {"tool": "magic_wand"})

    def disable(self) -> None:
        self._pending_commands.clear()
        self._last_seed = None
        self._last_layer_id = None

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
        del alt, meta
        if not self.host_api:
            return

        seed_x = round(pt.pos[0])
        seed_y = round(pt.pos[1])
        layer_id = self._active_layer_id()

        # Store for re-application on parameter change
        self._last_seed = (seed_x, seed_y)
        self._last_layer_id = layer_id

        # Modifier keys adjust operation
        op = self._operation
        if shift and ctrl:
            op = SelectionOp.INTERSECT
        elif shift:
            op = SelectionOp.ADD
        elif ctrl:
            op = SelectionOp.SUBTRACT

        command = MagicWandCommand(
            layer_id=layer_id,
            seed_x=seed_x,
            seed_y=seed_y,
            tolerance=self._tolerance,
            operation=op,
            contiguous=self._contiguous,
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
            "tolerance": self._tolerance,
            "contiguous": self._contiguous,
            "operation": self._operation.value,
        }

    def set_settings(self, settings: ToolSettings | dict[str, Any]) -> None:
        if isinstance(settings, dict):
            self._apply_setting_dict(settings)

    def set_setting(self, key: str, value: Any) -> None:
        if key == "tolerance":
            self._tolerance = _clamp_int(value, 0, 255)
        elif key == "contiguous":
            self._contiguous = bool(value)
        elif key == "operation":
            self._set_operation(value)
        else:
            return
        # Re-apply selection with updated parameters
        self._reapply_selection()

    def create_options_widget(self) -> Any:
        return None

    def _reapply_selection(self) -> None:
        """Re-execute magic wand at the last seed point with current parameters."""
        if self._last_seed is None or self.host_api is None:
            return
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return
        seed_x, seed_y = self._last_seed
        cmd = MagicWandCommand(
            layer_id=self._last_layer_id,
            seed_x=seed_x,
            seed_y=seed_y,
            tolerance=self._tolerance,
            operation=SelectionOp.REPLACE,
            contiguous=self._contiguous,
        )
        submit = getattr(canvas, "submit", None)
        if callable(submit):
            submit(cmd)

    # --- helpers ---

    def _apply_setting_dict(self, payload: dict[str, Any]) -> None:
        if "tolerance" in payload:
            self._tolerance = _clamp_int(payload["tolerance"], 0, 255)
        if "contiguous" in payload:
            self._contiguous = bool(payload["contiguous"])
        if "operation" in payload:
            self._set_operation(payload["operation"])

    def _set_operation(self, value: Any) -> None:
        if isinstance(value, SelectionOp):
            self._operation = value
        elif isinstance(value, str):
            op = _OP_MAP.get(value)
            if op is None:
                source = getattr(value, "source_text", None) or value
                op = _OP_MAP.get(source, SelectionOp.REPLACE)
            self._operation = op

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


def create_plugin() -> MagicWandPlugin:
    return MagicWandPlugin()
