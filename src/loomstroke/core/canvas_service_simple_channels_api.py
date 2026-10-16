"""Internal canvas facade for channel editing; HostAPI contracts remain unchanged."""

from __future__ import annotations

from .channels.canvas_channels_service import CanvasChannelsService
from .native_cmyk.canvas_service import CanvasNativeCmykService


class CanvasServiceChannelsApiMixin:
    def channels(self) -> CanvasChannelsService:
        service = getattr(self, "_channels_service", None)
        if service is None:
            service = CanvasChannelsService(self)
            self._channels_service = service
        return service

    def native_cmyk(self) -> CanvasNativeCmykService:
        service = getattr(self, "_native_cmyk_service", None)
        if service is None:
            service = CanvasNativeCmykService(self)
            self._native_cmyk_service = service
        return service
