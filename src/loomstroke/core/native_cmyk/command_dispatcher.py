"""Route supported edits to native inks and reject RGB-only mutation paths."""

from __future__ import annotations

import logging

from loomstroke.core import commands
from loomstroke.core.commands_layer_metadata_key import SetLayerMetadataKeyCommand
from loomstroke.core.commands_layer_rename import RenameLayerCommand
from loomstroke.core.fill.commands_fill import FloodFillCommand
from loomstroke.plugins.basic_gradient.gradient_command import GradientCommand

from .convert_command import ConvertNativeCmykCommand
from .image_import_command import NativeCmykImageImportCommand
from .patch_command import NativeCmykPatchCommand

logger = logging.getLogger(__name__)


class NativeCmykCommandDispatcher:
    @staticmethod
    def submit(canvas, command) -> bool | None:  # noqa: PLR0911 - command dispatch cases
        service = getattr(canvas, "_native_cmyk_service", None)
        if service is None:
            if not any(
                getattr(layer, "native_cmyk", None) is not None
                for layer in canvas._layer_tree().flatten_layers()
            ):
                return None
            service = canvas.native_cmyk()
        if service.profile() is None:
            return None
        if isinstance(command, NativeCmykImageImportCommand):
            return canvas._tree_command_submit().submit(command)
        if isinstance(command, (NativeCmykPatchCommand, ConvertNativeCmykCommand)):
            return canvas._simple_layer_command_submit().submit(command, "CMYK Edit")
        layer = canvas._get_active_layer()
        if canvas.channels().is_alpha(layer) or canvas._mask_edit_flag:
            return None
        service.selected = canvas.channels().selected
        if isinstance(command, (commands.PaintCommand, commands.StampBrushCommand)):
            return service.paint(command)
        if isinstance(command, commands.EraseCommand):
            return service.paint(command, erase=True)
        if type(command) is FloodFillCommand:
            return service.fill(command)
        if isinstance(command, GradientCommand):
            return service.gradient(command)
        safe = (
            commands.UpdateSelectionMaskCommand,
            commands.ApplySelectionMaskCommand,
            commands.ModifySelectionMaskCommand,
            commands.MoveLayerCommand,
            commands.LayerVisibilityCommand,
            commands.AddLayerCommand,
            commands.DeleteLayerCommand,
            commands.DuplicateLayerCommand,
            commands.GroupLayersCommand,
            commands.UngroupLayerCommand,
            commands.ReparentLayerCommand,
            commands.AddLayerMaskCommand,
            commands.RemoveLayerMaskCommand,
            commands.SetLayerMaskCommand,
            commands.SetActiveColorCommand,
            commands.ToggleClippingCommand,
            SetLayerMetadataKeyCommand,
            RenameLayerCommand,
        )
        if isinstance(command, safe):
            return None
        logger.warning("%s cannot edit native CMYK pixels", type(command).__name__)
        return False
