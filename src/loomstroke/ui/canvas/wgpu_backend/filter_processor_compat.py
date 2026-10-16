"""Legacy processor hooks delegate to the canonical image-filter helpers."""

from __future__ import annotations

from typing import Any


def filter_method(instance: Any, legacy: str, canonical: str, original: Any) -> Any:
    """Honor existing method overrides, otherwise use the canonical helper."""
    method = getattr(instance, legacy)
    if getattr(method, "__func__", None) is original:
        return getattr(instance, canonical)
    return method


class FilterProcessorLegacyAliases:
    """Compatibility surface only; image-filter implementations live elsewhere."""

    def _legacy_filter_call(self, name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        for old, new in (("effect_type", "filter_type"), ("effect_result", "filter_result")):
            if old in kwargs:
                kwargs[new] = kwargs.pop(old)
        return getattr(self, name)(*args, **kwargs)

    def apply_layer_effect(self, *args: Any, **kwargs: Any) -> Any:
        return self._apply_layer_filter(*args, **kwargs)

    def _dispatch_effect(self, *args: Any, **kwargs: Any) -> Any:
        return self._legacy_filter_call("_dispatch_filter", args, kwargs)

    def _apply_fragment_effect(self, *args: Any, **kwargs: Any) -> Any:
        return self._legacy_filter_call("_apply_fragment_filter", args, kwargs)

    def _apply_effect_mask_if_needed(self, *args: Any, **kwargs: Any) -> Any:
        return self._legacy_filter_call("_apply_filter_mask_if_needed", args, kwargs)

    def _apply_effect_opacity_if_needed(self, *args: Any, **kwargs: Any) -> Any:
        return self._legacy_filter_call("_apply_filter_opacity_if_needed", args, kwargs)

    def _apply_plugin_wgsl_effect(self, *args: Any, **kwargs: Any) -> Any:
        return self._legacy_filter_call("_apply_plugin_wgsl_filter", args, kwargs)

    def _params_for_effect(self, *args: Any, **kwargs: Any) -> Any:
        return self._legacy_filter_call("_params_for_filter", args, kwargs)

    def _lut_for_effect(self, *args: Any, **kwargs: Any) -> Any:
        return self._legacy_filter_call("_lut_for_filter", args, kwargs)

    def _effect_mask_texture(self, *args: Any, **kwargs: Any) -> Any:
        return self._filter_mask_texture(*args, **kwargs)

    @classmethod
    def _effect_mask_version(cls, layer: Any) -> int:
        return cls._filter_mask_version(layer)

    @property
    def _effect_sampler(self) -> Any:
        return self._filter_sampler

    @_effect_sampler.setter
    def _effect_sampler(self, value: Any) -> None:
        self._filter_sampler = value

    @property
    def _effect_pipeline(self) -> Any:
        return self._filter_pipeline

    @_effect_pipeline.setter
    def _effect_pipeline(self, value: Any) -> None:
        self._filter_pipeline = value
