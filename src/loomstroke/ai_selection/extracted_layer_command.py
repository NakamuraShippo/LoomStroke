"""Lazy-created SAM extraction command with explicit ownership of one result layer."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from loomstroke.core.command_layer_access import _resolve_layer_by_id
from loomstroke.core.commands import AddLayerCommand

logger = logging.getLogger(__name__)


class _Sam3ExtractToNewLayerCommand(AddLayerCommand):
    """Keep completed RGB pixels; never retain or rewrite the source image."""

    def __init__(
        self,
        name: str,
        source_layer_id: str,
        mask_image: Any,
        mode: str,
        *,
        image_builder: Callable[..., Any],
        legacy_apply: Callable[..., str | None],
    ) -> None:
        super().__init__(name, layer_type="raster")
        self._source_layer_id = source_layer_id
        self._mask_image = mask_image
        self._mode = mode
        self._image_builder = image_builder
        self._legacy_apply = legacy_apply
        self._created_layer_id: str | None = None

    @property
    def created_layer_id(self) -> str | None:
        return self._created_layer_id

    @property
    def retained_bytes(self) -> int:
        mask_bytes = self._mask_image.sizeInBytes() if self._mask_image is not None else 0
        return super().retained_bytes + mask_bytes

    def apply(self, canvas: Any) -> bool:
        if self._created.layer is not None or self._created.supports(canvas):
            return self._apply_owned(canvas)
        # Lightweight adapters keep their existing add/remove contract. Their
        # repeat extraction still needs the mask, so do not release it here.
        identifier = self._legacy_apply(
            canvas,
            source_layer_id=self._source_layer_id,
            mask_image=self._mask_image,
            mode=self._mode,
            layer_name=self._name,
        )
        if identifier is None:
            return False
        self._layer_id = self._created_layer_id = identifier
        self._executed = True
        return True

    def _apply_owned(self, canvas: Any) -> bool:
        try:
            if self._created.layer is None:
                source = _resolve_layer_by_id(canvas, self._source_layer_id)
                if source is None or self._mask_image is None:
                    return False
                image = self._image_builder(source, self._mask_image, mode=self._mode)
                if image is None:
                    return False
                success = self._created.create(
                    canvas,
                    self._name,
                    "raster",
                    {
                        "image": image,
                        "offset_x": int(getattr(source, "offset_x", 0)),
                        "offset_y": int(getattr(source, "offset_y", 0)),
                        "texture_version": 1,
                    },
                )
            else:
                success = self._created.restore(canvas)
            # Even a notification failure may leave a detached, prepared layer
            # in custody. A factory failure does not: keep the mask for retry.
            if self._created.layer is not None:
                self._mask_image = None
            if success:
                self._layer_id = self._created_layer_id = self._created.layer.id
                self._executed = True
            return success
        except Exception as exc:
            logger.warning("SAM layer extraction failed (%s)", type(exc).__name__)
            return False

    def revert(self, canvas: Any) -> bool:
        if self._created_layer_id is None:
            return False
        if self._created.layer is not None:
            success = self._created.remove(canvas)
        else:
            remove = getattr(canvas, "remove_layer", None)
            success = bool(remove(self._created_layer_id)) if callable(remove) else False
        if success:
            # Preserve the existing visible ID contract; custody keeps the ID
            # and payload required by Redo and inverse history compensation.
            self._layer_id = self._created_layer_id = None
            self._executed = False
        return success
