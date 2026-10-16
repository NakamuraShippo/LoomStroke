"""3D layer state commands."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from .command_layer_access import _resolve_layer_by_id, _sync_layer_transform_translation
from .command_types import Command

if TYPE_CHECKING:
    from .canvas import Canvas

logger = logging.getLogger("loomstroke.core.commands")


class SetLayer3DSceneCommand(Command):
    """Replace an existing raster/3D layer with an editable 3D scene."""

    def __init__(
        self,
        layer_id: str,
        *,
        scene: Any,
        camera: Any = None,
        lighting: Any = None,
        display_mode: str = "solid",
        gen_3d: Any = None,
        origin: Any = None,
        name: str | None = None,
        merge_key: str | None = None,
    ) -> None:
        super().__init__(merge_key=merge_key)
        self.layer_id = layer_id
        self._scene = scene
        self._camera = camera
        self._lighting = lighting
        self._display_mode = str(display_mode or "solid")
        self._gen_3d = gen_3d
        self._origin = origin
        self._name = name
        self._old_state: dict[str, Any] | None = None

    def apply(self, canvas: Canvas) -> bool:
        layer = _resolve_scene_target_layer(canvas, self.layer_id)
        if layer is None or self._scene is None:
            return False
        if self._old_state is None:
            self._old_state = _capture_layer_state(layer)

        if self._name:
            layer.name = self._name
        layer.layer_type = "3d"
        layer.vector_kind = "draw"
        layer.vector_objects = []
        layer.scene_3d = self._scene
        layer.camera_3d = self._camera
        layer.lighting_3d = self._lighting
        layer.display_mode_3d = self._display_mode
        layer.gen_3d = self._gen_3d
        layer.origin = self._origin
        layer.image = _transparent_canvas_image(canvas, layer)
        _refresh_3d_layer_preview(canvas, layer)
        self._executed = True
        return True

    def revert(self, canvas: Canvas) -> bool:
        layer = _resolve_layer_by_id(canvas, self.layer_id)
        if layer is None or self._old_state is None:
            return False
        _restore_layer_state(layer, self._old_state)
        _invalidate_canvas(canvas)
        self._executed = False
        return True


class SetLayer3DCameraCommand(Command):
    """Update a 3D layer camera with undo support."""

    def __init__(
        self,
        layer_id: str,
        camera: Mapping[str, Any],
        *,
        old_camera: Mapping[str, Any] | None = None,
        merge_key: str | None = None,
    ) -> None:
        super().__init__(merge_key=merge_key)
        self.layer_id = layer_id
        self._new_camera = dict(camera)
        self._provided_old_camera = dict(old_camera) if old_camera is not None else None
        self._old_camera: Any = None
        self._had_old_camera = False

    def apply(self, canvas: Canvas) -> bool:
        layer = _resolve_3d_layer(canvas, self.layer_id)
        if layer is None:
            return False
        current_camera = getattr(layer, "camera_3d", None)
        self._old_camera = (
            dict(self._provided_old_camera)
            if self._provided_old_camera is not None
            else current_camera
        )
        self._had_old_camera = True
        if current_camera == self._new_camera:
            if self._provided_old_camera is None or self._old_camera == self._new_camera:
                return False
            _refresh_3d_layer_preview(canvas, layer)
            self._executed = True
            return True
        if self._old_camera == self._new_camera:
            return False
        layer.camera_3d = dict(self._new_camera)
        _refresh_3d_layer_preview(canvas, layer)
        self._executed = True
        return True

    def revert(self, canvas: Canvas) -> bool:
        layer = _resolve_3d_layer(canvas, self.layer_id)
        if layer is None or not self._had_old_camera:
            return False
        layer.camera_3d = self._old_camera
        _refresh_3d_layer_preview(canvas, layer)
        self._executed = False
        return True


class SetLayer3DOffsetCommand(Command):
    """Update a 3D layer canvas-space offset with undo support."""

    def __init__(
        self,
        layer_id: str,
        offset: tuple[int, int] | list[int],
        *,
        old_offset: tuple[int, int] | list[int] | None = None,
        merge_key: str | None = None,
    ) -> None:
        super().__init__(merge_key=merge_key)
        self.layer_id = layer_id
        self._new_offset = (int(offset[0]), int(offset[1]))
        self._provided_old_offset = (
            (int(old_offset[0]), int(old_offset[1])) if old_offset is not None else None
        )
        self._old_offset: tuple[int, int] | None = None

    def apply(self, canvas: Canvas) -> bool:
        layer = _resolve_3d_layer(canvas, self.layer_id)
        if layer is None:
            return False
        current = (
            int(getattr(layer, "offset_x", 0)),
            int(getattr(layer, "offset_y", 0)),
        )
        self._old_offset = self._provided_old_offset or current
        if current == self._new_offset:
            if self._provided_old_offset is None or self._old_offset == self._new_offset:
                return False
            _sync_layer_transform_translation(layer)
            _refresh_3d_layer_preview(canvas, layer)
            self._executed = True
            return True
        layer.offset_x, layer.offset_y = self._new_offset
        _sync_layer_transform_translation(layer)
        _refresh_3d_layer_preview(canvas, layer)
        self._executed = True
        return True

    def revert(self, canvas: Canvas) -> bool:
        layer = _resolve_3d_layer(canvas, self.layer_id)
        if layer is None or self._old_offset is None:
            return False
        layer.offset_x, layer.offset_y = self._old_offset
        _sync_layer_transform_translation(layer)
        _refresh_3d_layer_preview(canvas, layer)
        self._executed = False
        return True


class SetLayer3DDisplayModeCommand(Command):
    """Update a 3D layer display mode with undo support."""

    def __init__(
        self,
        layer_id: str,
        display_mode: str,
        *,
        merge_key: str | None = None,
    ) -> None:
        super().__init__(merge_key=merge_key)
        self.layer_id = layer_id
        self._new_display_mode = str(display_mode or "solid")
        self._old_display_mode: str | None = None
        self._old_scene: Any = None
        self._scene_backfilled = False

    def apply(self, canvas: Canvas) -> bool:
        layer = _resolve_3d_layer(canvas, self.layer_id)
        if layer is None:
            return False
        self._old_display_mode = str(getattr(layer, "display_mode_3d", "solid") or "solid")
        backfilled_scene = (
            _maybe_backfill_generated_scene_textures(layer)
            if _display_mode_wants_texture(self._new_display_mode)
            else None
        )
        if backfilled_scene is not None:
            if not self._scene_backfilled:
                self._old_scene = getattr(layer, "scene_3d", None)
            layer.scene_3d = backfilled_scene
            self._scene_backfilled = True

        if self._old_display_mode == self._new_display_mode and backfilled_scene is None:
            return False
        layer.display_mode_3d = self._new_display_mode
        _refresh_3d_layer_preview(canvas, layer)
        self._executed = True
        return True

    def revert(self, canvas: Canvas) -> bool:
        layer = _resolve_3d_layer(canvas, self.layer_id)
        if layer is None or self._old_display_mode is None:
            return False
        if self._scene_backfilled:
            layer.scene_3d = self._old_scene
        layer.display_mode_3d = self._old_display_mode
        _refresh_3d_layer_preview(canvas, layer)
        self._executed = False
        return True


def _resolve_3d_layer(canvas: Canvas, layer_id: str) -> Any | None:
    layer = _resolve_layer_by_id(canvas, layer_id)
    if layer is None or getattr(layer, "layer_type", "") != "3d":
        return None
    if bool(getattr(layer, "locked", False)):
        return None
    return layer


def _resolve_scene_target_layer(canvas: Canvas, layer_id: str) -> Any | None:
    layer = _resolve_layer_by_id(canvas, layer_id)
    if layer is None or bool(getattr(layer, "locked", False)):
        return None
    if getattr(layer, "layer_type", "raster") not in {"raster", "3d"}:
        return None
    return layer


def _capture_layer_state(layer: Any) -> dict[str, Any]:
    image = getattr(layer, "image", None)
    return {
        "name": getattr(layer, "name", None),
        "layer_type": getattr(layer, "layer_type", "raster"),
        "vector_kind": getattr(layer, "vector_kind", "draw"),
        "vector_objects": list(getattr(layer, "vector_objects", []) or []),
        "image": image.copy() if isinstance(image, QImage) else image,
        "scene_3d": getattr(layer, "scene_3d", None),
        "camera_3d": getattr(layer, "camera_3d", None),
        "lighting_3d": getattr(layer, "lighting_3d", None),
        "display_mode_3d": getattr(layer, "display_mode_3d", "solid"),
        "gen_3d": getattr(layer, "gen_3d", None),
        "origin": getattr(layer, "origin", None),
        "texture_version": int(getattr(layer, "texture_version", 0)),
    }


def _restore_layer_state(layer: Any, state: Mapping[str, Any]) -> None:
    layer.name = state.get("name") or getattr(layer, "name", "")
    layer.layer_type = state.get("layer_type", "raster")
    layer.vector_kind = state.get("vector_kind", "draw")
    layer.vector_objects = list(state.get("vector_objects") or [])
    image = state.get("image")
    layer.image = image.copy() if isinstance(image, QImage) else image
    layer.scene_3d = state.get("scene_3d")
    layer.camera_3d = state.get("camera_3d")
    layer.lighting_3d = state.get("lighting_3d")
    layer.display_mode_3d = str(state.get("display_mode_3d") or "solid")
    layer.gen_3d = state.get("gen_3d")
    layer.origin = state.get("origin")
    layer.texture_version = int(state.get("texture_version", 0)) + 1


def _display_mode_wants_texture(display_mode: str) -> bool:
    return str(display_mode or "").strip().lower() in {"textured", "pbr"}


def _maybe_backfill_generated_scene_textures(layer: Any) -> Any | None:
    scene = getattr(layer, "scene_3d", None)
    if _scene_has_renderable_base_color_texture(scene):
        return None
    generated_info = _generated_scene_info(layer)
    if generated_info is None:
        return None
    asset_id, artifact = generated_info

    try:
        loaded_scene = _load_generated_scene(asset_id, artifact)
    except Exception:
        logger.debug(
            "Failed to backfill generated 3D texture scene for layer %s",
            getattr(layer, "id", "<unknown>"),
            exc_info=True,
        )
        return None
    if not _scene_has_renderable_base_color_texture(loaded_scene):
        logger.debug(
            "Generated 3D artifact for layer %s has no renderable base color texture",
            getattr(layer, "id", "<unknown>"),
        )
        return None
    logger.info(
        "Backfilled generated 3D texture scene for layer %s from asset %s",
        getattr(layer, "id", "<unknown>"),
        asset_id,
    )
    return loaded_scene


def _generated_scene_info(layer: Any) -> tuple[str, str] | None:
    gen_3d = getattr(layer, "gen_3d", None)
    if not isinstance(gen_3d, Mapping):
        return None
    asset_id = str(gen_3d.get("asset_id") or "").strip()
    artifact = str(gen_3d.get("artifact") or "").strip()
    if not asset_id or not artifact:
        return None
    return asset_id, artifact


def _load_generated_scene(asset_id: str, artifact: str) -> Any:
    from .assets_3d import Generated3DAssetStore, load_scene  # noqa: PLC0415

    artifact_path = Generated3DAssetStore().artifact_path(asset_id, artifact)
    return load_scene(artifact_path)


def _scene_has_renderable_base_color_texture(scene: Any) -> bool:
    if scene is None:
        return False
    textures = {texture.id: texture for texture in getattr(scene, "textures", ())}
    textured_material_ids = {
        material.id
        for material in getattr(scene, "materials", ())
        if _texture_has_payload(textures.get(material.base_color_texture_id))
    }
    if not textured_material_ids:
        return False
    for mesh in getattr(scene, "meshes", ()):
        for primitive in getattr(mesh, "primitives", ()):
            if primitive.material_id in textured_material_ids and _primitive_has_uvs(primitive):
                return True
    return False


def _texture_has_payload(texture: Any) -> bool:
    if texture is None:
        return False
    metadata = getattr(texture, "metadata", None)
    data = metadata.get("data") if isinstance(metadata, dict) else None
    if isinstance(data, bytes) and data:
        return True
    if getattr(texture, "path", None) is not None:
        return True
    return getattr(texture, "color", None) is not None


def _primitive_has_uvs(primitive: Any) -> bool:
    uvs = getattr(primitive, "uvs", None)
    if uvs is None:
        return False
    try:
        expected = (len(primitive.vertices), 2)
    except (AttributeError, TypeError):
        return False
    return getattr(uvs, "shape", None) == expected


def _refresh_3d_layer_preview(canvas: Canvas, layer: Any) -> None:
    layer.texture_version = int(getattr(layer, "texture_version", 0)) + 1
    _invalidate_canvas(canvas)


def _transparent_canvas_image(canvas: Canvas, layer: Any) -> QImage:
    existing = getattr(layer, "image", None)
    width = int(getattr(canvas, "_width", 0) or getattr(existing, "width", lambda: 0)() or 1)
    height = int(getattr(canvas, "_height", 0) or getattr(existing, "height", lambda: 0)() or 1)
    image = QImage(
        max(1, width),
        max(1, height),
        QImage.Format.Format_ARGB32_Premultiplied,
    )
    image.fill(Qt.GlobalColor.transparent)
    return image


def _invalidate_canvas(canvas: Canvas) -> None:
    invalidate = getattr(canvas, "_invalidate_layer_cache", None)
    if callable(invalidate):
        invalidate()
    recompose = getattr(canvas, "_recompose", None)
    if callable(recompose):
        recompose()
