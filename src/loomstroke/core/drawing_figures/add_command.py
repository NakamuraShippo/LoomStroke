"""Create a complete drawing figure through the existing layer creation history."""

from __future__ import annotations

from typing import Any

from ..commands_layer_tree import AddLayerCommand
from .commands import scene_retained_bytes
from .model import FigurePose
from .scene import build_scene


def select_created_layer(canvas: Any, layer: Any, previous: Any) -> None:
    if canvas.current_layer().id != layer.id:
        canvas.set_active_layer(layer.id)
    else:
        # CreatedLayerHistory activates before publishing ADDED. The UI still
        # needs SELECTED even when set_active_layer would treat it as a no-op.
        canvas._emit_layer_selected(previous, layer)


class AddDrawingFigureCommand(AddLayerCommand):
    def __init__(self, pose: FigurePose) -> None:
        self.pose = FigurePose.from_dict(pose.to_dict())
        super().__init__("BodyKun" if pose.model == "bodykun" else "BodyChan", layer_type="3d")

    @property
    def layer_id(self) -> str | None:
        return self._created.layer.id if self._created.layer is not None else None

    @property
    def retained_bytes(self) -> int:
        return super().retained_bytes + scene_retained_bytes(
            getattr(self._created.layer, "scene_3d", None)
        )

    def apply(self, canvas: Any) -> bool:
        if getattr(canvas, "_in_transaction", False) or not self._created.supports(canvas):
            return False
        if any(
            getattr(layer, "native_cmyk", None) is not None for layer in canvas._flatten_layers()
        ):
            return False
        previous = canvas.current_layer()
        if self._created.layer is not None:
            succeeded = self._created.restore(canvas)
        else:
            scene = build_scene(self.pose)
            low, high = scene.bounds()
            succeeded = self._created.create(
                canvas,
                self._name,
                "3d",
                {
                    "scene_3d": scene,
                    "display_mode_3d": "solid",
                    "lighting_3d": {"direction": [-0.4, -0.7, 1.0]},
                    "camera_3d": {
                        "yaw_degrees": 0.0,
                        "pitch_degrees": 0.0,
                        "zoom": 1.0,
                        "target": ((low + high) / 2).tolist(),
                        "distance": 4.0,
                    },
                    "gen_3d": {"drawing_figure": self.pose.to_dict()},
                },
            )
        if succeeded:
            select_created_layer(canvas, self._created.layer, previous)
        self._executed = succeeded
        return succeeded
