"""Projection of 3D preview renders onto the 2D canvas."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from inspect import getattr_static
from typing import Any, cast

import numpy as np
from PySide6.QtGui import QImage

from loomstroke.core.assets_3d.scene import Scene3DDocument
from loomstroke.core.commands import ApplyImageCommand

from .cameras import OrbitCameraState
from .display_modes import PreviewDisplayMode
from .preview_renderer import _DEFAULT_SOFTWARE_RENDER, PreviewRenderResult, SoftwarePreviewRenderer

# Adopt the current software face-processing range for formal projections.
# This admission policy is independent of future preview sampling changes;
# it is not a vertex, pixel, memory, time or cancellation budget.
_FORMAL_SOFTWARE_FACE_LIMIT = 25_000
_PROJECTION_FACE_LIMIT_MESSAGE = (
    "Projection was not imported because this scene exceeds the current software rendering limit."
)


class _ProjectionFaceLimitExceeded(ValueError):
    """Refuse an incomplete formal projection before allocating render output."""

    def __init__(self) -> None:
        super().__init__(_PROJECTION_FACE_LIMIT_MESSAGE)


@dataclass(frozen=True, slots=True)
class ProjectionImportRequest:
    """Projection/import parameters for 2D canvas insertion."""

    position: tuple[int, int] = (0, 0)
    width: int = 512
    height: int = 512
    display_mode: PreviewDisplayMode = PreviewDisplayMode.TEXTURED
    camera: OrbitCameraState | None = None
    target_layer_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProjectionImportResult:
    """Result from command-backed projection import."""

    submitted: bool
    command: ApplyImageCommand
    preview: PreviewRenderResult


class ProjectionImportService:
    """Render a 3D scene and submit it through ``ApplyImageCommand``."""

    def __init__(self, renderer: SoftwarePreviewRenderer | None = None) -> None:
        self._renderer = renderer or SoftwarePreviewRenderer()

    def build_command(
        self,
        scene: Scene3DDocument,
        request: ProjectionImportRequest,
    ) -> tuple[ApplyImageCommand, PreviewRenderResult]:
        options: dict[str, Any] = {
            "width": request.width,
            "height": request.height,
            "camera": request.camera,
            "display_mode": request.display_mode,
        }
        if getattr_static(self._renderer, "render", None) is _DEFAULT_SOFTWARE_RENDER:
            faces = sum(
                int(primitive.indices.size // 3)
                for mesh in scene.meshes
                for primitive in mesh.primitives
                if primitive.indices.size >= 3
            )
            if faces > _FORMAL_SOFTWARE_FACE_LIMIT:
                raise _ProjectionFaceLimitExceeded()
            options["max_faces"] = None
        # Unknown injected renderers retain their existing call shape. The
        # complete-face guarantee above is limited to the standard renderer.
        preview = self._renderer.render(scene, **options)
        metadata = {
            "source": "3d_projection",
            "scene_id": scene.id,
            "display_mode": request.display_mode.value,
            "map_outputs": ("depth", "normal", "segmentation"),
            **dict(request.metadata),
        }
        if request.target_layer_id:
            metadata["target_layer_id"] = request.target_layer_id
        command = ApplyImageCommand(
            image=qimage_to_rgba_array(preview.image),
            position=request.position,
            metadata=metadata,
        )
        return command, preview

    def import_to_canvas(
        self,
        scene: Scene3DDocument,
        canvas: Any,
        request: ProjectionImportRequest,
    ) -> ProjectionImportResult:
        command, preview = self.build_command(scene, request)
        submit = getattr(canvas, "submit", None)
        if callable(submit):
            submitted = bool(submit(command, merge_key="3d_projection_import"))
        else:
            submitted = bool(command.apply(canvas))
        return ProjectionImportResult(submitted=submitted, command=command, preview=preview)


def qimage_to_rgba_array(image: QImage) -> np.ndarray:
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    width = rgba.width()
    height = rgba.height()
    ptr = cast(Any, rgba.bits())
    size = rgba.sizeInBytes()
    with contextlib.suppress(AttributeError):
        ptr.setsize(size)
    arr = np.frombuffer(ptr, dtype=np.uint8, count=size)
    rows = arr.reshape((height, rgba.bytesPerLine()))
    return rows[:, : width * 4].reshape((height, width, 4)).copy()
