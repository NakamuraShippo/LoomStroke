"""
Command pattern implementation for LoomStroke.

All drawing operations must go through Commands to ensure
consistent undo/redo and history management.
This is the core contract for all plugins per Master.md specifications.
"""

# ruff: noqa: I001,F401

from __future__ import annotations

import logging
import math
from abc import ABC, abstractmethod  # noqa: F401
from collections.abc import Mapping, Sequence
from dataclasses import dataclass  # noqa: F401
from enum import Enum  # noqa: F401
from typing import TYPE_CHECKING, Any, TypeAlias, cast  # noqa: F401

import numpy as np
from numpy.typing import NDArray

from . import command_layer_helpers  # noqa: F401
from .color_state import ColorDescriptor, ColorState, ColorStateSnapshot
from .command_image_utils import _qimage_from_bgra  # noqa: F401
from .command_image_utils import _unpremultiply_rgba  # noqa: F401
from .command_image_utils import (
    _affine_bounds,
    _composite_nonpremultiplied,
    _composite_premultiplied,
    _ensure_qimage_premultiplied,
    _is_qimage_layer,
    _numpy_bgra_to_rgba,
    _numpy_rgba_to_bgra,
    _place_mask_on_canvas,
    _premultiply_rgba,
    _qimage_to_numpy_copy,
    _qimage_to_numpy_view,
    _union_bounds,
)
from .command_layer_access import _bump_layer_mask_version  # noqa: F401
from .command_layer_access import _layer_mask_to_local  # noqa: F401
from .command_layer_access import _mask_size_matches_layer_image  # noqa: F401
from .command_layer_access import (
    _canvas_dpi,
    _canvas_size_tuple,
    _coerce_layer_transform,
    _ensure_layer_bounds,
    _layer_allows_edit,
    _layer_offset,
    _layer_transform,
    _resolve_layer_by_id,
    _resolve_target_layer,
    _set_layer_transform,
    _shift_layer_mask,
    _supports_layer_expansion,
    _sync_layer_transform_translation,
)
from .command_types import CompositeCommand  # noqa: F401
from .command_types import (
    SELECTION_MASK_LAYER_ID,
    BlendMode,
    BrushSettings,
    Command,
    DirtyRegion,
    LayerTransform,
    SelectionOp,
    SelectionRect,
    SelectionSnapshot,
    StrokePoint,
)
from .commands_color import SetActiveColorCommand
from .commands_filter import (
    AddFilterLayerCommand,
    ModifyFilterParamsCommand,
    RemoveFilterLayerCommand,
)
from .commands_image import ApplyImageCommand, FilterCommand, TransformCommand
from .commands_layer_3d import (
    SetLayer3DCameraCommand,
    SetLayer3DDisplayModeCommand,
    SetLayer3DOffsetCommand,
    SetLayer3DSceneCommand,
)
from .commands_layer_transform import (
    LayerVisibilityCommand,
    MoveLayerCommand,
    TransformLayerCommand,
    _translate_vec_obj,
)
from .commands_layer_tree import (
    AddEffectCommand,
    AddLayerCommand,
    AddLayerMaskCommand,
    DeleteLayerCommand,
    DuplicateLayerCommand,
    GroupLayersCommand,
    ModifyEffectParamsCommand,
    RemoveEffectCommand,
    RemoveLayerMaskCommand,
    ReparentLayerCommand,
    SetLayerMaskCommand,
    ToggleClippingCommand,
    UngroupLayerCommand,
)
from .commands_selection_content import (
    CutSelectionCommand,
    PasteSelectionCommand,
    TransformSelectionCommand,
    _selection_payload_image_numpy,
    _selection_payload_mask_numpy,
    _transform_payload_qimage,
    _transform_payload_raster,
)
from .commands_selection_mask import (
    _MAX_SELECTION_RADIUS,
    ApplySelectionMaskCommand,
    ModifySelectionMaskCommand,
    UpdateSelectionMaskCommand,
    _clamp_radius,
    _clip_bounds_to_canvas,
    _get_selection_full_bounds,
    _get_selection_full_mask,
    _normalize_bounds,
    _set_selection_full_bounds,
    _set_selection_full_mask,
)
from .commands_stroke import (
    _HARDNESS_MASK_CACHE,
    _TIP_ALPHA_CACHE,
    _TIP_MASK_CACHE,
    EraseCommand,
    PaintCommand,
    StampBrushCommand,
    _load_tip_alpha,
    _load_tip_mask,
    _radial_falloff_mask,
    _tone_color_for_pressure,
    _tone_params_from_brush,
    invalidate_tip_cache,
)
from .event_bus import EventBus
from .events import ColorChangeEvent, Events
from .geometry import Affine2D
from .selection_clipboard import SelectionPayload

