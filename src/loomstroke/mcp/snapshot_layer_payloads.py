"""Copy known snapshot values, never arbitrary renderer or backend objects."""

from dataclasses import fields, replace
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from types import MappingProxyType

import numpy as np
from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage

from loomstroke.core.appearance import LayerStyleStack
from loomstroke.core.appearance.layer_style_spec import LayerStyleSpec
from loomstroke.core.assets_3d.material import MaterialAsset
from loomstroke.core.assets_3d.mesh import MeshAsset, MeshPrimitive
from loomstroke.core.assets_3d.scene import (
    Scene3DCamera,
    Scene3DDocument,
    Scene3DLight,
    Scene3DNode,
    Transform3D,
)
from loomstroke.core.assets_3d.texture import TextureAsset
from loomstroke.core.command_types import LayerTransform
from loomstroke.core.geometry import Affine2D
from loomstroke.core.native_cmyk.pixels import NativeCmykPixels
from loomstroke.plugins.ai_effect.data_model import (
    AIEffectState,
    AIError,
    GenerationState,
    LastResult,
    LayerOrigin,
    SlotRef,
)
from loomstroke.plugins.ai_effect.errors import AIError as GatewayAIError
from loomstroke.plugins.vector_tools.common import Anchor, VectorObject, VectorPath, VectorStyle

from .snapshot_native_payloads import (
    detach_display_frame,
    detach_native,
    detach_native_preview,
    unsupported_snapshot_payload,
)

_VALUE_RECORDS = (
    LayerStyleStack,
    LayerStyleSpec,
    MaterialAsset,
    MeshAsset,
    MeshPrimitive,
    Scene3DCamera,
    Scene3DDocument,
    Scene3DLight,
    Scene3DNode,
    Transform3D,
    TextureAsset,
    LayerTransform,
    Affine2D,
    AIEffectState,
    AIError,
    GenerationState,
    LastResult,
    LayerOrigin,
    SlotRef,
    GatewayAIError,
    Anchor,
    VectorObject,
    VectorPath,
    VectorStyle,
)
_COPY_IN_PROGRESS = object()


def detach_value(value, memo):
    if value is None or isinstance(
        value,
        (
            str,
            bytes,
            int,
            float,
            bool,
            Enum,
            Path,
            date,
            datetime,
            np.integer,
            np.floating,
            np.bool_,
        ),
    ):
        return value
    if type(value) is NativeCmykPixels:
        return value
    key = id(value)
    if key in memo:
        if memo[key] is _COPY_IN_PROGRESS:
            raise unsupported_snapshot_payload()
        return memo[key]
    memo[key] = _COPY_IN_PROGRESS
    if isinstance(value, QImage):
        result = None if value.isNull() else value.copy()
    elif isinstance(value, np.ndarray):
        if value.dtype.hasobject:
            raise unsupported_snapshot_payload()
        result = value.copy()
    elif isinstance(value, (QColor, QRect)):
        result = type(value)(value)
    elif type(value) in (dict, MappingProxyType):
        result = {detach_value(key, memo): detach_value(item, memo) for key, item in value.items()}
    elif type(value) in (list, tuple, set, frozenset):
        result = type(value)(detach_value(item, memo) for item in value)
    elif type(value) in _VALUE_RECORDS:
        result = replace(
            value,
            **{
                field.name: detach_value(getattr(value, field.name), memo)
                for field in fields(value)
            },
        )
    else:
        raise unsupported_snapshot_payload()
    memo[key] = result
    return result


def layer_payload_changes(layer, memo):
    """Keep new scalar fields via replace; new mutable fields still need review."""
    return {
        name: detach_value(getattr(layer, name), memo)
        for name in (
            "image",
            "layer_mask",
            "effect_mask",
            "transform",
            "_preview_transform",
            "vector_objects",
            "effect_params",
            "style_stack",
            "ai_effect",
            "origin",
            "scene_3d",
            "camera_3d",
            "lighting_3d",
            "gen_3d",
        )
    } | {
        "native_cmyk": detach_native(layer.native_cmyk, memo),
        "native_display": detach_display_frame(layer.native_display),
    }


def scene_payload_changes(scene, memo):
    selection = scene.selection
    vector = scene.vector
    return {
        "canvas_state": replace(
            scene.canvas_state, background=QColor(scene.canvas_state.background)
        ),
        "selection": replace(
            selection,
            **{
                name: detach_value(getattr(selection, name), memo)
                for name in ("mask", "bounds", "preview_image", "mask_full")
            },
        ),
        "vector": replace(
            vector,
            layers=tuple(
                replace(layer, vector_objects=detach_value(layer.vector_objects, memo))
                for layer in vector.layers
            ),
            preview_object=detach_value(vector.preview_object, memo),
            selection=detach_value(vector.selection, memo),
        ),
        "selection_preview_image": detach_value(scene.selection_preview_image, memo),
        "native_selection_preview": detach_native_preview(scene.native_selection_preview, memo),
    }
