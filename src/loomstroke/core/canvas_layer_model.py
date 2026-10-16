"""Layer model for the SimpleCanvasService facade.

The class remains re-exported from ``canvas_service_simple`` for legacy
callers and tests that import the private compatibility surface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from PySide6.QtGui import QImage

from loomstroke.core.commands import BlendMode, LayerTransform
from loomstroke.core.native_cmyk.buffer import NativeCmykBuffer


@dataclass
class _Layer:
    """Internal layer representation."""

    id: str
    name: str
    visible: bool = True
    opacity: float = 1.0
    blend_mode: BlendMode = BlendMode.NORMAL
    texture_version: int = 0
    transform_version: int = 0
    image: QImage | None = None
    native_cmyk: NativeCmykBuffer | None = None
    offset_x: int = 0
    offset_y: int = 0
    transform: LayerTransform = field(default_factory=LayerTransform.identity)
    _preview_transform: LayerTransform | None = None
    _preview_transform_version: int = 0
    locked: bool = False
    alpha_lock: bool = False  # Preserves alpha channel during paint/erase
    layer_type: str = "raster"  # "raster" | "vector" | "mask" | "group" | "effect" | "3d"
    vector_kind: str = "draw"  # "draw" (pen/path) | "shape" | "text"
    vector_objects: list[Any] = field(default_factory=list)
    # Persisted layer metadata (plugin namespaces, e.g. the CT-07 master
    # instance marker). Carried through the live canvas so a snapshot ->
    # canvas -> snapshot round trip no longer drops it.
    metadata: dict[str, Any] | None = None

    # --- Group layer ---
    children: list[_Layer] = field(default_factory=list)
    parent_id: str | None = None

    # --- Clipping mask ---
    clipping: bool = False

    # --- Layer-attached mask ---
    layer_mask: QImage | None = None
    mask_enabled: bool = True
    mask_edit_mode: bool = False

    # --- Effect layer ---
    effect_type: str | None = None  # e.g. "gaussian_blur", "levels"
    effect_params: dict[str, Any] = field(default_factory=dict)
    effect_mask: QImage | None = None  # persistent mask (grayscale)
    effect_cache: QImage | None = None
    effect_cache_version: int = 0
    effect_cache_input_ver: int = 0
    effect_cache_output_ver: int = 0
    effect_cache_extra_ver: int = 0
    effect_mask_version: int = 0
    effect_cache_mask_ver: int = 0
    effect_cache_last_used: int = 0

    # --- AI Effect Layer (Phase 2-2, post-Beta) ---
    # Populated only when ``layer_type == "ai_effect"``. Type annotation uses
    # ``Any`` to avoid importing ai_effect types at core import time
    # (keeps core → plugin dependency direction intact). Callers that need
    # the actual types should cast locally:
    #   from loomstroke.plugins.ai_effect.data_model import AIEffectState
    #   state = cast(AIEffectState | None, layer.ai_effect)
    ai_effect: Any = None
    origin: Any = None

    # --- 3D layer (format_version 1.3.0+) ---
    # Opaque at core level to avoid forcing assets_3d imports into every
    # layer construction path. Rendering/import tools can cast locally.
    scene_3d: Any = None
    camera_3d: Any = None
    lighting_3d: Any = None
    display_mode_3d: str = "solid"
    gen_3d: Any = None

    # Canonical filter names share legacy dataclass storage and constructor kwargs.

    @property
    def filter_type(self) -> str | None:
        return self.effect_type

    @filter_type.setter
    def filter_type(self, value: str | None) -> None:
        self.effect_type = value

    @property
    def filter_params(self) -> dict[str, Any]:
        return self.effect_params

    @filter_params.setter
    def filter_params(self, value: dict[str, Any]) -> None:
        self.effect_params = value

    @property
    def filter_mask(self) -> QImage | None:
        return self.effect_mask

    @filter_mask.setter
    def filter_mask(self, value: QImage | None) -> None:
        self.effect_mask = value

    @property
    def filter_cache(self) -> QImage | None:
        return self.effect_cache

    @filter_cache.setter
    def filter_cache(self, value: QImage | None) -> None:
        self.effect_cache = value

    @property
    def filter_cache_version(self) -> int:
        return self.effect_cache_version

    @filter_cache_version.setter
    def filter_cache_version(self, value: int) -> None:
        self.effect_cache_version = value

    @property
    def filter_cache_input_ver(self) -> int:
        return self.effect_cache_input_ver

    @filter_cache_input_ver.setter
    def filter_cache_input_ver(self, value: int) -> None:
        self.effect_cache_input_ver = value

    @property
    def filter_cache_output_ver(self) -> int:
        return self.effect_cache_output_ver

    @filter_cache_output_ver.setter
    def filter_cache_output_ver(self, value: int) -> None:
        self.effect_cache_output_ver = value

    @property
    def filter_cache_extra_ver(self) -> int:
        return self.effect_cache_extra_ver

    @filter_cache_extra_ver.setter
    def filter_cache_extra_ver(self, value: int) -> None:
        self.effect_cache_extra_ver = value

    @property
    def filter_mask_version(self) -> int:
        return self.effect_mask_version

    @filter_mask_version.setter
    def filter_mask_version(self, value: int) -> None:
        self.effect_mask_version = value

    @property
    def filter_cache_mask_ver(self) -> int:
        return self.effect_cache_mask_ver

    @filter_cache_mask_ver.setter
    def filter_cache_mask_ver(self, value: int) -> None:
        self.effect_cache_mask_ver = value

    @property
    def filter_cache_last_used(self) -> int:
        return self.effect_cache_last_used

    @filter_cache_last_used.setter
    def filter_cache_last_used(self, value: int) -> None:
        self.effect_cache_last_used = value
