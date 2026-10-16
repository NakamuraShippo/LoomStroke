"""UI plugin protocols and base classes.

This module defines the protocols for UI-related plugins.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QWidget

from .base import PluginBase

if TYPE_CHECKING:
    from loomstroke.host_api import HostAPI


@dataclass
class PanelDescriptor:
    """Descriptor for UI panel plugins."""

    id: str
    display_name: str
    category: str = "tools"
    icon_svg: str | None = None
    default_position: str = "right"  # left, right, bottom, floating
    default_size: tuple[int, int] = (300, 400)
    resizable: bool = True
    closable: bool = True
    floating: bool = False


class UIPanelPlugin(PluginBase, ABC):
    """Base class for UI panel plugins."""

    @abstractmethod
    def descriptor(self) -> PanelDescriptor:
        """Get panel descriptor.

        Returns:
            Panel descriptor with metadata
        """
        ...

    @abstractmethod
    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        """Create the panel widget.

        Args:
            parent: Optional parent widget

        Returns:
            The panel widget
        """
        ...

    def activate(self, host_api: HostAPI) -> None:
        """Activate the plugin.

        Args:
            host_api: Host API instance
        """
        super().activate(host_api)

    def deactivate(self) -> None:
        """Deactivate the plugin."""
        super().deactivate()

    def on_panel_shown(self) -> None:
        """Called when the panel is shown."""
        pass

    def on_panel_hidden(self) -> None:
        """Called when the panel is hidden."""
        pass

    def on_settings_changed(self, key: str, value: any) -> None:
        """Called when plugin settings change.

        Args:
            key: Setting key that changed
            value: New setting value
        """
        pass
