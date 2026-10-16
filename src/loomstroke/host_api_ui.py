"""Internal UI/layout delegation for :mod:`loomstroke.host_api`."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from .core.color_state import ColorDescriptor

if TYPE_CHECKING:
    from .host_api import HostAPI, UILayoutService, UILayoutSnapshot

logger = logging.getLogger("loomstroke.host_api")


class HostAPIUIFacade:
    """Own the HostAPI UI bridge without changing plugin-facing methods."""

    def __init__(self, host: HostAPI) -> None:
        self._host = host

    def attach_canvas_widget(self, widget: Any) -> None:
        """Attach the canvas widget used by GPU readback helpers."""
        object.__setattr__(self._host, "_canvas_widget", widget)

    def get_filter_pen_service(self) -> Any | None:
        """Provide the optional raster edit service with its isolated wgpu processor."""
        canvas = self._host.canvas
        if not callable(getattr(canvas, "_get_active_layer", None)):
            return None
        service = getattr(canvas, "_filter_pen_service", None)
        if service is None:
            service = getattr(canvas, "_effect_pen_service", None)
        if service is None or getattr(service, "_closed", False):
            from .core.filter_pen.service import FilterPenService  # noqa: PLC0415
            from .ui.canvas.wgpu_backend.filter_pen_processor import (  # noqa: PLC0415
                FilterPenGpuProcessor,
            )

            service = FilterPenService(canvas, FilterPenGpuProcessor)
            canvas._filter_pen_service = service
            canvas._effect_pen_service = service  # Legacy canvas/service adapter.
            destroyed = getattr(canvas, "destroyed", None)
            if destroyed is not None:
                destroyed.connect(service.close)
        return service

    get_effect_pen_service = get_filter_pen_service

    def get_layout_snapshot(self, *, include_panels: bool = True) -> UILayoutSnapshot | None:
        """Return a captured UI layout snapshot if the UI supports it."""
        service_getter = getattr(self._host.ui, "get_layout_service", None)
        if not callable(service_getter):
            return None
        try:
            layout_service = service_getter()
        except Exception as exc:
            logger.debug("UI layout service getter raised: %s", exc)
            return None
        if layout_service is None:
            return None
        try:
            layout_proto = cast("UILayoutService", layout_service)
            return layout_proto.snapshot(include_panels=include_panels)
        except Exception as exc:
            logger.warning("Failed to snapshot UI layout: %s", exc)
            return None

    def register_panel_widget(
        self,
        panel_id: str,
        title: str,
        widget: Any,
        *,
        area: str = "right",
        default_visible: bool = True,
        owner_plugin_id: str | None = None,
        closable: bool = True,
        resizable: bool = True,
        floating: bool = False,
        default_size: tuple[int, int] | None = None,
    ) -> bool:
        """Register a panel via the attached UI service."""
        register = getattr(self._host.ui, "register_panel_widget", None)
        if not callable(register):
            logger.warning("UI service does not support register_panel_widget")
            return False
        try:
            return bool(
                register(
                    panel_id,
                    title,
                    widget,
                    area=area,
                    default_visible=default_visible,
                    owner_plugin_id=owner_plugin_id,
                    closable=closable,
                    resizable=resizable,
                    floating=floating,
                    default_size=default_size,
                )
            )
        except Exception as exc:
            logger.warning("Failed to register panel %s: %s", panel_id, exc)
            return False

    def unregister_panel(self, panel_id: str) -> bool:
        """Unregister a panel via the attached UI service."""
        unregister = getattr(self._host.ui, "unregister_panel", None)
        if not callable(unregister):
            logger.warning("UI service does not support unregister_panel")
            return False
        try:
            return bool(unregister(panel_id))
        except Exception as exc:
            logger.warning("Failed to unregister panel %s: %s", panel_id, exc)
            return False

    def has_panel(self, panel_id: str) -> bool:
        """Return True when the UI currently has the panel registered."""
        has_panel = getattr(self._host.ui, "has_panel", None)
        if not callable(has_panel):
            return False
        try:
            return bool(has_panel(panel_id))
        except Exception as exc:
            logger.debug("Failed to query panel %s: %s", panel_id, exc)
            return False

    def set_panel_visible(self, panel_id: str, visible: bool) -> bool:
        """Set panel visibility via the attached UI service."""
        setter = getattr(self._host.ui, "set_panel_visible", None)
        if not callable(setter):
            logger.warning("UI service does not support set_panel_visible")
            return False
        try:
            return bool(setter(panel_id, visible))
        except Exception as exc:
            logger.warning("Failed to set panel visibility %s=%s: %s", panel_id, visible, exc)
            return False

    def get_pixel_color(self, x: int, y: int) -> ColorDescriptor | None:
        """Sample a pixel color from the composited canvas at (*x*, *y*)."""
        try:
            from PySide6.QtGui import QColor  # noqa: PLC0415

            # GPU readback is the sole source of composited pixels.
            widget = getattr(self._host, "_canvas_widget", None)
            gpu_pixmap = getattr(widget, "get_gpu_composited_pixmap", None)
            pixmap = gpu_pixmap() if callable(gpu_pixmap) else None

            if pixmap is None or pixmap.isNull():
                return None
            img = pixmap.toImage()
            if x < 0 or y < 0 or x >= img.width() or y >= img.height():
                return None
            qc: QColor = img.pixelColor(x, y)
            return ColorDescriptor(rgba=(qc.redF(), qc.greenF(), qc.blueF(), qc.alphaF()))
        except Exception as exc:
            logger.debug("get_pixel_color failed at (%s, %s): %s", x, y, exc)
            return None


__all__ = ["HostAPIUIFacade"]
