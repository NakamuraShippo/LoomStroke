"""Undoable set / clear of the CT-18 resource record in one layer's metadata.

Used by Place (attach), Relink / Reload (update hash + locator) and Embed
(drop the locator).  Pixels are untouched; see ``ReplaceLayerImageCommand``.
"""

from __future__ import annotations

import copy
from typing import Any

from .command_types import Command
from .commands_layer_lookup import find_layer
from .linked_assets.resource_layer_metadata_codec import ResourceLayerMetadataCodec
from .linked_assets.resource_record import ResourceRecord

__all__ = ["SetLayerResourceCommand"]


class SetLayerResourceCommand(Command):
    def __init__(
        self,
        layer_id: str,
        record: ResourceRecord | None,
        *,
        codec: ResourceLayerMetadataCodec | None = None,
        merge_key: str | None = None,
    ) -> None:
        super().__init__(merge_key=merge_key)
        self.layer_id = layer_id
        self.record = record
        self._codec = codec or ResourceLayerMetadataCodec()
        self._previous: dict[str, Any] | None = None

    def apply(self, canvas: Any) -> bool:
        layer = find_layer(canvas, self.layer_id)
        if layer is None:
            return False
        current = getattr(layer, "metadata", None)
        self._previous = copy.deepcopy(current) if isinstance(current, dict) else None
        if self.record is not None:
            layer.metadata = self._codec.write(current, self.record)
        else:
            layer.metadata = self._codec.strip(current)
        self._executed = True
        return True

    def revert(self, canvas: Any) -> bool:
        if not self._executed:
            return False
        layer = find_layer(canvas, self.layer_id)
        if layer is None:
            return False
        layer.metadata = copy.deepcopy(self._previous) if self._previous is not None else None
        self._executed = False
        return True
