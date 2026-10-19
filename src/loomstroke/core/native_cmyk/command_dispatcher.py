"""Route supported edits to native inks and reject RGB-only mutation paths."""

from __future__ import annotations

import logging

from loomstroke.core import commands
from loomstroke.core.commands_layer_metadata_key import SetLayerMetadataKeyCommand
from loomstroke.core.commands_layer_rename import RenameLayerCommand
from loomstroke.core.commands_layer_transform import TransformLayerCommand
from loomstroke.core.fill.commands_fill import (
    FloodFillCommand,
    FloodFillToNewLayerCommand,
    PatternFillCommand,
)
from loomstroke.core.fill.prepared_fill_command import PreparedFillCommand
from loomstroke.core.filter_pen.command import FilterPenStrokeCommand
from loomstroke.core.pen_taper_command import PenTaperCommand
from loomstroke.core.recontour.commands import RecontourCommand
from loomstroke.plugins.basic_gradient.gradient_command import GradientCommand
from loomstroke.plugins.magic_wand.magic_wand_command import MagicWandCommand

from .convert_command import ConvertNativeCmykCommand
from .image_import_command import NativeCmykImageImportCommand
from .patch_command import NativeCmykPatchCommand

logger = logging.getLogger(__name__)


class NativeCmykCommandDispatcher:
    @staticmethod
    def submit(canvas, command) -> bool | None:  # noqa: PLR0911, PLR0912 - dispatch cases
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
        if isinstance(command, (PenTaperCommand, RecontourCommand)):
            return command.apply(canvas)
        if isinstance(command, (commands.PaintCommand, commands.StampBrushCommand)):
            return service.paint(command)
        if isinstance(command, commands.EraseCommand):
            return service.paint(command, erase=True)
        if type(command) in (FloodFillCommand, PatternFillCommand):
            return service.fill(command)
        if isinstance(command, (PreparedFillCommand, FloodFillToNewLayerCommand)):
            return bool(canvas._simple_layer_command_submit().submit(command, "CMYK Fill"))
        if isinstance(command, GradientCommand):
            return service.gradient(command)
        if isinstance(
            command,
            (
                commands.CutSelectionCommand,
                commands.PasteSelectionCommand,
                commands.TransformSelectionCommand,
            ),
        ):
            return bool(canvas._simple_layer_command_submit().submit(command, "CMYK Selection"))
        if isinstance(command, TransformLayerCommand):
            from dataclasses import astuple  # noqa: PLC0415
            from math import isfinite  # noqa: PLC0415

            transform = command.new_transform
            if (
                not all(isfinite(value) for value in astuple(transform))
                or transform.to_affine().inverse() is None
            ):
                return False
        if hasattr(command, "vector_object") or hasattr(command, "new_object"):
            from loomstroke.plugins.vector_tools.common import (  # noqa: PLC0415
                VectorCreateCommand,
                VectorReplaceCommand,
            )

            if isinstance(command, (VectorCreateCommand, VectorReplaceCommand)):
                return None
        safe = (
            TransformLayerCommand,
            MagicWandCommand,
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
            FilterPenStrokeCommand,
        )
        if isinstance(command, safe):
            return None
        logger.warning("%s cannot edit native CMYK pixels", type(command).__name__)
        return False
