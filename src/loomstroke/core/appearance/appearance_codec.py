"""Layer metadata namespace for fills and style stacks (``loomstroke.comictools.appearance``).

No new ``layer_type``: a fill layer is a raster layer carrying a ``FillSpec``;
styles ride on any layer's metadata.  Old readers keep the rasterized pixels.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from .fill_spec import FillSpec
from .layer_style_stack import LayerStyleStack

__all__ = ["AppearanceLayerMetadataCodec"]


class AppearanceLayerMetadataCodec:
    NAMESPACE: ClassVar[str] = "loomstroke.comictools.appearance"
    SCHEMA_VERSION: ClassVar[int] = 1

    def _payload(self, metadata: Mapping[str, Any] | None) -> dict[str, Any]:
        if not isinstance(metadata, Mapping):
            return {}
        raw = metadata.get(self.NAMESPACE)
        return dict(raw) if isinstance(raw, Mapping) else {}

    def read_fill(self, metadata: Mapping[str, Any] | None) -> FillSpec | None:
        return FillSpec.from_dict(self._payload(metadata).get("fill"))

    def read_styles(self, metadata: Mapping[str, Any] | None) -> LayerStyleStack:
        return LayerStyleStack.from_dict(self._payload(metadata).get("styles"))

    def payload_with_fill(
        self, metadata: Mapping[str, Any] | None, fill: FillSpec | None
    ) -> dict[str, Any] | None:
        payload = self._payload(metadata)
        if fill is None:
            payload.pop("fill", None)
        else:
            payload["fill"] = fill.to_dict()
        return self._finish(payload)

    def payload_with_styles(
        self, metadata: Mapping[str, Any] | None, stack: LayerStyleStack
    ) -> dict[str, Any] | None:
        payload = self._payload(metadata)
        if stack.is_empty:
            payload.pop("styles", None)
        else:
            payload["styles"] = stack.to_dict()
        return self._finish(payload)

    def _finish(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        payload.pop("schema_version", None)
        if not payload:
            return None
        payload["schema_version"] = self.SCHEMA_VERSION
        return payload
