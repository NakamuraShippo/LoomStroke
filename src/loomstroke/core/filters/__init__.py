"""Non-destructive filter layer framework.

Provides ``BaseFilter``, ``FilterRegistry``, and built-in filters
(Gaussian Blur, Levels).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

from PySide6.QtGui import QImage


# ------------------------------------------------------------------
# Parameter schema
# ------------------------------------------------------------------
@dataclass(slots=True)
class FilterParamSchema:
    """Describes a single adjustable parameter for a filter."""

    key: str
    label: str
    param_type: str  # "float" | "int" | "bool" | "enum"
    default: Any
    min_val: Any = None
    max_val: Any = None
    step: Any = None
    choices: list[str] | None = None


@dataclass(slots=True)
class SamplerUniform:
    """Wrapper for ``sampler2D`` uniform values in plugin GPU shaders.

    Use this in :meth:`BaseFilter.gpu_uniforms` return values to bind
    an existing GL texture to a sampler uniform::

        def gpu_uniforms(self, params):
            return {"u_lut": SamplerUniform(self._lut_tex_id)}

    Texture units 0 and 1 are reserved (``u_src`` and ``u_mask``).
    Plugin samplers are automatically assigned to units 2+.
    """

    texture_id: int


# ------------------------------------------------------------------
# Base class
# ------------------------------------------------------------------
class BaseFilter(ABC):
    """Abstract base for all live image filters."""

    @abstractmethod
    def name(self) -> str:
        """Human-readable filter name."""

    @abstractmethod
    def category(self) -> str:
        """Category grouping (e.g. "blur", "adjustment")."""

    @abstractmethod
    def param_schema(self) -> list[FilterParamSchema]:
        """Return the list of adjustable parameters."""

    @abstractmethod
    def apply(
        self,
        source: QImage,
        params: dict[str, Any],
        mask: QImage | None = None,
    ) -> QImage:
        """Apply the filter to *source* and return the result.

        *mask* is an optional grayscale QImage that limits the affected
        region (white = full filter, black = no filter).
        """

    def gpu_shader_source(self) -> str | None:
        """Return legacy GLSL fragment shader source.

        This compatibility hook is retired from the production render path.
        New GPU plugin filters should implement :meth:`wgsl_shader_source`.
        """
        return None

    def gpu_uniforms(self, params: dict[str, Any]) -> dict[str, Any]:
        """Convert filter parameters to legacy GLSL uniform values.

        This is retained for source compatibility; wgpu production rendering
        uses :meth:`wgpu_uniforms`.
        """
        return {}

    def wgsl_shader_source(self) -> str | None:
        """Return WGSL source for wgpu-accelerated processing.

        The source must define ``apply_plugin(src, uv, params)`` using the
        ``PluginParams`` struct documented in the SDK guide. Returning
        ``None`` keeps the filter on the built-in or CPU compatibility path.
        """
        return None

    def wgpu_uniforms(self, params: dict[str, Any]) -> dict[str, Any]:
        """Convert filter parameters to wgpu uniform values.

        The initial WGSL plugin ABI accepts scalar float slots named ``p0`` to
        ``p7``. Existing plugins do not need to implement this method.
        """
        return {}

    def cache_extra_version(self, params: dict[str, Any]) -> int:
        """Return version for external cache dependencies.

        Override if the filter depends on external state (e.g. other layers,
        time-based animation). Return a different value when the external
        state changes to invalidate the cache.

        Default returns 0 (no external dependencies).
        """
        return 0


# ------------------------------------------------------------------
# Registry
# ------------------------------------------------------------------
class _FilterRegistryAliases(type):
    def __setattr__(cls, name: str, value: Any) -> None:
        if name in {"_filters", "_effects"}:
            # Both names remain in the class dictionary so monkeypatch can
            # restore either spelling, including nested old/new patches.
            super().__setattr__("_filters", value)
            super().__setattr__("_effects", value)
            return
        super().__setattr__(name, value)


class FilterRegistry(metaclass=_FilterRegistryAliases):
    """Global registry mapping stable filter IDs to implementations."""

    _filters: ClassVar[dict[str, type[BaseFilter]]] = {}
    _effects: ClassVar[dict[str, type[BaseFilter]]] = _filters

    @classmethod
    def register(
        cls,
        filter_type: str | None = None,
        filter_class: type[BaseFilter] | None = None,
        *,
        effect_type: str | None = None,
        effect_class: type[BaseFilter] | None = None,
    ) -> None:
        """Register a filter; old keyword names remain source compatible."""
        if effect_type is not None:
            if filter_type is not None and filter_type != effect_type:
                raise TypeError("Conflicting filter_type and effect_type")
            filter_type = effect_type
        if effect_class is not None:
            if filter_class is not None and filter_class is not effect_class:
                raise TypeError("Conflicting filter_class and effect_class")
            filter_class = effect_class
        if filter_type is None or filter_class is None:
            raise TypeError("register() requires filter_type and filter_class")
        cls._filters[filter_type] = filter_class

    @classmethod
    def get(
        cls, filter_type: str | None = None, *, effect_type: str | None = None
    ) -> type[BaseFilter] | None:
        if effect_type is not None:
            if filter_type is not None and filter_type != effect_type:
                raise TypeError("Conflicting filter_type and effect_type")
            filter_type = effect_type
        return cls._filters.get(filter_type)

    @classmethod
    def list_filters(cls) -> list[tuple[str, str, str]]:
        """Return ``[(type_id, name, category), ...]``."""
        result: list[tuple[str, str, str]] = []
        for type_id, klass in cls._filters.items():
            inst = klass()
            result.append((type_id, inst.name(), inst.category()))
        return result

    @classmethod
    def list_effects(cls) -> list[tuple[str, str, str]]:
        """Legacy query alias using the same registry."""
        return cls.list_filters()


# ------------------------------------------------------------------
# Register built-in filters on import
# ------------------------------------------------------------------
from .brightness_contrast import BrightnessContrastFilter  # noqa: E402
from .curves import CurvesFilter  # noqa: E402
from .gaussian_blur import GaussianBlurFilter  # noqa: E402
from .gradient_map import GradientMapFilter  # noqa: E402
from .hue_saturation import HueSaturationFilter  # noqa: E402
from .invert import InvertFilter  # noqa: E402
from .levels import LevelsFilter  # noqa: E402
from .mosaic import MosaicFilter  # noqa: E402
from .noise_reduction import NoiseReductionFilter  # noqa: E402
from .screentone import MangaScreentoneFilter  # noqa: E402
from .sharpen import SharpenFilter  # noqa: E402

FilterRegistry.register("gaussian_blur", GaussianBlurFilter)
FilterRegistry.register("levels", LevelsFilter)
FilterRegistry.register("sharpen", SharpenFilter)
FilterRegistry.register("noise_reduction", NoiseReductionFilter)
FilterRegistry.register("hue_saturation", HueSaturationFilter)
FilterRegistry.register("invert", InvertFilter)
FilterRegistry.register("brightness_contrast", BrightnessContrastFilter)
FilterRegistry.register("curves", CurvesFilter)
FilterRegistry.register("gradient_map", GradientMapFilter)
FilterRegistry.register("mosaic", MosaicFilter)
FilterRegistry.register("manga_screentone", MangaScreentoneFilter)

__all__ = [
    "FILTER_LAYER_TYPE",
    "BaseFilter",
    "BrightnessContrastFilter",
    "CurvesFilter",
    "FilterParamSchema",
    "FilterRegistry",
    "GaussianBlurFilter",
    "GradientMapFilter",
    "HueSaturationFilter",
    "InvertFilter",
    "LevelsFilter",
    "MangaScreentoneFilter",
    "MosaicFilter",
    "NoiseReductionFilter",
    "SamplerUniform",
    "SharpenFilter",
]

# The persisted discriminator is part of the existing .lstroke/plugin contract.
FILTER_LAYER_TYPE = "effect"
