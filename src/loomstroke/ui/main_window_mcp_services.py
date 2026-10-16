"""Resolve existing live services for MCP without creating duplicate UI/services."""

from __future__ import annotations

from typing import Any


def editing_services(window: Any) -> dict[str, Any]:
    from loomstroke.plugins.comfyui import server_registry  # noqa: PLC0415
    from loomstroke.sam31 import server_manager  # noqa: PLC0415
    from loomstroke.sam31.runtime_config import build_sam31_runtime_config  # noqa: PLC0415

    host = getattr(window, "_host_api", None)
    registry = getattr(window, "_plugin_registry", None)

    def plugin(name: str) -> Any:
        getter = getattr(registry, "get", None)
        return getter(name) if callable(getter) else None

    color = plugin("color_panel")
    reference = plugin("loomstroke.reference_viewer")
    pen = plugin("basic_pen")
    shape = plugin("loomstroke.shape_manager")
    document_getter = getattr(window, "_get_document_service", None)
    document = getattr(host, "document", None)
    get_service = getattr(host, "get_service", None)
    shape_getter = getattr(window, "_get_shape_manager_panel", None)
    gateways = get_service("ai_effect_gateway_registry") if callable(get_service) else None
    comfy_gateway = (
        gateways.get("comfyui") if gateways is not None and "comfyui" in gateways else None
    )
    dock_getter = getattr(window, "get_panel_dock", None)
    dock = dock_getter("ui.ai_effect_manager") if callable(dock_getter) else None
    manager = dock.widget() if dock is not None else None
    services = {
        "host_api": host,
        "sam31_manager": server_manager._manager,
        "sam31_manager_factory": server_manager.get_sam31_server_manager,
        "sam31_config_factory": lambda: build_sam31_runtime_config(
            getattr(window, "_settings", None)
        ),
        "pages": getattr(host, "pages", None),
        "document_service": document,
        "document_service_factory": document_getter,
        "palette_repository": getattr(color, "_repository", None),
        "color_panel": getattr(color, "_panel", None),
        "reference_viewer": getattr(reference, "_panel", None),
        "reference_viewer_factory": getattr(reference, "create_widget", None),
        "brush_store": getattr(pen, "_store", None),
        "brush_panel": getattr(pen, "_panel", None),
        "brush_library_changed": getattr(pen, "_refresh_panel", None),
        "viewport": getattr(getattr(window, "canvas", None), "viewport", None),
        "shape_manager": getattr(shape, "_panel", None),
        "shape_manager_factory": shape_getter,
        "ai_effect_preset_library": getattr(window, "_ai_effect_preset_library", None),
        "ai_effect_gateway_registry": gateways,
        "ai_effect_presets_changed": getattr(manager, "refresh", None),
        "comfyui_registry": server_registry._REGISTRY,
        "comfyui_registry_factory": server_registry.get_registry,
        "comfyui_service": getattr(comfy_gateway, "_service", None),
        "comfyui_native_author": getattr(manager, "_comfy_author", None),
        "generation_history": get_service("generation_history") if callable(get_service) else None,
        "llm_gateway": plugin("llm_gateway_service"),
    }
    if document is not None or callable(document_getter):
        from loomstroke.core.document_service import SaveOptions  # noqa: PLC0415

        services["document_metadata"] = lambda: (
            document or document_getter()
        )._build_document_metadata(SaveOptions())
    return {key: value for key, value in services.items() if value is not None}
