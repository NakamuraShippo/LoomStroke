"""Command-backed creation of built-in 3D sketching primitives."""

from __future__ import annotations

from typing import Any

from loomstroke.rendering_3d.cameras import OrbitCameraState

from .assets_3d.primitive_shapes import (
    PRIMITIVE_KINDS,
    PRIMITIVE_NAMES,
    create_primitive_scene,
)
from .commands_layer_tree import AddLayerCommand


class AddPrimitive3DCommand(AddLayerCommand):
    """Add one editable 3D layer using standard layer history."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        super().__init__(PRIMITIVE_NAMES.get(kind, str(kind)), layer_type="3d")

    @property
    def layer_id(self) -> str | None:
        return self._created.layer.id if self._created.layer is not None else None

    @property
    def retained_bytes(self) -> int:
        scene = getattr(self._created.layer, "scene_3d", None)
        if scene is None:
            return super().retained_bytes
        arrays = {
            id(array): array
            for mesh in scene.meshes
            for primitive in mesh.primitives
            for array in (primitive.vertices, primitive.indices, primitive.normals, primitive.uvs)
            if array is not None
        }
        return super().retained_bytes + sum(array.nbytes for array in arrays.values())

    def apply(self, canvas: Any) -> bool:
        if (
            self.kind not in PRIMITIVE_KINDS
            or getattr(canvas, "_in_transaction", False)
            or not self._created.supports(canvas)
        ):
            return False
        if any(
            getattr(layer, "native_cmyk", None) is not None for layer in canvas._flatten_layers()
        ):
            return False
        previous = canvas.current_layer()
        if self._created.layer is not None:
            succeeded = self._created.restore(canvas)
        else:
            scene = create_primitive_scene(self.kind)
            camera = OrbitCameraState.fit_to_scene(scene)
            succeeded = self._created.create(
                canvas,
                self._name,
                "3d",
                {
                    "scene_3d": scene,
                    "display_mode_3d": "solid",
                    "camera_3d": {
                        "yaw_degrees": float(camera.yaw_degrees),
                        "pitch_degrees": float(camera.pitch_degrees),
                        "zoom": float(camera.zoom),
                        "target": list(camera.target),
                        "distance": float(camera.distance),
                    },
                },
            )
        if succeeded:
            layer = self._created.layer
            if canvas.current_layer().id != layer.id:
                canvas.set_active_layer(layer.id)
            else:
                canvas._emit_layer_selected(previous, layer)
        self._executed = succeeded
        return succeeded
