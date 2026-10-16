"""Prepared edge updates tolerate only empty padding retained by brush Undo."""

import numpy as np
from PySide6.QtGui import QImage

from loomstroke.core.command_types import LayerTransform

from .layer_patch_command import PanelLayerPatchCommand


class PanelRasterPatchCommand(PanelLayerPatchCommand):
    def _install(self, canvas, expected, values):
        layer = canvas._get_layer_by_id(self.layer_id)
        image = expected.get("image")
        if (
            layer is not None
            and isinstance(image, QImage)
            and isinstance(layer.image, QImage)
            and layer.transform == LayerTransform.identity()
            and expected.get("transform") == LayerTransform.identity()
            and {"offset_x", "offset_y"} <= expected.keys()
        ):
            dx, dy = expected["offset_x"] - layer.offset_x, expected["offset_y"] - layer.offset_y
            current = layer.image
            if (
                dx >= 0
                and dy >= 0
                and dx + image.width() <= current.width()
                and dy + image.height() <= current.height()
                and current.copy(dx, dy, image.width(), image.height()) == image
            ):
                rgba = current.convertToFormat(QImage.Format.Format_RGBA8888)
                array = np.frombuffer(rgba.constBits(), np.uint8).reshape(
                    rgba.height(), rgba.bytesPerLine()
                )
                pixels = array[:, : rgba.width() * 4].reshape(rgba.height(), rgba.width(), 4)
                empty = (
                    not np.any(pixels[:dy])
                    and not np.any(pixels[dy + image.height() :])
                    and not np.any(pixels[dy : dy + image.height(), :dx])
                    and not np.any(pixels[dy : dy + image.height(), dx + image.width() :])
                )
                if empty:
                    expected = dict(
                        expected, image=current, offset_x=layer.offset_x, offset_y=layer.offset_y
                    )
        return super()._install(canvas, expected, values)
