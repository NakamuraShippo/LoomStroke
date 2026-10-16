"""Use ordinary content movement with exact restoration of page-sized masks."""

from PySide6.QtGui import QImage

from loomstroke.core.command_types import LayerTransform
from loomstroke.core.commands_layer_transform import MoveLayerCommand


def translated_mask(mask, dx, dy):
    return mask.copy(-int(dx), -int(dy), mask.width(), mask.height())


class PanelContentMoveCommand(MoveLayerCommand):
    """Preserve clipped-away coverage for Undo instead of inverse-shifting it."""

    def apply(self, canvas):
        root = canvas._get_layer_by_id(self.layer_id)
        if root is None:
            return False
        self._mask_sources = []
        self._raster_transforms = []

        def visit(layer):
            if layer.layer_type == "raster" and layer.transform != LayerTransform.identity():
                self._raster_transforms.append((layer, layer.transform))
            for name in ("layer_mask", "effect_mask"):
                mask = getattr(layer, name, None)
                if mask is not None:
                    self._mask_sources.append((layer, name, QImage(mask)))
            for child in layer.children:
                visit(child)

        visit(root)
        if not super().apply(canvas):
            return False
        for layer, transform in self._raster_transforms:
            layer.transform = transform.with_translation(
                transform.tx + self.dx, transform.ty + self.dy
            )
        for layer, name, mask in self._mask_sources:
            setattr(layer, name, translated_mask(mask, self.dx, self.dy))
        canvas.bump_layer_texture_versions([layer.id for layer, _, _ in self._mask_sources])
        return True

    def revert(self, canvas):
        if not self._executed:
            return False
        current_transforms = [(layer, layer.transform) for layer, _ in self._raster_transforms]
        if not super().revert(canvas):
            return False
        for layer, transform in current_transforms:
            layer.transform = transform.with_translation(
                transform.tx - self.dx, transform.ty - self.dy
            )
        for layer, name, mask in self._mask_sources:
            setattr(layer, name, QImage(mask))
        canvas.bump_layer_texture_versions([layer.id for layer, _, _ in self._mask_sources])
        return True
