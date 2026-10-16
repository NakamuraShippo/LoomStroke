"""Non-destructive appearance (CT-21): fill specs, layer style stacks, adjustment registry.

Pure data + numpy rasterizers, one class per module.  Layer metadata namespace
``loomstroke.comictools.appearance``; no new ``layer_type`` (old readers keep pixels).
"""

from __future__ import annotations

from .appearance_codec import AppearanceLayerMetadataCodec
from .appearance_issue import AppearanceValidationIssue
from .appearance_kind import AppearanceFamily, AppearanceKind
from .appearance_param import AppearanceParam, ParamKind
from .appearance_registry import AppearanceRegistry
from .fill_renderer import FillRenderer
from .fill_spec import FillSpec
from .layer_style_rasterizer import LayerStyleRasterizer
from .layer_style_spec import LayerStyleSpec
from .layer_style_stack import LayerStyleStack

__all__ = [
    "AppearanceFamily",
    "AppearanceKind",
    "AppearanceLayerMetadataCodec",
    "AppearanceParam",
    "AppearanceRegistry",
    "AppearanceValidationIssue",
    "FillRenderer",
    "FillSpec",
    "LayerStyleRasterizer",
    "LayerStyleSpec",
    "LayerStyleStack",
    "ParamKind",
]