logger = logging.getLogger(__name__)
__annotations__ = {
    "SelectionRect": "TypeAlias",
    "_TIP_ALPHA_CACHE": "dict[tuple[str, int], NDArray[Any]]",
    "_HARDNESS_MASK_CACHE": "dict[tuple[int, float, str], NDArray[Any]]",
    "_TIP_MASK_CACHE": "dict[tuple[str, int, float, str], NDArray[Any]]",
}

if TYPE_CHECKING:
    from .canvas import Canvas
    from .layer import Layer

__all__ = (
    "SELECTION_MASK_LAYER_ID",
    "_HARDNESS_MASK_CACHE",
    "_MAX_SELECTION_RADIUS",
    "_TIP_ALPHA_CACHE",
    "_TIP_MASK_CACHE",
    "AddEffectCommand",
    "AddFilterLayerCommand",
    "AddLayerCommand",
    "AddLayerMaskCommand",
    "Affine2D",
    "ApplyImageCommand",
    "ApplySelectionMaskCommand",
    "BlendMode",
    "BrushSettings",
    "ColorChangeEvent",
    "ColorDescriptor",
    "ColorState",
    "ColorStateSnapshot",
    "Command",
    "CompositeCommand",
    "CutSelectionCommand",
    "DeleteLayerCommand",
    "DirtyRegion",
    "DuplicateLayerCommand",
    "EraseCommand",
    "EventBus",
    "Events",
    "FilterCommand",
    "GroupLayersCommand",
    "LayerTransform",
    "LayerVisibilityCommand",
    "ModifyEffectParamsCommand",
    "ModifyFilterParamsCommand",
    "ModifySelectionMaskCommand",
    "MoveLayerCommand",
    "PaintCommand",
    "PasteSelectionCommand",
    "RemoveEffectCommand",
    "RemoveFilterLayerCommand",
    "RemoveLayerMaskCommand",
    "ReparentLayerCommand",
    "SelectionOp",
    "SelectionPayload",
    "SelectionRect",
    "SelectionSnapshot",
    "SetActiveColorCommand",
    "SetLayer3DCameraCommand",
    "SetLayer3DDisplayModeCommand",
    "SetLayer3DOffsetCommand",
    "SetLayer3DSceneCommand",
    "SetLayerMaskCommand",
    "StampBrushCommand",
    "StrokePoint",
    "ToggleClippingCommand",
    "TransformCommand",
    "TransformLayerCommand",
    "TransformSelectionCommand",
    "UngroupLayerCommand",
    "UpdateSelectionMaskCommand",
    "_affine_bounds",
    "_bump_layer_mask_version",
    "_canvas_dpi",
    "_canvas_size_tuple",
    "_clamp_radius",
    "_clip_bounds_to_canvas",
    "_coerce_layer_transform",
    "_composite_nonpremultiplied",
    "_composite_premultiplied",
    "_ensure_layer_bounds",
    "_ensure_qimage_premultiplied",
    "_get_selection_full_bounds",
    "_get_selection_full_mask",
    "_is_qimage_layer",
    "_layer_allows_edit",
    "_layer_mask_to_local",
    "_layer_offset",
    "_layer_transform",
    "_load_tip_alpha",
    "_load_tip_mask",
    "_mask_size_matches_layer_image",
    "_normalize_bounds",
    "_numpy_bgra_to_rgba",
    "_numpy_rgba_to_bgra",
    "_place_mask_on_canvas",
    "_premultiply_rgba",
    "_qimage_from_bgra",
    "_qimage_to_numpy_copy",
    "_qimage_to_numpy_view",
    "_radial_falloff_mask",
    "_resolve_layer_by_id",
    "_resolve_target_layer",
    "_selection_payload_image_numpy",
    "_selection_payload_mask_numpy",
    "_set_layer_transform",
    "_set_selection_full_bounds",
    "_set_selection_full_mask",
    "_shift_layer_mask",
    "_supports_layer_expansion",
    "_sync_layer_transform_translation",
    "_tone_color_for_pressure",
    "_tone_params_from_brush",
    "_transform_payload_qimage",
    "_transform_payload_raster",
    "_translate_vec_obj",
    "_union_bounds",
    "_unpremultiply_rgba",
    "command_layer_helpers",
    "invalidate_tip_cache",
)
