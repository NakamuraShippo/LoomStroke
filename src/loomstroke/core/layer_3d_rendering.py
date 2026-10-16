"""Shared helpers for rendering editable 3D layer previews."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from PySide6.QtGui import QImage

logger = logging.getLogger("loomstroke.core.layer_3d_rendering")


def render_3d_layer(
    layer: Any,
    *,
    width: int | None = None,
    height: int | None = None,
    display_mode: str | None = None,
) -> Any | None:
    """Render the current 3D layer view with the software fallback renderer."""

    scene = getattr(layer, "scene_3d", None)
    if scene is None:
        return None
    try:
        from loomstroke.core.assets_3d import Scene3DDocument  # noqa: PLC0415
        from loomstroke.rendering_3d import SoftwarePreviewRenderer  # noqa: PLC0415
    except ImportError:
        logger.debug("3D preview renderer unavailable", exc_info=True)
        return None
    if not isinstance(scene, Scene3DDocument):
        return None

    layer_image = getattr(layer, "image", None)
    render_width = _render_size(width, layer_image, axis="width")
    render_height = _render_size(height, layer_image, axis="height")
    mode = preview_display_mode(display_mode or getattr(layer, "display_mode_3d", "solid"))
    camera = orbit_camera_state(getattr(layer, "camera_3d", None), scene)
    try:
        return SoftwarePreviewRenderer().render(
            scene,
            width=render_width,
            height=render_height,
            camera=camera,
            display_mode=mode,
        )
    except Exception:
        logger.debug("3D layer preview render failed", exc_info=True)
        return None


def refresh_3d_layer_preview(
    layer: Any,
    *,
    width: int | None = None,
    height: int | None = None,
    bump_texture: bool = False,
) -> bool:
    """Render and store the layer preview image in ``layer.image``."""

    rendered = render_3d_layer(layer, width=width, height=height)
    if rendered is None:
        return False
    layer.image = rendered.image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    if bump_texture:
        layer.texture_version = int(getattr(layer, "texture_version", 0)) + 1
    return True


def preview_display_mode(value: Any) -> Any:
    from loomstroke.rendering_3d import PreviewDisplayMode  # noqa: PLC0415

    return {
        "solid": PreviewDisplayMode.SOLID,
        "wireframe": PreviewDisplayMode.WIREFRAME,
        "textured": PreviewDisplayMode.TEXTURED,
        "pbr": PreviewDisplayMode.TEXTURED,
        "normal": PreviewDisplayMode.NORMAL_DEBUG,
        "normal_debug": PreviewDisplayMode.NORMAL_DEBUG,
    }.get(str(value or "solid").strip().lower(), PreviewDisplayMode.SOLID)


def orbit_camera_state(value: Any, scene: Any) -> Any:
    from loomstroke.rendering_3d import OrbitCameraState  # noqa: PLC0415

    if isinstance(value, OrbitCameraState):
        return value
    if isinstance(value, Mapping):
        return OrbitCameraState(
            yaw_degrees=float(value.get("yaw_degrees", value.get("yaw", 35.0))),
            pitch_degrees=float(value.get("pitch_degrees", value.get("pitch", -20.0))),
            zoom=float(value.get("zoom", 1.0)),
            target=_target_tuple(value.get("target", (0.0, 0.0, 0.0))),
            distance=float(value.get("distance", 4.0)),
        )
    return OrbitCameraState.fit_to_scene(scene)


def _render_size(value: int | None, image: Any, *, axis: str) -> int:
    if value is not None:
        return max(1, int(value))
    if isinstance(image, QImage) and not image.isNull():
        return max(1, int(image.width() if axis == "width" else image.height()))
    return 512


def _target_tuple(value: Any) -> tuple[float, float, float]:
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        return (float(value[0]), float(value[1]), float(value[2]))
    return (0.0, 0.0, 0.0)
