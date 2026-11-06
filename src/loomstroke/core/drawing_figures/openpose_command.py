"""Create and select a directly projected OpenPose image with one history entry."""

from typing import Any

from ..add_generated_image_layer_command import AddGeneratedImageLayerCommand
from .add_command import select_created_layer


class AddFigureOpenPoseLayerCommand(AddGeneratedImageLayerCommand):
    def __init__(self, pixels: Any) -> None:
        super().__init__(pixels, "OpenPose", {"source": "drawing_figure_openpose"})

    def apply(self, canvas: Any) -> bool:
        previous = canvas.current_layer()
        succeeded = super().apply(canvas)
        if succeeded:
            select_created_layer(canvas, canvas._get_layer_by_id(self.layer_id), previous)
        return succeeded
