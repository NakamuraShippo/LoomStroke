"""Optional wgpu device discovery and lifetime management."""

from __future__ import annotations

from typing import Any

from loomstroke.gpu.adapter_policy import is_wgpu_importable as _is_wgpu_importable
from loomstroke.gpu.runtime import GpuRuntime, GpuRuntimeUnavailableError

from .types import WgpuDeviceBundle, WgpuUnavailableError


def is_wgpu_importable() -> bool:
    """Return whether the optional ``wgpu`` package can be imported."""

    return _is_wgpu_importable()


class WgpuDeviceManager:
    """Create and retain a headless wgpu device.

    This class deliberately owns no Qt objects and has no dependency on the
    OpenGL widget/context. It is safe to import in environments where ``wgpu``
    is not installed; device creation raises :class:`WgpuUnavailableError`.
    """

    def __init__(
        self,
        *,
        power_preference: str = "high-performance",
        runtime: GpuRuntime | None = None,
    ) -> None:
        self.power_preference = power_preference
        self._runtime = runtime or GpuRuntime(power_preference=power_preference)
        self._owns_runtime = runtime is None
        self._bundle: WgpuDeviceBundle | None = None

    @staticmethod
    def import_wgpu() -> Any:
        try:
            return GpuRuntime.import_wgpu()
        except GpuRuntimeUnavailableError as exc:
            raise WgpuUnavailableError(str(exc)) from exc

    def is_available(self) -> bool:
        try:
            self.get_bundle()
        except WgpuUnavailableError:
            return False
        return True

    def get_bundle(self) -> WgpuDeviceBundle:
        if self._bundle is not None:
            return self._bundle

        try:
            context = self._runtime.get_context()
        except GpuRuntimeUnavailableError as exc:
            raise WgpuUnavailableError(str(exc)) from exc
        self._bundle = WgpuDeviceBundle(
            wgpu=context.wgpu,
            adapter=context.adapter,
            device=context.device,
            queue=context.queue,
            adapter_info=dict(context.adapter_info),
        )
        return self._bundle

    def release(self) -> None:
        """Drop Python references to wgpu handles."""

        self._bundle = None
        if self._owns_runtime:
            self._runtime.release()

    @staticmethod
    def _adapter_info(adapter: Any) -> dict[str, Any]:
        request_info = getattr(adapter, "request_adapter_info", None)
        if callable(request_info):
            try:
                info = request_info()
                if isinstance(info, dict):
                    return dict(info)
            except Exception:
                return {}
        info = getattr(adapter, "info", None)
        return dict(info) if isinstance(info, dict) else {}
