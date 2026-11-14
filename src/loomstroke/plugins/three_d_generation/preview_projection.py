"""Import the textured 3D dock view through the standard projection command."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from loomstroke.core.assets_3d.scene import Scene3DDocument
from loomstroke.rendering_3d import (
    OrbitCameraState,
    PreviewDisplayMode,
    ProjectionImportRequest,
    ProjectionImportService,
    SoftwarePreviewRenderer,
)
from loomstroke.rendering_3d.preview_renderer import PreviewRenderResult
from loomstroke.ui.three_d_preview_renderer import ThreeDPreviewRenderer


@dataclass(frozen=True, slots=True)
class DockProjectionRequest(ProjectionImportRequest):
    lighting: Any = None


class DockProjectionImportService(ProjectionImportService):
    def __init__(
        self, maps_renderer: SoftwarePreviewRenderer, display_renderer: ThreeDPreviewRenderer
    ) -> None:
        super().__init__(maps_renderer)
        self._display_renderer = display_renderer

    def _render_projection(
        self, scene: Scene3DDocument, request: ProjectionImportRequest
    ) -> PreviewRenderResult:
        # Keep the existing admission check and software auxiliary maps.
        projection = super()._render_projection(scene, request)
        if request.display_mode is not PreviewDisplayMode.TEXTURED:
            return projection

        lighting = request.lighting if isinstance(request, DockProjectionRequest) else None
        frame = self._display_renderer.render(
            scene,
            width=max(1, request.width),
            height=max(1, request.height),
            camera=request.camera or OrbitCameraState.fit_to_scene(scene),
            display_mode=request.display_mode,
            lighting=lighting,
        )
        return replace(
            projection,
            image=frame.image,
            backend_name="wgpu-3d-preview",
            fallbacks=tuple(dict.fromkeys((*projection.fallbacks, *frame.fallbacks))),
        )
