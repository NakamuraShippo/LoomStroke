"""Create a complete generated-image layer as one undoable tree edit."""

from __future__ import annotations

from typing import Any

import numpy as np
from numpy.typing import NDArray

from .channels.channel_pixel_policy import ChannelPixelPolicy
from .commands_layer_tree import AddLayerCommand
from .layer_history_memory import estimate_retained_layer_bytes


class AddGeneratedImageLayerCommand(AddLayerCommand):
    """Reuse layer creation history and native-CMYK import without nested submits."""

    def __init__(self, image: NDArray[np.uint8], name: str, metadata: dict[str, Any]) -> None:
        super().__init__(name)
        self._pixels = np.array(image, dtype=np.uint8, copy=True)
        self._metadata = dict(metadata)
        self._native_command: Any = None
        self._native_retained_bytes = 0

    @property
    def layer_id(self) -> str | None:
        if self._native_command is not None:
            return self._native_command.layer_id
        return self._created.layer.id if self._created.layer is not None else None

    @property
    def retained_bytes(self) -> int:
        return self._pixels.nbytes + self._created.retained_bytes + self._native_retained_bytes

    def apply(self, canvas: Any) -> bool:
        # Do not splice a generation result into an unfinished brush transaction.
        if getattr(canvas, "_in_transaction", False):
            return False
        if self._native_command is not None:
            return self._apply_native(canvas)
        if not self._created.supports(canvas):
            return False
        if any(layer.native_cmyk is not None for layer in canvas._flatten_layers()):
            from .native_cmyk.image_import_command import (  # noqa: PLC0415
                NativeCmykImageImportCommand,
            )

            self._native_command = NativeCmykImageImportCommand(
                canvas, self._pixels, self._name, as_new=True
            )
            return self._apply_native(canvas)
        succeeded = self._created.create(
            canvas,
            self._name,
            "raster",
            {
                "image": ChannelPixelPolicy.image(self._pixels, straight=True),
                "metadata": dict(self._metadata),
            },
        )
        self._executed = succeeded
        return succeeded

    def _apply_native(self, canvas: Any) -> bool:
        succeeded = bool(self._native_command.apply(canvas))
        if succeeded:
            layer = canvas._get_layer_by_id(self.layer_id)
            layer.metadata = {**(layer.metadata or {}), **self._metadata}
            self._native_retained_bytes = estimate_retained_layer_bytes(layer)
        self._executed = succeeded
        return succeeded

    def revert(self, canvas: Any) -> bool:
        if not self._executed:
            return False
        succeeded = (
            self._native_command.revert(canvas)
            if self._native_command is not None
            else self._created.remove(canvas)
        )
        if succeeded:
            self._executed = False
        return bool(succeeded)
