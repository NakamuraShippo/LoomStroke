"""Command routing helper for ``SimpleCanvasService``."""

from __future__ import annotations

import logging
from typing import Any, cast

from loomstroke.core.commands import (
    AddFilterLayerCommand,
    AddLayerCommand,
    AddLayerMaskCommand,
    ApplyImageCommand,
    ApplySelectionMaskCommand,
    Command,
    CutSelectionCommand,
    DeleteLayerCommand,
    DuplicateLayerCommand,
    EraseCommand,
    GroupLayersCommand,
    LayerVisibilityCommand,
    ModifyFilterParamsCommand,
    ModifySelectionMaskCommand,
    MoveLayerCommand,
    PaintCommand,
    PasteSelectionCommand,
    RemoveFilterLayerCommand,
    RemoveLayerMaskCommand,
    ReparentLayerCommand,
    SetActiveColorCommand,
    SetLayer3DCameraCommand,
    SetLayer3DDisplayModeCommand,
    SetLayer3DOffsetCommand,
    SetLayer3DSceneCommand,
    SetLayerMaskCommand,
    StampBrushCommand,
    ToggleClippingCommand,
    TransformCommand,
    TransformLayerCommand,
    TransformSelectionCommand,
    UngroupLayerCommand,
    UpdateSelectionMaskCommand,
)
from loomstroke.core.commands_layer_image_replace import ReplaceLayerImageCommand
from loomstroke.core.commands_layer_metadata_key import SetLayerMetadataKeyCommand
from loomstroke.core.fill.commands_fill import (
    FloodFillCommand,
    FloodFillToNewLayerCommand,
    PatternFillCommand,
)
from loomstroke.core.filter_pen.command import (
    FilterPenStrokeCommand,
    submit_filter_pen_command,
)
from loomstroke.core.pen_taper_command import PenTaperCommand
from loomstroke.core.recontour.commands import RecontourCommand
from loomstroke.core.screentone.create_screentone_command import CreateScreentoneCommand
from loomstroke.plugins.basic_gradient.gradient_command import GradientCommand

logger = logging.getLogger("loomstroke.core.canvas_service_simple")


class CanvasCommandRoutingService:
    """Route commands to the existing ``SimpleCanvasService`` submit handlers."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def submit(self, cmd: Command, *, merge_key: str | None = None) -> bool:
        del merge_key
        owner = self._owner
        filter_pen = getattr(
            owner, "_filter_pen_service", getattr(owner, "_effect_pen_service", None)
        )
        if filter_pen is not None:
            filter_pen.before_command(cmd)
        handlers = (
            (PenTaperCommand, lambda command: command.apply(owner)),
            (FilterPenStrokeCommand, lambda command: submit_filter_pen_command(owner, command)),
            (RecontourCommand, lambda command: command.apply(owner)),
            (SetActiveColorCommand, owner._submit_set_active_color),
            (PaintCommand, owner._submit_paint),
            (StampBrushCommand, owner._submit_stamp_brush),
            (EraseCommand, owner._submit_erase),
            (
                (FloodFillCommand, FloodFillToNewLayerCommand, PatternFillCommand),
                owner._submit_flood_fill,
            ),
            (ApplyImageCommand, owner._apply_image_command),
            (GradientCommand, owner._submit_gradient),
            (UpdateSelectionMaskCommand, owner._submit_update_selection_mask),
            (ApplySelectionMaskCommand, owner._submit_apply_selection_mask),
            (ModifySelectionMaskCommand, owner._submit_modify_selection_mask),
            (CutSelectionCommand, owner._submit_cut_selection),
            (PasteSelectionCommand, owner._submit_paste_selection),
            (TransformSelectionCommand, owner._submit_transform_selection),
            (MoveLayerCommand, owner._submit_move_layer),
            (LayerVisibilityCommand, owner._submit_layer_visibility),
            (TransformLayerCommand, owner._submit_transform_layer),
            (TransformCommand, owner._submit_transform),
        )
        for expected, handler in handlers:
            if isinstance(cmd, expected):
                return cast("bool", handler(cmd))

        if owner._is_vector_command(cmd):
            return cast("bool", owner._apply_vector_command(cmd))

        from loomstroke.plugins.magic_wand.magic_wand_command import (  # noqa: PLC0415
            MagicWandCommand,
        )

        if isinstance(cmd, MagicWandCommand):
            return cast("bool", owner._submit_magic_wand(cmd))

        from loomstroke.plugins.vector_tools.fill_to_selection_command import (  # noqa: PLC0415
            VectorFillToSelectionCommand,
        )

        if isinstance(cmd, VectorFillToSelectionCommand):
            return cast("bool", owner._submit_vector_fill_to_selection(cmd))

        tree_types = (
            CreateScreentoneCommand,
            ToggleClippingCommand,
            AddFilterLayerCommand,
            RemoveFilterLayerCommand,
            ModifyFilterParamsCommand,
            AddLayerMaskCommand,
            RemoveLayerMaskCommand,
            SetLayerMaskCommand,
            GroupLayersCommand,
            UngroupLayerCommand,
            AddLayerCommand,
            DeleteLayerCommand,
            DuplicateLayerCommand,
            ReparentLayerCommand,
            SetLayer3DCameraCommand,
            SetLayer3DDisplayModeCommand,
            SetLayer3DOffsetCommand,
            SetLayer3DSceneCommand,
            ReplaceLayerImageCommand,
            SetLayerMetadataKeyCommand,
        )
        tree_or_sam3_submitted = _submit_tree_or_sam3_command(owner, cmd, tree_types)
        if tree_or_sam3_submitted is not None:
            return tree_or_sam3_submitted

        logger.warning("Unsupported command type: %s", type(cmd).__name__)
        return False


def _submit_tree_or_sam3_command(
    owner: Any,
    cmd: Any,
    tree_types: tuple[type[Any], ...],
) -> bool | None:
    if isinstance(cmd, tree_types):
        return cast("bool", owner._submit_tree_command(cmd))
    if _is_sam3_ai_effect_mask_slot_binding_command(cmd):
        return cast(
            "bool",
            owner._simple_layer_command_submit().submit(
                cmd,
                "SAM3 AI Effect Mask Slot",
            ),
        )
    return None


def _is_sam3_ai_effect_mask_slot_binding_command(cmd: Any) -> bool:
    return getattr(cmd, "sam3_command_kind", None) == "ai_effect_mask_slot_binding"
