"""ComfyUI Plugin for LoomStroke.

Provides AI generation capabilities through ComfyUI backend
as an AIServicePlugin following the Master.md architecture.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loomstroke.plugins.sdk.base import PluginMetadata, PluginType

if TYPE_CHECKING:
    from loomstroke.core.event_bus import EventBus

from loomstroke.core.event_bus import get_event_bus

from .service import ComfyUIService

__version__ = "1.0.0"


def get_plugin_metadata() -> PluginMetadata:
    """Get plugin metadata for registration.

    Returns:
        Plugin metadata describing this plugin
    """
    return PluginMetadata(
        id="comfyui_service",
        name="ComfyUI AI Service",
        version=__version__,
        description="ComfyUI integration for AI image generation",
        author="LoomStroke Team",
        license="MIT",
        types=[PluginType.AI_SERVICE],
        capabilities={
            "text2img": "Generate images from text prompts",
            "img2img": "Transform existing images",
            "inpaint": "Fill in masked areas intelligently",
            "upscale": "Enhance image resolution",
            "controlnet": "Guided generation with control images",
        },
        settings_schema={
            "comfyui.host": {
                "type": "string",
                "default": "127.0.0.1",
                "description": "ComfyUI server host",
            },
            "comfyui.port": {
                "type": "integer",
                "default": 8188,
                "description": "ComfyUI server port",
            },
            "comfyui.use_ssl": {
                "type": "boolean",
                "default": False,
                "description": "Use SSL/TLS for connection",
            },
            "comfyui.timeout": {
                "type": "integer",
                "default": 30,
                "description": "Request timeout in seconds",
            },
            "comfyui.max_retries": {
                "type": "integer",
                "default": 3,
                "description": "Maximum retry attempts",
            },
            "comfyui.local_url": {
                "type": "string",
                "default": "",
                "description": "Local ComfyUI server URL (Phase 10c-ui)",
            },
            "comfyui.external_url": {
                "type": "string",
                "default": "",
                "description": "External (remote) ComfyUI server URL (Phase 10c-ui)",
            },
            "comfyui.external_servers": {
                "type": "array",
                "default": [],
                "description": "External ComfyUI server list (Phase 10c-ui)",
            },
        },
    )


def create_plugin(event_bus: EventBus | None = None) -> ComfyUIService:
    """Factory function to create plugin instance.

    Args:
        event_bus: Optional event bus for integration

    Returns:
        ComfyUIService instance
    """
    from .service import ComfyUIService  # noqa: PLC0415

    if event_bus is None:
        event_bus = get_event_bus()

    return ComfyUIService(event_bus=event_bus)


def create_event_bridge(
    event_bus: EventBus,
    canvas: Any = None,
) -> Any:
    """Create event bridge for command generation.

    Args:
        event_bus: Event bus for communication
        canvas: Optional canvas reference

    Returns:
        ComfyUIEventBridge instance
    """
    from .event_bridge import ComfyUIEventBridge  # noqa: PLC0415

    return ComfyUIEventBridge(event_bus=event_bus, canvas=canvas)


def create_preview_handler(
    event_bus: EventBus,
    event_bridge: Any,
    parent: Any = None,
) -> Any:
    """Create preview handler for UI integration.

    Args:
        event_bus: Event bus for communication
        event_bridge: Event bridge for command generation
        parent: Optional parent widget

    Returns:
        ComfyUIPreviewHandler instance
    """
    from .preview_handler import ComfyUIPreviewHandler  # noqa: PLC0415

    return ComfyUIPreviewHandler(
        event_bus=event_bus,
        event_bridge=event_bridge,
        parent=parent,
    )


# Export public API
__all__ = [
    "__version__",
    "create_event_bridge",
    "create_plugin",
    "create_preview_handler",
    "get_plugin_metadata",
]
