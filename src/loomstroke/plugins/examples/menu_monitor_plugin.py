from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ...core.events import MenuVisibilityEvent
from ...host_api import HostAPI
from ..sdk.base import PluginBase, PluginMetadata, PluginType


class MenuVisibilityMonitorPlugin(PluginBase):
    """Automation plugin that records menu visibility toggles to disk."""

    def __init__(self, output_path: Path | None = None) -> None:
        metadata = PluginMetadata(
            id="loomstroke.menu_visibility_monitor",
            name="Menu Visibility Monitor",
            version="1.0.0",
            description="Records optional menu toggles for audits.",
            author="LoomStroke",
            types=[PluginType.AUTOMATION],
        )
        super().__init__(metadata)
        self._output_path = output_path or Path("telemetry") / "menu_visibility_monitor.log"
        self._unsubscribe: Callable[[], None] | None = None

    def activate(self, host_api: HostAPI) -> None:  # type: ignore[override]  # plugin API signature divergence
        super().activate(host_api)
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        self._unsubscribe = host_api.subscribe_menu_visibility_changed(self._on_menu_toggle)

    def deactivate(self) -> None:  # type: ignore[override]  # plugin API signature divergence
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
        super().deactivate()

    def _on_menu_toggle(self, event: MenuVisibilityEvent) -> None:
        line = f"[{event.source or 'system'}] {event.menu_id} -> {'on' if event.enabled else 'off'}"
        with self._output_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def create_plugin() -> MenuVisibilityMonitorPlugin:
    """Factory used by plugin loader."""

    return MenuVisibilityMonitorPlugin()
