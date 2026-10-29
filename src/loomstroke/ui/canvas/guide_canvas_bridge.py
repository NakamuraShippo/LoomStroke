"""Keep guide state on its input controller and repaint the visible overlay."""

from typing import Any


def guide_canvas(widget: Any) -> Any:
    return getattr(widget, "legacy_canvas", widget)


def update_guide_overlay(widget: Any) -> None:
    controller = guide_canvas(widget)
    host = getattr(controller, "_guide_viewport_host", None)
    if host is not None and host.native_surface_active:
        host._overlay_surface.update()
    else:
        controller.update()
