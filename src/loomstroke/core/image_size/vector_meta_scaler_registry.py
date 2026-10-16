"""Registry of metadata scalers applied to vector objects on Image Size.

Core ships the text and parametric-shape scalers; plugins that store page
geometry in their own ``meta`` namespace (panel frames, balloons, …) can
register an additional scaler through :meth:`VectorMetaScalerRegistry.shared`
without core learning their schema (CT-13 spec §10.3).
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterable, Mapping
from typing import Any, ClassVar

from .parametric_meta_scaler import ParametricMetaScaler
from .text_meta_scaler import TextMetaScaler

MetaScaler = Callable[[Mapping[str, Any], float, float], "dict[str, Any] | None"]


class VectorMetaScalerRegistry:
    """Ordered collection of ``(meta, sx, sy) -> new_meta | None`` callables."""

    _shared: ClassVar[VectorMetaScalerRegistry | None] = None

    def __init__(self, scalers: Iterable[MetaScaler] = ()) -> None:
        self._scalers: list[MetaScaler] = list(scalers)
        self._resamplers: dict[Any, set[object]] = {}

    def register_resampler(self, callback: Any, *, owner: object) -> None:
        """Register a layer/object metadata hook which also needs the image kernel."""
        self._resamplers.setdefault(callback, set()).add(owner)

    def unregister_resampler(self, callback: Any, *, owner: object) -> None:
        owners = self._resamplers.get(callback)
        if owners is not None:
            owners.discard(owner)
            if not owners:
                del self._resamplers[callback]

    def resample_meta(self, meta: Any, sx: float, sy: float, mode: Any) -> dict[str, Any] | None:
        if not self._resamplers or not isinstance(meta, Mapping) or not meta:
            return None
        current = copy.deepcopy(dict(meta))
        changed = False
        for callback in tuple(self._resamplers):
            result = callback(current, sx, sy, mode)
            if result is not None:
                current = result
                changed = True
        return current if changed else None

    @classmethod
    def default(cls) -> VectorMetaScalerRegistry:
        return cls([TextMetaScaler().scale, ParametricMetaScaler().scale])

    @classmethod
    def shared(cls) -> VectorMetaScalerRegistry:
        """Process-wide registry used by the canvas service; plugins extend it."""
        if cls._shared is None:
            cls._shared = cls.default()
        return cls._shared

    def register(self, scaler: MetaScaler) -> None:
        if scaler not in self._scalers:
            self._scalers.append(scaler)

    def unregister(self, scaler: MetaScaler) -> None:
        if scaler in self._scalers:
            self._scalers.remove(scaler)

    def scalers(self) -> tuple[MetaScaler, ...]:
        return tuple(self._scalers)

    def scale_meta(self, meta: Any, sx: float, sy: float) -> dict[str, Any] | None:
        """Run every scaler; ``None`` when nothing recognised the metadata."""
        if not isinstance(meta, Mapping):
            return None
        current: dict[str, Any] | None = None
        for scaler in list(self._scalers):
            result = scaler(current if current is not None else meta, sx, sy)
            if result is not None:
                current = result
        return current
