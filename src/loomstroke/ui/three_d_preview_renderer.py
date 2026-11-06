"""Image-only dock preview using the canvas's retained wgpu 3D renderer."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from PySide6.QtGui import QColor, QImage, QPainter

from loomstroke.core.assets_3d.scene import Scene3DDocument
from loomstroke.rendering_3d import OrbitCameraState, PreviewDisplayMode
from loomstroke.ui.canvas.wgpu_backend.device_manager import WgpuDeviceManager
from loomstroke.ui.canvas.wgpu_backend.readback import WgpuReadback
from loomstroke.ui.canvas.wgpu_backend.target_reuse import allocate_render_target
from loomstroke.ui.canvas.wgpu_backend.three_d_renderer import Wgpu3DLayerRenderer


@dataclass(frozen=True, slots=True)
class ThreeDPreviewFrame:
    image: QImage
    fallbacks: tuple[str, ...] = ()


class ThreeDPreviewRenderer:
    """Own one reusable GPU preview; do not initialize a device until visible."""

    def __init__(self) -> None:
        self._devices = WgpuDeviceManager()
        self._renderer: Wgpu3DLayerRenderer | None = None
        self._readback: WgpuReadback | None = None
        # Geometry keys include scene identity. Keep that identity alive while
        # its buffers are cached, including across coalesced pose updates.
        self._scene: Scene3DDocument | None = None

    def render(
        self,
        scene: Scene3DDocument,
        *,
        width: int,
        height: int,
        camera: OrbitCameraState,
        display_mode: PreviewDisplayMode,
        lighting: Any = None,
    ) -> ThreeDPreviewFrame:
        bundle = self._devices.get_bundle()
        if self._renderer is None:
            self._renderer = Wgpu3DLayerRenderer(bundle)
            self._readback = WgpuReadback(bundle)
        layer = SimpleNamespace(
            id="generated-3d-preview",
            scene_3d=scene,
            camera_3d=camera,
            lighting_3d=lighting,
            display_mode_3d=display_mode.value,
        )
        encoder = bundle.device.create_command_encoder()
        rendered = self._renderer.render_layer(
            encoder,
            layer,
            width=width,
            height=height,
            create_target=lambda w, h: allocate_render_target(bundle, w, h),
        )
        bundle.queue.submit([encoder.finish()])
        image = QImage(width, height, QImage.Format.Format_RGBA8888)
        image.fill(QColor(28, 30, 34, 255))
        if rendered is None:
            self._scene = scene
            return ThreeDPreviewFrame(image)
        assert self._readback is not None
        rgba = self._readback.read_rgba(rendered.texture)
        overlay = QImage(rgba.data, width, height, width * 4, QImage.Format.Format_RGBA8888)
        painter = QPainter(image)
        try:
            painter.drawImage(0, 0, overlay)
        finally:
            painter.end()
        self._scene = scene
        return ThreeDPreviewFrame(image, rendered.warnings)

    def release(self) -> None:
        if self._renderer is not None:
            self._renderer.release()
            self._renderer = None
        self._readback = None
        self._scene = None
        self._devices.release()
